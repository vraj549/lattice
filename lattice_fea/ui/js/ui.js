// Tree + context-panel rendering. Pure DOM, no framework.
import { fmtVal, contourStyle } from "./colormap.js";
import { frfChart, frfPlot, findPeaks, seriesColor } from "./charts.js";
import { defaultReferenceFace, describeFace, candidateTargets } from "./pattern.js";
import { transmissibility } from "./dynamics.js";

export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "html") n.innerHTML = v;
    // boolean attributes (disabled, hidden, checked, …) disable/enable by
    // PRESENCE — setAttribute(k, false) would render disabled="false" and
    // still apply. Only set them when actually true.
    else if (v === false) continue;
    else if (v === true) n.setAttribute(k, "");
    else if (v !== null && v !== undefined) n.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c == null) continue;
    n.append(c.nodeType ? c : document.createTextNode(c));
  }
  return n;
}

const uid = () => Math.random().toString(36).slice(2, 8);

// ============================================================ tree

// The tree itself lives in tree.js. What stays here is the shared logic it
// asks about a model — what an analysis needs, what results it produced, and
// whether the mesh still matches — so the tree, the panels and the run
// blockers can never give three different answers to the same question.


/** Does this analysis take applied loads?
 *
 *  Modal has none by definition; random is driven entirely by its input
 *  spectrum; a base-driven harmonic is driven through its supports. Offering
 *  a Loads branch in those cases invites the user to define something the
 *  solver will silently ignore. */
export function needsLoads(a) {
  if (a.type === "static") return true;
  if (a.type === "harmonic") return (a.config?.excitation || "force") !== "base";
  return false;
}

export function excitationMeta(a) {
  const c = a.config || {};
  const d = c.base_dir || [0, 0, 1];
  const ax = ["X", "Y", "Z"][d.map(Math.abs).indexOf(Math.max(...d.map(Math.abs)))] || "Z";
  if (a.type === "random") return `${gramsOf(c.spec || [])} g RMS · ${ax}`;
  if (a.type === "shock") {
    const axis = ["X", "Y", "Z"][c.axis ?? 2];
    return (c.input || "spectrum") === "pulse"
      ? `${fmtVal(c.pulse_g ?? 20)} g / ${fmtVal(c.pulse_ms ?? 11)} ms · ${axis}`
      : `SRS ${fmtVal((c.spec || []).reduce((m, r) => Math.max(m, r[1]), 0))} g · ${axis}`;
  }
  return `${fmtVal(c.base_g ?? 1)} g · ${ax}`;
}

/** Status of one analysis, as a dot + optional badge.
 *
 *  The badge is the answer to "can I trust what I am looking at" — the one
 *  question a results tree has to answer without being asked. */
export function analysisStatus(S, a) {
  const st = S.runStatus[a.id];
  const res = S.results[a.id];
  if (st === "running") {
    return { dot: "run", badge: null, title: "Running…" };
  }
  // Failure is checked BEFORE the presence of results. A run that died after
  // writing some of its output leaves a payload behind, and the ✓ branch
  // claimed it — so the one badge that answers "can I trust this" said yes
  // about a fragment of a run that had aborted.
  if (st === "failed" || res?.failed) {
    const why = res?.recovered
      ? "The last run failed. What it left behind is a fragment, not a result."
      : "The last run failed and produced nothing.";
    return { dot: "bad", title: why,
      badge: { text: "\u2715", cls: "bad", title: why } };
  }
  if (res && res.stale) {
    return { dot: "warn", title: "Results are out of date",
      badge: { text: "!", cls: "stale",
        title: "Out of date: the mesh, materials, connections or boundary "
             + "conditions changed after this ran." } };
  }
  if (res) {
    return { dot: "ok", title: "Results are current for this model",
      badge: { text: "✓", cls: "ok", title: "Results are current for this model" } };
  }
  return { dot: "idle", badge: null, title: "Not run yet" };
}

/** The result items that actually exist for this analysis, in reading order. */
export function solutionItems(S, a) {
  const meta = S.results[a.id];
  if (!meta) return [];
  const out = [];
  const fields = (meta.fields || []).filter((f) => f.part !== "I");
  if (fields.length) {
    out.push({ what: "contours", label: "Contours", meta: plural(fields.length, "field") });
  }
  const modes = meta.tables?.modes?.[0];
  if (modes && a.type !== "static") {
    out.push({ what: "modes", label: "Modes", meta: String(modes.rows.length) });
  }
  if (meta.frf?.length) {
    out.push({ what: "frf", label: "Frequency response", meta: plural(meta.frf.length, "curve") });
  }
  if (a.type === "random") {
    out.push({ what: "random", label: "Random response" });
  }
  if (a.type === "shock") {
    out.push({ what: "shock", label: "Shock response" });
  }
  if (a.type === "static" && meta.tables?.contact_check?.length) {
    out.push({ what: "slip", label: "Slip check" });
  }
  if (meta.tables?.bolt_forces?.length) {
    // the meta column is for a count worth scanning; a unit ("N") or a
    // restated label ("preload") is noise in it
    out.push({ what: "bolts", label: "Bolt forces" });
    out.push({ what: "sizing", label: "Bolt sizing" });
  }
  if (a.type === "static" && reactionRow(meta)) {
    out.push({ what: "reactions", label: "Reactions" });
  }
  // A failed run often has no warnings at all — the failure IS the message —
  // so the node has to exist for the error too, or there is nowhere to read it.
  if (meta.warnings?.length || meta.error) {
    out.push({ what: "warnings", label: "Solver messages",
               meta: meta.error ? "failed" : String(meta.warnings.length) });
  }
  return out;
}

/** Analyses whose mesh groups are missing from the current mesh. Shared by
 *  the tree badge, the Mesh panel and the run blockers so all three agree. */
export function staleForAnalyses(S) {
  const stats = S.meshData?.stats;
  const have = new Set(stats?.face_groups || []);
  if (!have.size) return [];
  const out = [];
  (S.project?.setup?.analyses || []).forEach((a, ai0) => {
    const ai = ai0 + 1;
    const missing = requiredGroups(a, ai).filter((g) => !have.has(g));
    if (missing.length) out.push({ analysis: a, missing });
  });
  return out;
}

// The server owns this number; it is sent in /api/config. The UI used to
// keep its own copy and it drifted — the writer moved to 3 while the browser
// still compared against 2, so a mesh that genuinely needed rewriting was
// reported as current. The literal here is only a floor for the moment before
// the config arrives.
export const MESH_FORMAT = 3;
const meshFormat = (S) => S.config?.mesh_format ?? MESH_FORMAT;

/**
 * A derived result — slip, shock, random, bolt sizing — that still belongs to
 * the run currently loaded.
 *
 * These are computed from one run and are valid only for THAT run. Keyed by
 * analysis id alone, a re-run replaced the numbers the contour panel read
 * while these kept the previous ones: the staleness badge went green and the
 * shock table underneath it still showed the old model's bolt loads. The
 * server already publishes a signature for exactly this purpose — every input
 * that changes the answer, hashed — so a stale entry simply cannot be read.
 */
export function derived(S, name, aid) {
  const hit = S[name]?.[aid];
  if (!hit) return null;
  const sig = S.results?.[aid]?.signature;
  return hit.sig === sig ? hit.data : null;
}

/**
 * Everything about the current mesh that no longer matches the model.
 *
 * Boundary conditions become mesh groups, and bolts and probes become actual
 * elements and nodes, so all three are baked in at mesh time. Patterning a
 * bolt across a flange is the fastest way to get five joints that exist in
 * the tree and in none of the matrices — the load path would silently not be
 * there, which is worse than a failed run.
 */
export function meshIssues(S) {
  const stats = S.meshData?.stats;
  if (!stats) return [];
  const out = [];

  const goneNow = JSON.stringify(
    [...(S.project.setup.suppressed_solids || [])].map(Number).sort((a, b) => a - b));
  const goneThen = JSON.stringify((stats.suppressed_solids || []).map(Number));
  if (stats.suppressed_solids !== undefined && goneNow !== goneThen) {
    out.push({ scope: "all",
      text: "a body has been removed from or restored to the analysis since "
          + "this mesh was built — re-mesh before running" });
  }

  // A mesh from an older build may be unusable rather than merely stale.
  if ((stats.mesh_format || 0) < meshFormat(S)) {
    out.push({ scope: "all",
      text: "this mesh was written by an earlier version of Lattice whose "
          + "group records could confuse code_aster (duplicate GROUP_NO). "
          + "Re-mesh before running" });
  }
  for (const s of staleForAnalyses(S)) {
    out.push({ scope: "all",
      text: `boundary conditions changed for “${s.analysis.name || s.analysis.type}” ` +
            `(missing ${s.missing.join(", ")})` });
  }

  const setup = S.project?.setup || {};
  const meshed = new Set((stats.bolts || []).map((b) => b.id));
  const live = (setup.bolts || []).filter(
    (b) => b.side_a_faces?.length && b.side_b_faces?.length);
  const added = live.filter((b) => !meshed.has(b.id)).length;
  const gone = [...meshed].filter((id) => !live.some((b) => b.id === id)).length;
  if (added) {
    out.push({ scope: "all",
      text: `${added} bolt${added === 1 ? "" : "s"} added since meshing — ` +
            `${added === 1 ? "its beam is" : "their beams are"} not in the model yet` });
  }
  if (gone) {
    out.push({ scope: "all",
      text: `${gone} bolt${gone === 1 ? "" : "s"} removed since meshing — ` +
            "the mesh still carries the old beams" });
  }

  const meshProbes = new Set((stats.probes || []).map((p) => p.id));
  const newProbes = (setup.probes || []).filter((p) => !meshProbes.has(p.id)).length;
  if (newProbes) {
    out.push({ scope: "vibration",
      text: `${newProbes} probe${newProbes === 1 ? "" : "s"} added since meshing — ` +
            "response cannot be extracted there until you re-mesh" });
  }
  return out;
}

/** Mesh group names this analysis will reference — must match meshing.py. */
export function requiredGroups(a, ai) {
  const need = [];
  (a.supports || []).forEach((x, i) => { if (x.faces?.length) need.push(`SUP${ai}_${i + 1}`); });
  if (needsLoads(a)) {
    (a.loads || []).forEach((x, i) => {
      if (["force", "pressure", "remote"].includes(x.type) && x.faces?.length) {
        need.push(`LOA${ai}_${i + 1}`);
      }
    });
  }
  return need;
}

export function loadMeta(l) {
  if (l.type === "gravity") return "gravity";
  if (l.type === "rotation") return `${fmtVal(l.rpm || 0)} rpm`;
  if (l.type === "pressure") return `${fmtVal(l.pressure || 0)} MPa`;
  const f = Math.hypot(l.fx || 0, l.fy || 0, l.fz || 0);
  const m = Math.hypot(l.mx || 0, l.my || 0, l.mz || 0);
  if (l.type === "remote") return m > 0 && f === 0 ? `${fmtVal(m)} N·mm` : `${fmtVal(f)} N remote`;
  return `${fmtVal(f)} N`;
}

// ============================================================ panels

export function renderPanel(S, A) {
  const panel = document.getElementById("panel");
  const title = document.getElementById("ctxTitle");
  const meta = document.getElementById("ctxMeta");
  panel.innerHTML = "";
  const { kind, id } = S.selection;
  const setup = S.project?.setup;
  if (!setup) { title.textContent = "Model"; meta.textContent = ""; return; }

  const put = (t, m, ...secs) => {
    title.textContent = t; meta.textContent = m || "";
    panel.append(...secs.filter(Boolean));
  };

  // A throw in one panel used to blank the entire sidebar with no message —
  // that is how a stale reference after the per-analysis refactor presented.
  try {
    return renderPanelBody(S, A, put, kind, id);
  } catch (e) {
    console.error("panel render failed:", e);
    title.textContent = "Panel error";
    meta.textContent = "";
    panel.append(
      el("div", { class: "sec" },
        el("div", { class: "hint bad" }, `⚠ ${e.message}`),
        el("div", { class: "hint" },
          "This is a bug in Lattice, not your model. The rest of the app still " +
          "works — the browser console has the stack trace."),
        el("div", { class: "btnrow" },
          el("button", { class: "btn", onclick: () => A.select("model", "root") },
            "Back to model"))));
  }
}

function renderPanelBody(S, A, put, kind, id) {
  switch (kind) {
    case "solid": return panelSolid(S, A, put, id);
    case "interfaces": return panelBondedInterfaces(S, A, put);
    case "connections": return panelConnectionsGroup(S, A, put);
    case "geometry": return panelGeometry(S, A, put);
    case "probes": return panelProbes(S, A, put);
    case "support": return panelSupport(S, A, put, id);
    case "load": return panelLoad(S, A, put, id);
    case "bolt": return panelBolt(S, A, put, id);
    case "tie": return panelTie(S, A, put, id);
    case "contact": return panelContact(S, A, put, id);
    case "probe": return panelProbe(S, A, put, id);
    case "mesh": return panelMesh(S, A, put);
    case "analysis": return panelAnalysis(S, A, put, id);
    case "settings": return panelSettings(S, A, put, id);
    case "excitation": return panelExcitation(S, A, put, id);
    case "solution": return panelSolution(S, A, put, id);
    case "result": return panelResult(S, A, put, id);
    default: return panelModel(S, A, put);
  }
}

/** Contact area of an interface: the smaller of its two sides. */
export function interfaceArea(S, c) {
  const byTag = new Map((S.project?.geometry?.faces || []).map((f) => [f.tag, f.area || 0]));
  const side = (list) => (list || []).reduce((t, x) => t + (byTag.get(x) || 0), 0);
  const a = side(c.faces_a), b = side(c.faces_b);
  return a && b ? Math.min(a, b) : (a || b);
}

/** "1 face", "2 faces" — never "1 face(s)", which is how a tool announces
 *  that nobody read its own output. */
export function plural(n, one, many = null) {
  return `${n.toLocaleString()} ${n === 1 ? one : (many || one + "s")}`;
}

/**
 * Groups of bodies that nothing joins at solve time.
 *
 * The mesh reports how many pieces it is in, and a model built from separate
 * parts is MEANT to be in pieces: the contacts, ties and bolts that join them
 * are applied by the solver, not by shared nodes. Warning on the raw island
 * count told every correctly built contact assembly that it would "produce
 * singular static solves". Joined here through every connection the solver
 * will apply, so what remains is a real gap.
 */
export function unjoinedGroups(S) {
  const setup = S.project.setup;
  const gone = new Set((setup.suppressed_solids || []).map(Number));
  const solids = (S.project.geometry.solids || [])
    .map((x) => Number(x.tag)).filter((t) => !gone.has(t));
  const parent = new Map(solids.map((t) => [t, t]));
  const find = (t) => { while (parent.get(t) !== t) t = parent.get(t); return t; };
  const join = (a, b) => parent.set(find(a), find(b));
  const faceSolids = new Map((S.project.geometry.faces || [])
    .map((f) => [f.tag, (f.solids || []).map(Number)]));
  const solidsOf = (faces) => (faces || []).flatMap((f) => faceSolids.get(f) || []);
  const joinAll = (list) => {
    const live = list.filter((t) => parent.has(t));   // a removed body joins nothing
    live.forEach((t) => join(live[0], t));
  };

  // faces shared by two solids: a bonded import already merged these
  for (const ss of faceSolids.values()) joinAll(ss);
  for (const c of setup.contacts || []) {
    if (c.suppressed || !(c.faces_a?.length && c.faces_b?.length)) continue;
    joinAll([...solidsOf(c.faces_a), ...solidsOf(c.faces_b)]);
  }
  for (const t of setup.ties || []) {
    if (t.slave_faces?.length && t.master_solid != null) {
      joinAll([Number(t.master_solid), ...solidsOf(t.slave_faces)]);
    }
  }
  for (const b of setup.bolts || []) {
    if (b.side_a_faces?.length && b.side_b_faces?.length) {
      joinAll([...solidsOf(b.side_a_faces), ...solidsOf(b.side_b_faces)]);
    }
  }
  const groups = new Map();
  for (const t of solids) {
    const r = find(t);
    groups.set(r, [...(groups.get(r) || []), t]);
  }
  return [...groups.values()];
}

/**
 * What a solid is called.
 *
 * The name lives in `setup.solid_names`, not on the geometry: geometry is
 * re-derived from the STEP on every import, and `setup` is where the user's
 * own data belongs — it already keys material assignments by the same tag.
 * A name set here survives a reload, and a re-import keeps it as long as the
 * tag does.
 */
export function solidName(S, tag) {
  const t = String(tag);
  const named = S.project?.setup?.solid_names?.[t];
  if (named) return named;
  const sd = (S.project?.geometry?.solids || []).find((x) => String(x.tag) === t);
  return sd?.name || `Solid ${t}`;
}

/**
 * A panel section.
 *
 * If it contains explanatory prose, the heading grows a "?" that folds it
 * away. Explanations are read once and then re-read forever against the
 * reader's will, because they sit above the numbers and push them down the
 * panel. They are worth keeping — someone meeting a feature for the first
 * time needs them — but not worth the permanent cost, so they are off by
 * default and the choice is remembered.
 *
 * Warnings are never folded. Those are state, not teaching.
 */
const sec = (label, ...kids) => {
  const body = kids.filter(Boolean);
  const neutralHint = (k) => k?.classList?.contains?.("hint")
    && !k.classList.contains("warn") && !k.classList.contains("bad")
    && !k.classList.contains("good");
  const explains = body.some(neutralHint);
  // A section whose entire content is explanatory prose is hidden with that
  // prose. It used to leave its heading and its "?" behind with nothing under
  // them, which is how a panel comes to look unfinished — the Workflow
  // section on the model panel was a heading and a question mark.
  const onlyExplains = body.length > 0 && body.every(neutralHint);
  return el("div", { class: `sec${onlyExplains ? " sec-help" : ""}` },
    label || explains
      ? el("span", { class: "lbl" },
          label || "",
          explains ? el("button", {
            class: "helpq", title: "Explain this section",
            "aria-expanded": String(document.body.classList.contains("show-help")),
            onclick: (e) => {
              e.stopPropagation();
              const s = e.currentTarget.closest(".sec");
              s.classList.toggle("show-help");
              e.currentTarget.setAttribute("aria-expanded",
                String(s.classList.contains("show-help")));
            },
          }, "?") : null)
      : null,
    ...body);
};

const dl = (rows) => el("dl", {}, rows.map(([k, v]) =>
  el("div", { class: "fld" }, el("dt", {}, k), el("dd", {}, String(v)))));

/**
 * Text and number fields edit the model WITHOUT re-rendering the panel.
 *
 * Every edit used to go through A.mutate, which rebuilds this panel — so the
 * input element you were typing into was destroyed after the first keystroke
 * and focus fell back to <body>. Entering "4500" meant clicking the field
 * four times. The tree and viewport still update live; only the panel holds
 * still, and it re-renders on `change` (blur or Enter) once you are done.
 */
function liveInput(attrs, emit) {
  return el("input", {
    ...attrs,
    oninput: (e) => { withPanelFrozen(() => emit(e.target)); },
    onchange: () => { thawPanel(); },
  });
}

let _panelFrozen = 0;
export const panelIsFrozen = () => _panelFrozen > 0;
function withPanelFrozen(fn) {
  _panelFrozen++;
  try { fn(); } finally { _panelFrozen--; }
}
let _thaw = () => {};
export function setPanelThaw(fn) { _thaw = fn; }
function thawPanel() { _thaw(); }

function numInput(label, value, oninput, attrs = {}) {
  return el("label", { class: "frm" }, label,
    liveInput({ type: "number", value: value ?? "", step: "any", ...attrs },
      (t) => oninput(t.value === "" ? null : Number(t.value))));
}
function textInput(label, value, oninput) {
  return el("label", { class: "frm" }, label,
    liveInput({ type: "text", value: value ?? "" }, (t) => oninput(t.value)));
}
/** One cell of a spectrum table.
 *
 * These were raw inputs saving on every keystroke, which meant two things:
 * no undo step was ever recorded for a spectrum edit (the panel's other
 * fields get one on blur, through liveInput), and a half-typed value was
 * written to the model. `Number("") || 0` turned a cleared cell into a
 * breakpoint at 0 Hz, which is not a frequency — so a blank field now leaves
 * the previous value alone instead of inventing one.
 */
function specCell(row, k, onedit) {
  return liveInput({ type: "number", step: "any", value: row[k] }, (t) => {
    const v = Number(t.value);
    if (t.value === "" || !Number.isFinite(v)) return;
    row[k] = v;
    onedit();
  });
}

function selInput(label, value, options, onchange) {
  // Two different situations, and they must not look alike.
  //
  // UNSET — the field is simply absent, which every optional setting is until
  // someone touches it. The solver then uses its default, and every caller
  // lists that default FIRST, so the first option is the honest display.
  // Treating absent as unrecognised printed "undefined — not recognised" on
  // the sweep spacing of any analysis that had never had it set.
  //
  // UNRECOGNISED — a value is present and matches nothing, e.g. a contact of
  // kind "frictional". A select cannot show what is not in it and would fall
  // back to the first option, so the panel would say "Bonded" over a contact
  // the solver treats as something else. That one is added and marked.
  const unset = value === undefined || value === null || value === ""
             || value === "undefined" || value === "null";
  const known = unset || options.some(([v]) => String(v) === String(value));
  const opts = known ? options
    : [[value, `${value} — not recognised, pick one below`], ...options];
  const s = el("select",
    { class: known ? "" : "bad", onchange: (e) => onchange(e.target.value) },
    opts.map(([v, t]) => {
      const o = el("option", { value: v }, t);
      if (String(v) === String(value)) o.selected = true;
      return o;
    }));
  return el("label", { class: "frm" }, label, s);
}

/** Copy an item, keeping its definition. For anything face-based the copy
 *  starts on the same faces — re-pick them, or use the bolt Pattern tool when
 *  the same joint repeats across a hole pattern. */
function dupRow(A, listName, id, label) {
  return el("div", { class: "btnrow" },
    el("button", { class: "btn btn-small", onclick: () => A.duplicateItem(listName, id) },
      `Duplicate ${label}`));
}

/** A select with <optgroup>s — needed once the size list spans two thread
 *  series and a flat list stopped being scannable. */
function selGroups(label, value, groups, onchange) {
  const s = el("select", { onchange: (e) => onchange(e.target.value) });
  for (const [gLabel, options] of groups) {
    const host = gLabel ? el("optgroup", { label: gLabel }) : s;
    for (const [v, t] of options) {
      const o = el("option", { value: v }, t);
      if (String(v) === String(value)) o.selected = true;
      host.append(o);
    }
    if (gLabel) s.append(host);
  }
  return el("label", { class: "frm" }, label, s);
}

function delBtn(label, fn) {
  return el("div", { class: "btnrow" },
    el("button", { class: "btn btn-small btn-danger", onclick: fn }, `Delete ${label}`));
}

function pickBtn(S, A, item, key = "faces") {
  const n = item[key]?.length || 0;
  return el("div", {},
    el("div", { class: "btnrow" },
      el("button", { class: "btn btn-accent", onclick: () => A.pickFaces(item, key) },
        n ? `Re-pick faces (${n})` : "Pick faces")),
    el("div", { class: "hint" }, n ? `${plural(n, "face")} assigned — shown highlighted in the viewport.`
                                   : "Click faces in the viewport, then press Done."));
}

// ---------- model / solids / connections ----------

function panelModel(S, A, put) {
  const geo = S.project.geometry;
  put("Model", S.project.name,
    sec("Geometry", dl([
      ["Solids", (() => {
        const gone = (S.project.setup.suppressed_solids || []).length;
        return gone ? `${geo.solids.length - gone} (${gone} removed)`
                    : geo.solids.length;
      })()],
      ["Faces", geo.faces.length],
      ["Bonded interfaces", geo.interfaces.length],
      ["Bounding box", `${fmtVal(geo.bbox[3] - geo.bbox[0])} × ${fmtVal(geo.bbox[4] - geo.bbox[1])} × ${fmtVal(geo.bbox[5] - geo.bbox[2])} mm`],
    ])),
    workflow(S),
    validation(S));
}

/**
 * Where the model actually is, on the one panel a project opens to.
 *
 * This was four numbered steps of static prose, classed as explanatory, which
 * meant it was hidden by default — so the panel a project opens to was four
 * geometry statistics and nothing else. A list that reads the model is worth
 * the space; a list that recites the manual is not.
 */
function workflow(S) {
  const setup = S.project.setup;
  const gone = new Set((setup.suppressed_solids || []).map(Number));
  const solids = (S.project.geometry.solids || []).filter((x) => !gone.has(Number(x.tag)));
  const analyses = setup.analyses || [];
  const meshed = !!S.meshData?.stats;

  const steps = [
    ["Material on every body",
     solids.length > 0 && solids.every((x) => setup.assignments[String(x.tag)]),
     `${solids.filter((x) => setup.assignments[String(x.tag)]).length} of ${solids.length}`],
    ["An analysis, with supports",
     analyses.length > 0 && analyses.every((a) => (a.supports || []).some((x) => x.faces?.length)),
     analyses.length ? plural(analyses.length, "analysis", "analyses") : "none yet"],
    ["Mesh", meshed && !meshIssues(S).length,
     meshed ? plural(S.meshData.stats.nodes, "node") : "not generated"],
    ["Results", analyses.some((a) => S.results[a.id] && !S.results[a.id].stale),
     analyses.filter((a) => S.results[a.id] && !S.results[a.id].stale).length
       ? `${analyses.filter((a) => S.results[a.id] && !S.results[a.id].stale).length} current`
       : "none current"],
  ];
  return sec("Model", el("div", { class: "steps" }, steps.map(([label, done, note]) =>
    el("div", { class: `step${done ? " done" : ""}` },
      el("span", { class: "tick" }, done ? "\u2713" : "\u00b7"),
      el("span", { class: "what" }, label),
      el("span", { class: "note" }, note)))));
}

function validation(S) {
  const w = [];
  const setup = S.project.setup;
  const geo = S.project.geometry;
  for (const s of geo.solids) {
    if (!setup.assignments[String(s.tag)]) w.push(`Solid "${s.name || s.tag}" has no material.`);
  }
  for (const a of setup.analyses || []) {
    if (!(a.supports || []).some((x) => x.faces?.length)) {
      w.push(`"${a.name || a.type}" has no support with faces.`);
    }
  }
  if (!(setup.analyses || []).length) w.push("No analyses yet — add one to define supports and loads.");
  if (!w.length) return null;
  return sec("Checks", ...w.map((t) => el("div", { class: "hint warn" }, "⚠ " + t)));
}

function panelSolid(S, A, put, tag) {
  const geo = S.project.geometry;
  const setup = S.project.setup;
  const s = geo.solids.find((x) => String(x.tag) === String(tag));
  if (!s) return put("Solid", "");
  const mid = setup.assignments[String(tag)] || "";
  const opts = [["", "— none —"],
    ...S.library.map((m) => [`lib:${m.id}`, m.name]),
    ...setup.materials.filter(isProjectMaterial)
        .map((m) => [m.id, `${m.name} (custom)`])];

  const hidden = S.hiddenSolids.has(s.tag);
  const removed = (setup.suppressed_solids || []).map(Number).includes(Number(tag));
  const custom = setup.materials.filter(isProjectMaterial);
  put(solidName(S, tag), removed ? "removed from the analysis"
                                 : `${s.faces.length} faces`,
    sec("Definition",
      textInput("Name", solidName(S, tag), (v) => A.renameSolid(s.tag, v))),
    sec("Material",
      selInput("Assign material", matValue(S, mid), opts,
        (v) => A.assignMaterial(tag, v)),
      matProps(S, mid),
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small", onclick: () => A.newMaterial(tag) },
          "New custom material…"),
        custom.some((m) => m.id === mid)
          ? el("button", { class: "btn btn-small", onclick: () => A.editMaterial(mid) },
              "Edit") : null)),
    sec("Properties", dl([
      ["Volume", `${fmtVal(s.volume)} mm³`],
      ["Mass", massOf(S, s)],
      ["Tag", String(s.tag)],
    ])),
    sec("In the analysis",
      el("div", { class: "hint" }, removed
        ? "This body is not meshed, carries no mass, and has no faces to pick. "
          + "The geometry is kept, so it can come back."
        : "Removing a body takes it out of the mesh and every analysis. The "
          + "geometry is kept — re-importing the STEP would renumber "
          + "everything, so removal is reversible instead."),
      el("div", { class: "btnrow" },
        el("button", { class: removed ? "btn btn-accent" : "btn btn-danger",
          onclick: () => A.removeSolid(s.tag) },
          removed ? "Restore to the analysis" : "Remove from the analysis"))),
    sec("Display",
      el("div", { class: "btnrow" },
        el("button", { class: "btn", onclick: () => A.toggleSolid(s.tag) },
          hidden ? "Show solid" : "Hide solid"),
        el("button", { class: "btn", onclick: () => A.isolateSolid(s.tag) },
          "Isolate"),
        S.hiddenSolids.size
          ? el("button", { class: "btn", onclick: () => A.showAllSolids() },
              `Show all (${S.hiddenSolids.size} hidden)`)
          : null),
      el("div", { class: "hint" },
        "Visibility is for looking and picking — a hidden solid is still in "
        + "the model and still solved. Hidden faces cannot be clicked, which "
        + "is the point when the face you want is inside a stack.")));
}

/**
 * Everything in the model that would stop meaning anything if this body went.
 *
 * Face-based items are matched through the geometry, not by remembering which
 * body they were picked on: a face knows which solids it bounds, which is the
 * only record that cannot drift.
 */
export function dependentsOfSolid(S, tag) {
  const t = Number(tag);
  const setup = S.project.setup;
  const faceSolids = new Map(
    (S.project.geometry.faces || []).map((f) => [f.tag, f.solids || []]));
  const touches = (faces) => (faces || []).some(
    (ft) => (faceSolids.get(ft) || []).map(Number).includes(t));

  const out = [];
  for (const c of setup.contacts || []) {
    if ((c.solids || []).map(Number).includes(t)
        || touches(c.faces_a) || touches(c.faces_b)) {
      out.push(`Contact "${c.name || "unnamed"}"`);
    }
  }
  for (const b of setup.bolts || []) {
    if (touches(b.side_a_faces) || touches(b.side_b_faces)) {
      out.push(`Bolt "${b.name || "unnamed"}"`);
    }
  }
  for (const ti of setup.ties || []) {
    if (Number(ti.master_solid) === t || touches(ti.slave_faces)) {
      out.push(`Tie "${ti.name || "unnamed"}"`);
    }
  }
  for (const a of setup.analyses || []) {
    const an = a.name || a.type;
    for (const sup of a.supports || []) {
      if (touches(sup.faces)) out.push(`${an}: support "${sup.name || "unnamed"}"`);
    }
    for (const l of a.loads || []) {
      if (touches(l.faces)) out.push(`${an}: load "${l.name || "unnamed"}"`);
    }
  }
  return out;
}

/**
 * Is this the project's own material rather than a copy of a library entry?
 *
 * One definition, because there used to be two and they disagreed. The option
 * list asked `!id.startsWith("lib-")` while the selected value asked
 * `id.startsWith("custom")`, so a material that was neither — anything
 * written by a script or an older build, e.g. an id of "st" — was listed as
 * an option and then not selected, and the dropdown quietly showed "— none —"
 * over a solid that had a material assigned. The properties printed directly
 * underneath it came from the real one, so the panel disagreed with itself on
 * the single control that decides what the part is made of.
 */
function isProjectMaterial(m) {
  return !m.lib && !String(m.id).startsWith("lib-");
}

/** The dropdown value that stands for a material id. */
function matValue(S, mid) {
  if (!mid) return "";
  const m = S.project.setup.materials.find((x) => x.id === mid);
  if (!m) return mid;              // assigned something that no longer exists
  return m.lib ? `lib:${m.lib}` : m.id;
}

function matProps(S, mid) {
  const m = S.project.setup.materials.find((x) => x.id === mid);
  if (!m) return el("div", { class: "hint" }, "Pick from the library — properties appear here.");
  return dl([
    ["Young's modulus", `${fmtVal(m.E_GPa)} GPa`],
    ["Poisson's ratio", m.nu],
    ["Density", `${m.rho_kgm3} kg/m³`],
    ...(m.yield_MPa ? [["Yield strength", `${m.yield_MPa} MPa`]] : []),
  ]);
}

function massOf(S, s) {
  const mid = S.project.setup.assignments[String(s.tag)];
  const m = S.project.setup.materials.find((x) => x.id === mid);
  if (!m) return "—";
  return `${fmtVal(s.volume * m.rho_kgm3 * 1e-12 * 1000)} kg`;  // mm³ → kg
}

/* ------------------------------------------------------------ group panels
 *
 * The tree's group rows used to be expand-only: clicking Connections did
 * nothing but fold it. A group is a place in the model, and selecting one
 * should tell you what is in it and let you add to it — which is also what
 * puts its actions on the command bar.
 */
function panelConnectionsGroup(S, A, put) {
  const setup = S.project.setup;
  const rows = [
    ...(setup.contacts || []).map((c) => [c.name || "Contact",
      c.suppressed ? "suppressed" : (c.kind || "bonded")]),
    ...(setup.bolts || []).map((b) => [b.name || "Bolt",
      `${boltSizeOf(b)?.id ?? "?"} · ${fmtVal(b.preload_N || 0)} N`]),
    ...(setup.ties || []).map((t) => [t.name || "Tie",
      t.master_solid ? `→ solid ${t.master_solid}` : "incomplete"]),
  ];
  put("Connections", rows.length ? `${rows.length} defined` : "none",
    sec("Defined", rows.length ? dl(rows)
      : el("div", { class: "hint" }, "Nothing yet.")),
    sec("Add",
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-accent",
          onclick: () => A.detectContacts() }, "Detect contacts"),
        el("button", { class: "btn", onclick: () => A.addBolt() }, "Bolt"),
        el("button", { class: "btn", onclick: () => A.addTie(),
          disabled: S.project.geometry.solids.length < 2 || null }, "Tie")),
      el("div", { class: "hint" },
        "A contact is a pair of coincident faces, so it is detected rather "
        + "than built by hand — picking both sides blind is slower and easier "
        + "to get wrong than editing what the geometry found.")));
}

function panelGeometry(S, A, put) {
  const geo = S.project.geometry;
  put("Geometry", `${geo.solids.length} solids · ${geo.faces.length} faces`,
    sec("Solids", dl(geo.solids.map((sd) => [
      solidName(S, sd.tag),
      S.hiddenSolids.has(sd.tag) ? "hidden" : `${fmtVal(sd.volume)} mm³`]))),
    sec("Bounding box", dl([
      ["X", `${fmtVal(geo.bbox[0])} … ${fmtVal(geo.bbox[3])} mm`],
      ["Y", `${fmtVal(geo.bbox[1])} … ${fmtVal(geo.bbox[4])} mm`],
      ["Z", `${fmtVal(geo.bbox[2])} … ${fmtVal(geo.bbox[5])} mm`],
      ["Diagonal", `${fmtVal(geo.diag)} mm`]])),
    S.hiddenSolids.size
      ? sec(null, el("div", { class: "btnrow" },
          el("button", { class: "btn", onclick: () => A.showAllSolids() },
            `Show all (${S.hiddenSolids.size} hidden)`)))
      : null);
}

function panelProbes(S, A, put) {
  const probes = S.project.setup.probes || [];
  put("Probes", probes.length ? `${probes.length} points` : "none",
    sec("Points", probes.length
      ? dl(probes.map((p) => [p.name || "Probe",
          `${fmtVal(p.x)}, ${fmtVal(p.y)}, ${fmtVal(p.z)}`]))
      : el("div", { class: "hint" },
          "Harmonic, random and shock read their response here.")),
    sec(null, el("div", { class: "btnrow" },
      el("button", { class: "btn btn-accent", onclick: () => A.addProbe() },
        "Add probe"))));
}

function panelBondedInterfaces(S, A, put) {
  const geo = S.project.geometry;
  const nameOf = (t) => solidName(S, t);
  put("Connections", `${geo.interfaces.length} bonded`,
    sec("Bonded (conformal)",
      el("div", { class: "hint" },
        "Shared faces found while importing. The mesh is continuous across " +
        "them — parts are bonded with no tie constraints needed."),
      dl(geo.interfaces.map((i) => [
        `Face ${i.face}`, `${nameOf(i.solids[0])} ↔ ${nameOf(i.solids[1])}`]))),
    sec(null, el("div", { class: "hint" },
      "Parts that only touch without sharing a face are NOT connected. " +
      "The mesh step warns if the model comes out in disconnected pieces.")));
}

// ---------- supports / loads / probes ----------

function panelSupport(S, A, put, id) {
  const { analysis, item: s } = A.findAnalysisOf("support", id);
  if (!s) return put("Support", "");
  put(s.name || "Support", analysis ? (analysis.name || analysis.type) : "",
    sec("Definition",
      textInput("Name", s.name, (v) => A.mutate(() => { s.name = v; })),
      selInput("Type", s.type, [
        ["fixed", "Fixed (all DOF = 0)"],
        ["frictionless", "Frictionless / symmetry"],
        ["disp", "Prescribed displacement"]],
        (v) => A.mutate(() => { s.type = v; })),
      s.type === "frictionless" ? el("div", { class: "hint" },
        "Blocks motion normal to the face, allows in-plane sliding — Ansys " +
        "“Frictionless Support”; also the symmetry-plane condition.") : null,
      s.type === "disp" ? el("div", { class: "frm-row" },
        numInput("UX (mm)", s.ux, (v) => A.mutate(() => { s.ux = v; })),
        numInput("UY (mm)", s.uy, (v) => A.mutate(() => { s.uy = v; })),
        numInput("UZ (mm)", s.uz, (v) => A.mutate(() => { s.uz = v; })),
      ) : null,
      s.type === "disp" ? el("div", { class: "hint" }, "Leave a field empty to keep that DOF free.") : null),
    sec("Faces", pickBtn(S, A, s)),
    sec(null, dupRow(A, "supports", id, "support"),
        delBtn("support", () => A.removeItem("supports", id))));
}

function panelLoad(S, A, put, id) {
  const { analysis, item: l } = A.findAnalysisOf("load", id);
  if (!l) return put("Load", "");
  const secs = [
    sec("Definition",
      textInput("Name", l.name, (v) => A.mutate(() => { l.name = v; })),
      selInput("Type", l.type,
        [["force", "Force on faces (total N)"],
         ["pressure", "Pressure (MPa)"],
         ["remote", "Remote force / moment"],
         ["gravity", "Gravity / body load"],
         ["rotation", "Rotational velocity"]],
        (v) => A.mutate(() => { l.type = v; }))),
  ];
  if (l.type === "force") {
    secs.push(sec("Force (total, N)",
      el("div", { class: "frm-row" },
        numInput("Fx", l.fx, (v) => A.mutate(() => { l.fx = v || 0; })),
        numInput("Fy", l.fy, (v) => A.mutate(() => { l.fy = v || 0; })),
        numInput("Fz", l.fz, (v) => A.mutate(() => { l.fz = v || 0; }))),
      el("div", { class: "hint" },
        "Applied as uniform traction: total force ÷ selected face area.")));
    secs.push(sec("Faces", pickBtn(S, A, l)));
  } else if (l.type === "pressure") {
    secs.push(sec("Pressure",
      numInput("Pressure (MPa)", l.pressure, (v) => A.mutate(() => { l.pressure = v || 0; })),
      el("div", { class: "hint" }, "Positive presses into the surface.")));
    secs.push(sec("Faces", pickBtn(S, A, l)));
  } else if (l.type === "remote") {
    const centroid = () => {
      const faces = new Map(S.project.geometry.faces.map((f) => [f.tag, f]));
      let acc = [0, 0, 0], area = 0;
      for (const t of l.faces || []) {
        const f = faces.get(Number(t));
        if (f) { for (let k = 0; k < 3; k++) acc[k] += f.com[k] * f.area; area += f.area; }
      }
      if (area > 0) A.mutate(() => { l.x = acc[0] / area; l.y = acc[1] / area; l.z = acc[2] / area; });
    };
    secs.push(sec("Remote point (mm)",
      el("div", { class: "frm-row" },
        numInput("X", l.x, (v) => A.mutate(() => { l.x = v || 0; })),
        numInput("Y", l.y, (v) => A.mutate(() => { l.y = v || 0; })),
        numInput("Z", l.z, (v) => A.mutate(() => { l.z = v || 0; }))),
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small", onclick: centroid }, "Use face centroid")),
      el("div", { class: "hint" },
        "Force and moment act here, distributed to the faces below through an " +
        "RBE3 coupling (Ansys Remote Force / “Deformable” behavior).")));
    secs.push(sec("Force (N) and moment (N·mm)",
      el("div", { class: "frm-row" },
        numInput("Fx", l.fx, (v) => A.mutate(() => { l.fx = v || 0; })),
        numInput("Fy", l.fy, (v) => A.mutate(() => { l.fy = v || 0; })),
        numInput("Fz", l.fz, (v) => A.mutate(() => { l.fz = v || 0; }))),
      el("div", { class: "frm-row" },
        numInput("Mx", l.mx, (v) => A.mutate(() => { l.mx = v || 0; })),
        numInput("My", l.my, (v) => A.mutate(() => { l.my = v || 0; })),
        numInput("Mz", l.mz, (v) => A.mutate(() => { l.mz = v || 0; })))));
    secs.push(sec("Faces", pickBtn(S, A, l)));
    secs.push(sec(null, el("div", { class: "hint warn" },
      "⚠ Changing remote loads invalidates the mesh (a coupling node is placed " +
      "at mesh time) — re-mesh before solving.")));
  } else if (l.type === "rotation") {
    secs.push(sec("Rotation",
      numInput("Speed (rpm)", l.rpm, (v) => A.mutate(() => { l.rpm = v || 0; })),
      el("div", { class: "frm-row" },
        numInput("axis X", l.axis?.[0] ?? 0, (v) => A.mutate(() => { l.axis = [v || 0, l.axis?.[1] ?? 0, l.axis?.[2] ?? 1]; })),
        numInput("axis Y", l.axis?.[1] ?? 0, (v) => A.mutate(() => { l.axis = [l.axis?.[0] ?? 0, v || 0, l.axis?.[2] ?? 1]; })),
        numInput("axis Z", l.axis?.[2] ?? 1, (v) => A.mutate(() => { l.axis = [l.axis?.[0] ?? 0, l.axis?.[1] ?? 0, v ?? 1]; }))),
      el("div", { class: "frm-row" },
        numInput("center X", l.center?.[0] ?? 0, (v) => A.mutate(() => { l.center = [v || 0, l.center?.[1] ?? 0, l.center?.[2] ?? 0]; })),
        numInput("center Y", l.center?.[1] ?? 0, (v) => A.mutate(() => { l.center = [l.center?.[0] ?? 0, v || 0, l.center?.[2] ?? 0]; })),
        numInput("center Z", l.center?.[2] ?? 0, (v) => A.mutate(() => { l.center = [l.center?.[0] ?? 0, l.center?.[1] ?? 0, v || 0]; }))),
      el("div", { class: "hint" },
        "Centrifugal body load on the whole model (static analyses only).")));
  } else {
    secs.push(sec("Gravity",
      numInput("Multiple of g (9.81 m/s²)", l.g_mag ?? 1, (v) => A.mutate(() => { l.g_mag = v ?? 1; })),
      el("div", { class: "frm-row" },
        numInput("dir X", l.g?.[0] ?? 0, (v) => A.mutate(() => { l.g = [v || 0, l.g?.[1] ?? 0, l.g?.[2] ?? -1]; })),
        numInput("dir Y", l.g?.[1] ?? 0, (v) => A.mutate(() => { l.g = [l.g?.[0] ?? 0, v || 0, l.g?.[2] ?? -1]; })),
        numInput("dir Z", l.g?.[2] ?? -1, (v) => A.mutate(() => { l.g = [l.g?.[0] ?? 0, l.g?.[1] ?? 0, v ?? -1]; })))));
  }
  secs.push(sec(null, dupRow(A, "loads", id, "load"),
                     delBtn("load", () => A.removeItem("loads", id))));
  put(l.name || "Load", analysis ? (analysis.name || analysis.type) : l.type, ...secs);
}

/**
 * Fastener sizes.
 *
 * `As` is the TENSILE STRESS AREA, and it is what the model uses — for the
 * beam section as well as for the preload suggestion. A bolt carries axial
 * load through its thread root, not its major diameter, and on the small
 * sizes the difference is not a detail: an M1.6 modelled on its ⌀1.6 shank is
 * 58 % stiffer than the real screw.
 *
 * Metric: ISO 262 coarse pitch, stress areas per ISO 898-1.
 * Unified: ASME B1.1; areas converted from in² at 645.16 mm²/in².
 */
export const BOLT_SIZES = [
  { id: "M1.6", label: "M1.6 × 0.35", d: 1.6, As: 1.27, series: "metric" },
  { id: "M2", label: "M2 × 0.4", d: 2.0, As: 2.07, series: "metric" },
  { id: "M2.5", label: "M2.5 × 0.45", d: 2.5, As: 3.39, series: "metric" },
  { id: "M3", label: "M3 × 0.5", d: 3.0, As: 5.03, series: "metric" },
  { id: "M4", label: "M4 × 0.7", d: 4.0, As: 8.78, series: "metric" },
  { id: "M5", label: "M5 × 0.8", d: 5.0, As: 14.2, series: "metric" },
  { id: "M6", label: "M6 × 1.0", d: 6.0, As: 20.1, series: "metric" },
  { id: "M8", label: "M8 × 1.25", d: 8.0, As: 36.6, series: "metric" },
  { id: "M10", label: "M10 × 1.5", d: 10.0, As: 58.0, series: "metric" },
  { id: "M12", label: "M12 × 1.75", d: 12.0, As: 84.3, series: "metric" },
  { id: "0-80", label: "#0-80 UNF", d: 1.524, As: 1.161, series: "unified" },
  { id: "2-56", label: "#2-56 UNC", d: 2.184, As: 2.387, series: "unified" },
  { id: "4-40", label: "#4-40 UNC", d: 2.845, As: 3.897, series: "unified" },
  { id: "6-32", label: "#6-32 UNC", d: 3.505, As: 5.865, series: "unified" },
];

/**
 * Yield / 0.2 % proof stress by grade, for the preload suggestion.
 *
 * Metric classes are ISO 898-1. The unified entries are the two things these
 * small screws are actually made of; the value stays editable because the
 * published minimum depends on which revision of the spec your supplier
 * certifies to.
 */
export const BOLT_GRADES = [
  { id: "8.8", label: "Class 8.8", yield_MPa: 640, E_GPa: 210, series: "metric" },
  { id: "10.9", label: "Class 10.9", yield_MPa: 940, E_GPa: 210, series: "metric" },
  { id: "12.9", label: "Class 12.9", yield_MPa: 1100, E_GPa: 210, series: "metric" },
  { id: "A574", label: "ASTM A574 alloy socket head", yield_MPa: 1055, E_GPa: 210, series: "unified" },
  { id: "SS", label: "Stainless A2-70 / 18-8", yield_MPa: 450, E_GPa: 193, series: "any" },
  // Ti-6Al-4V annealed (ASTM F136 / Grade 5): Rp0.2 ~ 860 MPa, E ~ 114 GPa.
  { id: "TI5", label: "Titanium Grade 5 (Ti-6Al-4V)", yield_MPa: 860, E_GPa: 114, series: "any" },
  // Polymer fasteners: yield is an order of magnitude down and the modulus
  // two, so both must travel with the grade — a PEEK screw modelled at
  // 210 GPa would carry load a steel bolt's share of the joint.
  { id: "PEEK", label: "PEEK (unfilled)", yield_MPa: 98, E_GPa: 3.8, series: "any" },
  { id: "PEEKGF30", label: "PEEK GF30 (30 % glass)", yield_MPa: 135, E_GPa: 10.0, series: "any" },
];

/** Polymer and titanium fasteners creep and relax; flag the ones that do. */
export const GRADE_NOTES = {
  PEEK: "PEEK relaxes: expect to lose a large fraction of preload over time and "
      + "with temperature. This is a linear elastic model — it does not creep.",
  PEEKGF30: "Glass-filled PEEK still relaxes, and its properties are anisotropic "
      + "and mould-dependent. Treat this modulus as nominal.",
  TI5: "Titanium galls readily in threads; the usable preload is often set by "
      + "the joint's galling limit rather than by yield.",
};

export const boltSize = (id) => BOLT_SIZES.find((s) => s.id === id) || null;

/** The size a saved bolt refers to, tolerating projects that predate the
 *  id-keyed table and only stored a diameter. */
export function boltSizeOf(bl) {
  if (bl.size) return boltSize(bl.size);
  if (bl.d_mm == null) return null;
  return BOLT_SIZES.find((s) => s.series === "metric"
                             && Math.abs(s.d - bl.d_mm) < 1e-6) || null;
}

/** Yield stress to size the preload against. */
export function boltYield(bl) {
  if (bl.yield_MPa > 0) return bl.yield_MPa;
  const g = BOLT_GRADES.find((x) => x.id === bl.grade);
  if (g) return g.yield_MPa;
  return boltSizeOf(bl)?.series === "unified" ? 1055 : 640;
}

/** Stress area actually used by the solver, for display and for the deck. */
export function boltArea(bl) {
  if (bl.as_mm2 > 0) return bl.as_mm2;
  const s = boltSizeOf(bl);
  if (s) return s.As;
  return bl.d_mm ? Math.PI * (bl.d_mm / 2) ** 2 : null;
}

function cylInfo(S, ftags) {
  const faces = new Map(S.project.geometry.faces.map((f) => [f.tag, f]));
  for (const t of ftags || []) {
    const fit = faces.get(Number(t))?.fit;
    if (fit?.kind === "cylinder") return fit;
  }
  return null;
}

function panelBolt(S, A, put, id) {
  const bl = S.project.setup.bolts.find((x) => x.id === id);
  if (!bl) return put("Bolt", "");
  const cylA = cylInfo(S, bl.side_a_faces);
  const cylB = cylInfo(S, bl.side_b_faces);
  const cyl = cylA || cylB;
  const holeD = cyl ? cyl.radius * 2 : null;

  const pick = (key, label, n) => el("div", {},
    el("div", { class: "btnrow" },
      el("button", { class: "btn btn-accent", onclick: () => A.pickFaces(bl, key) },
        n ? `${label} (${n})` : label)),
  );

  const size = boltSizeOf(bl);
  const area = boltArea(bl);
  const sy = boltYield(bl);
  const suggested = area ? Math.round(0.65 * area * sy) : null;
  const gradeOpts = BOLT_GRADES.filter(
    (g) => g.series === "any" || !size || g.series === size.series);

  put(bl.name || "Bolt", "beam + spider",
    sec("Definition",
      textInput("Name", bl.name, (v) => A.mutate(() => { bl.name = v; })),
      el("div", { class: "hint" },
        "Timoshenko beam shank; each end drives its faces through a " +
        "distributing (RBE3) spider — the standard linear bolt idealization.")),
    sec("Connected faces",
      pick("side_a_faces", "Pick head/hole side faces", bl.side_a_faces?.length),
      pick("side_b_faces", "Pick thread/nut side faces", bl.side_b_faces?.length),
      el("div", { class: "hint" },
        "Pick the hole cylinder(s) or the bearing face under head/nut on each side."),
      holeD ? el("div", { class: "hint good" },
        `Cylinder detected: ⌀${fmtVal(holeD)} mm hole.`) : null),
    sec("Bolt",
      selGroups("Nominal size", bl.size ?? size?.id ?? "",
        [["", [["", "— choose —"]]],
         ["Metric (ISO coarse)", BOLT_SIZES.filter((s) => s.series === "metric")
           .map((s) => [s.id, s.label])],
         ["Unified (inch)", BOLT_SIZES.filter((s) => s.series === "unified")
           .map((s) => [s.id, s.label])]],
        (v) => A.mutate(() => { applyBoltSize(bl, v); })),
      // the suggestion is a button, not a hidden hint: it is the one thing
      // on this panel that saves looking something up
      holeD && !size
        ? (nearestBolt(holeD)
            ? el("div", { class: "btnrow" },
                el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
                  applyBoltSize(bl, nearestBolt(holeD).id);
                }) }, `Use ${nearestBolt(holeD).label} for this \u2300${fmtVal(holeD)} mm hole`))
            : el("div", { class: "hint warn" },
                `No listed size fits a \u2300${fmtVal(holeD)} mm hole.`))
        : null,
      size ? dl([
        ["Major ⌀", `${size.d.toFixed(size.series === "unified" ? 3 : 2)} mm`],
        ["Stress area Aₛ", `${area.toFixed(2)} mm²`],
        ["Modelled as", `⌀${(2 * Math.sqrt(area / Math.PI)).toFixed(2)} mm beam`],
      ]) : null,
      selInput("Grade", bl.grade ?? gradeOpts[0]?.id ?? "",
        gradeOpts.map((g) => [g.id, `${g.label} — ${g.yield_MPa} MPa`]),
        (v) => A.mutate(() => {
          const g = BOLT_GRADES.find((x) => x.id === v);
          bl.grade = v;
          if (g) { bl.yield_MPa = g.yield_MPa; bl.E_GPa = g.E_GPa; }
        })),
      GRADE_NOTES[bl.grade]
        ? el("div", { class: "hint warn" }, "⚠ " + GRADE_NOTES[bl.grade]) : null,
      numInput("Yield / proof stress (MPa)", sy,
        (v) => A.mutate(() => { bl.yield_MPa = v || null; })),
      numInput("Preload (N)", bl.preload_N, (v) => A.mutate(() => { bl.preload_N = v; })),
      suggested ? el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small",
          onclick: () => A.mutate(() => { bl.preload_N = suggested; }) },
          `Suggest ${suggested.toLocaleString()} N`)) : null,
      suggested ? el("div", { class: "hint" },
        `65 % of yield on Aₛ = ${area.toFixed(2)} mm² × ${sy} MPa. Check it ` +
        `against your fastener spec and the clamped material's bearing limit — ` +
        `on small screws the plate usually gives up before the screw does.`) : null,
      numInput("Bolt modulus E (GPa)", bl.E_GPa ?? 210, (v) => A.mutate(() => { bl.E_GPa = v ?? 210; }))),
    boltPatternSection(S, A, bl),
    sec(null, el("div", { class: "hint" },
      "Preload acts in static analyses (axial pre-strain). Modal/harmonic use the " +
      "bolt stiffness but not the preload — linear analyses have no stress stiffening.")),
    sec(null,
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small", onclick: () => A.duplicateItem("bolts", id) },
          "Duplicate")),
      delBtn("bolt", () => A.removeItem("bolts", id))));
}

/** Copy this joint onto other holes. */
function boltPatternSection(S, A, bl) {
  const geo = S.project.geometry;
  const ready = bl.side_a_faces?.length || bl.side_b_faces?.length;
  if (!ready) {
    return sec("Pattern", el("div", { class: "hint" },
      "Pick this bolt's faces first, then you can copy it onto every other " +
      "hole in one pass."));
  }
  const refTag = bl.ref_faces?.[0] ?? defaultReferenceFace(geo, bl);
  const nTargets = candidateTargets(geo, S.project.setup.bolts, refTag).length;

  return sec("Pattern",
    dl([["Reference face", refTag ? describeFace(geo, refTag) : "—"]]),
    el("div", { class: "btnrow" },
      el("button", { class: "btn btn-accent", disabled: !refTag,
        onclick: () => A.patternBolt(bl.id) }, "Copy to other holes…"),
      el("button", { class: "btn btn-small", onclick: () => A.pickFaces(bl, "ref_faces") },
        "Change reference")),
    el("div", { class: "hint" },
      "Pick each target hole in the viewport — one new bolt per face. Every " +
      "other face of this bolt is carried across by the same offset, so the " +
      "nut-side hole and any bearing faces come with it. Size, preload and " +
      "modulus are copied."),
    el("div", { class: "hint" },
      nTargets
        ? `${nTargets} unused hole${nTargets === 1 ? "" : "s"} of a similar diameter ` +
          `${nTargets === 1 ? "is" : "are"} available as targets.`
        : "Every matching hole already has a bolt."));
}

/** Write a size onto a bolt: the id drives everything, the rest is carried
 *  along so the solver and older readers still see a diameter. */
function applyBoltSize(bl, id) {
  const s = boltSize(id);
  bl.size = id || null;
  bl.d_mm = s ? s.d : null;
  bl.as_mm2 = s ? s.As : null;
  if (s) {
    // a metric class on a #4-40, or A574 on an M6, is meaningless
    const g = BOLT_GRADES.find((x) => x.id === bl.grade);
    if (!g || (g.series !== "any" && g.series !== s.series)) {
      const def = BOLT_GRADES.find((x) => x.series === s.series);
      bl.grade = def?.id ?? null;
      bl.yield_MPa = def?.yield_MPa ?? null;
      if (def?.E_GPa) bl.E_GPa = def.E_GPa;
    }
  }
}

/** Largest size that still clears the hole, across both series — or none,
 *  when even that would rattle in it. ISO 273's coarse clearance is about
 *  1.25 d; without the upper limit a 13.5 mm hole was offered an M8. */
function nearestBolt(holeD) {
  let best = null;
  for (const s of BOLT_SIZES) {
    if (s.d <= holeD - 0.15 && (!best || s.d > best.d)) best = s;
  }
  return best && holeD <= 1.3 * best.d + 0.2 ? best : null;
}

function panelTie(S, A, put, id) {
  const t = S.project.setup.ties.find((x) => x.id === id);
  if (!t) return put("Tie", "");
  const geo = S.project.geometry;
  put(t.name || "Tie", "bonded, non-conformal",
    sec("Definition",
      textInput("Name", t.name, (v) => A.mutate(() => { t.name = v; })),
      el("div", { class: "hint" },
        "Glues faces of one part onto another part's volume (LIAISON_MAIL) even " +
        "when meshes don't match. Use when import didn't auto-bond an interface.")),
    sec("Slave faces",
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-accent", onclick: () => A.pickFaces(t, "slave_faces") },
          t.slave_faces?.length ? `Re-pick faces (${t.slave_faces.length})` : "Pick faces"))),
    sec("Master solid",
      selInput("Glued onto", String(t.master_solid ?? ""),
        [["", "— choose —"], ...geo.solids.map((x) => [String(x.tag), solidName(S, x.tag)])],
        (v) => A.mutate(() => { t.master_solid = v ? Number(v) : null; }))),
    sec(null, dupRow(A, "ties", id, "tie"),
        delBtn("tie", () => A.removeItem("ties", id))));
}

/**
 * A contact interface.
 *
 * Bonded is a linear constraint and is what a fragmented (conformal) assembly
 * already is. The other three are what make a preloaded joint mean anything:
 * with a bonded interface the parts can neither separate nor slide, so the
 * clamp load has nothing to do and slip cannot be assessed at all.
 */
function panelContact(S, A, put, id) {
  const c = (S.project.setup.contacts || []).find((x) => x.id === id);
  if (!c) return put("Contact", "");
  const geo = S.project.geometry;
  const nameOf = (t) => solidName(S, t);
  const linearFriction = c.kind === "friction" && (c.solve || "linear") === "linear";
  const sliding = ["frictionless", "friction", "noseparation"].includes(c.kind)
                  && !linearFriction;

  put(c.name || "Contact", (c.solids || []).map(nameOf).join(" ↔ "),
    sec("Definition",
      textInput("Name", c.name, (v) => A.mutate(() => { c.name = v; })),
      selInput("Behaviour", c.kind || "bonded", [
        ["bonded", "Bonded — glued, no sliding or gapping"],
        ["noseparation", "No separation — cannot gap, free to slide"],
        ["frictionless", "Frictionless — can gap and slide freely"],
        ["friction", "Frictional — can gap, slides above μ·N"]],
        (v) => A.mutate(() => { c.kind = v; })),
      c.kind === "friction"
        ? numInput("Friction coefficient μ", c.mu ?? 0.2,
            (v) => A.mutate(() => { c.mu = v ?? 0.2; }), { min: 0, max: 2, step: 0.05 })
        : null,
      c.kind === "friction"
        ? el("div", { class: "hint" },
            "Steel on steel dry is roughly 0.15–0.25; a slip-critical joint is "
            + "usually specified by its faying-surface class, not by a guess.")
        : null,
      c.kind === "friction"
        ? selInput("Solve", c.solve || "linear", [
            ["linear", "Assume stuck, then check — linear"],
            ["nonlinear", "Solve the sliding — nonlinear"]],
            (v) => A.mutate(() => { c.solve = v; }))
        : null,
      c.kind === "friction"
        ? el("div", { class: "hint" }, linearFriction
            ? "Glues the interface, solves linearly, then checks from the "
              + "interface tractions that friction held. A stuck frictional "
              + "interface is the same constraint as a bonded one, so if it "
              + "held this is the nonlinear answer."
            : "Solves the sliding itself: the load is stepped up and the "
              + "contact status iterated. Use this once the check says the "
              + "joint slips and you need to know how far.")
        : null),
    sec("Faces", dl([
      ["Side A", `${plural((c.faces_a || []).length, "face")} on ${nameOf((c.solids || [])[0])}`],
      ["Side B", `${plural((c.faces_b || []).length, "face")} on ${nameOf((c.solids || [])[1])}`],
      // The stored area is only ever set on the auto-detect path, so a contact
      // made by hand or by a script showed a dash forever. The face areas are
      // in the geometry either way; take the smaller side, which is what the
      // detector records and what actually bears.
      ["Interface area", (() => {
        const a = c.area ?? interfaceArea(S, c);
        return a ? `${fmtVal(a)} mm²` : "\u2014";
      })()],
    ]),
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
          const a = c.faces_a; c.faces_a = c.faces_b; c.faces_b = a;
          c.solids = [(c.solids || [])[1], (c.solids || [])[0]];
        }) }, "Swap sides"),
        el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
          c.suppressed = !c.suppressed;
        }) }, c.suppressed ? "Restore to the analysis" : "Remove from the analysis")),
      el("div", { class: "hint" },
        "Side B is the slave — give that side the finer mesh. Suppressing a "
        + "contact leaves the parts free of each other entirely.")),
    sliding
      ? sec(null, el("div", { class: "hint warn" },
          "\u26a0 Nonlinear: the run steps the load up and iterates, and takes "
          + "much longer. Static only; modal and vibration studies use the "
          + "bonded state."))
      : linearFriction
        ? sec(null, el("div", { class: "hint good" },
            "Solves linearly. The slip check appears under Solution and says "
            + "whether the stuck assumption held."))
        : sec(null, el("div", { class: "hint" },
            "Bonded stays linear and is exact. Use it wherever parts really "
            + "are welded, glued or clamped hard enough never to move.")),
    sec(null, delBtn("contact", () => A.removeItem("contacts", id))));
}

function panelProbe(S, A, put, id) {
  const p = S.project.setup.probes.find((x) => x.id === id);
  if (!p) return put("Probe", "");
  put(p.name || "Probe", "response point",
    sec("Definition",
      textInput("Name", p.name, (v) => A.mutate(() => { p.name = v; })),
      el("div", { class: "frm-row" },
        numInput("X (mm)", p.x, (v) => A.mutate(() => { p.x = v || 0; })),
        numInput("Y (mm)", p.y, (v) => A.mutate(() => { p.y = v || 0; })),
        numInput("Z (mm)", p.z, (v) => A.mutate(() => { p.z = v || 0; }))),
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-accent", onclick: () => A.pickPoint(p) }, "Pick point on surface")),
      el("div", { class: "hint" },
        "Snapped to the nearest mesh node at solve time. Harmonic FRFs are extracted here.")),
    ...probeReadout(S, A, p),
    sec(null, dupRow(A, "probes", id, "probe"),
        delBtn("probe", () => A.removeItem("probes", id))));
}

/**
 * What this probe actually measured, per solved analysis.
 *
 * A probe exists to give a number at a point. Its panel used to show only the
 * coordinates it was placed at — the one thing it is for appeared nowhere in
 * it, and you had to know which result node to open to find out whether the
 * point you asked about had moved at all.
 *
 * Only the analyses that extract at probes are listed. Static and modal decks
 * do not create the probe node groups, so there is nothing to report for them
 * and saying so beats an empty row.
 */
function probeReadout(S, A, p) {
  const idx = (S.project.setup.probes || []).findIndex((x) => x.id === p.id) + 1;
  const rows = [];
  for (const a of S.project.setup.analyses || []) {
    const meta = S.results[a.id];
    if (!meta) continue;
    if (a.type === "shock") {
      const R = derived(S, "shockResults", a.id);
      if (!R) {
        // Same on-demand fetch the shock panel does, guarded by shockPending,
        // so asking here does not mean opening the result node first.
        A.loadShock(a.id);
        rows.push([a.name || a.type, "computing\u2026"]);
        continue;
      }
      const r = R.probes?.find((x) => x.probe === `PROBE${idx}`);
      rows.push([a.name || a.type,
                 r ? `${fmtVal(r.mag)} mm peak` : "not extracted in this run"]);
    } else if (a.type === "random") {
      // A random study's sweep is a 1 g transfer function, not a response;
      // its peak displacement is not something the part ever sees. The
      // answer is the RMS.
      if (!(meta.frf || []).some((f) => f.probe === idx)) continue;
      const R = derived(S, "randomResults", a.id);
      if (!R) {
        A.loadRandom(a.id);
        rows.push([a.name || a.type, "computing\u2026"]);
        continue;
      }
      for (const c of (R.curves || []).filter((x) => x.probe === idx)) {
        rows.push([`${a.name || a.type} \u00b7 ${c.comp}`, `${fmtVal(c.grms)} g RMS`]);
      }
    } else if (a.type === "harmonic") {
      const mine = (meta.frf || []).filter((f) => f.probe === idx);
      if (!mine.length) continue;
      for (const f of mine) {
        let best = 0, at = 0;
        f.module.forEach((v, i) => { if (v > best) { best = v; at = f.freq[i]; } });
        rows.push([`${a.name || a.type} · ${f.comp}`,
                   `${fmtVal(best)} mm at ${fmtVal(at)} Hz`]);
      }
    }
  }
  if (!rows.length) {
    return [sec("Measured here", el("div", { class: "hint" },
      "Nothing yet. Probes are extracted by harmonic, random and shock runs; "
      + "static and modal decks do not create the probe node groups, so read "
      + "those from the contour instead."))];
  }
  return [sec("Measured here", dl(rows))];
}

// ---------- mesh ----------

function panelMesh(S, A, put) {
  const setup = S.project.setup;
  const m = setup.mesh;
  const stats = S.meshData?.stats;
  const diag = S.project.geometry.diag;
  const memLimit = (S.config?.solver?.memory_mb || 6000) / 1000;

  const secs = [
    sec("Sizing",
      numInput("Target element size (mm)", m.size_mm,
        (v) => A.mutate(() => { m.size_mm = v; }),
        { placeholder: `auto (${fmtVal(diag / 25)})`, min: 0 }),
      numInput("Elements around a full circle", m.curvature,
        (v) => A.mutate(() => { m.curvature = v || 10; }), { min: 4, max: 40 }),
      // This is the setting that quietly decides the size of the whole model
      // on a part with holes, and the old default of 16 was the right number
      // for linear elements rather than these.
      (m.curvature || 10) > 12 && Number(m.order || 2) === 2
        ? el("div", { class: "hint warn" },
            `\u26a0 Quadratic elements need about half of ${m.curvature}. Past `
            + "about 8 the peak stress at a hole stops improving and only the "
            + "element count grows.")
        : el("div", { class: "hint" },
            "How finely a bore or a fillet is followed. Quadratic elements "
            + "carry a mid-side node, so they need about half as many as "
            + "linear ones; past roughly 8 the peak stress at a hole stops "
            + "improving and only the element count grows."),
      selInput("Element order", m.order == null ? null : String(m.order),
        [["2", "Quadratic — recommended"], ["1", "Linear"]],
        (v) => A.mutate(() => { m.order = Number(v); })),
      selInput("Element shape", m.elements || "tet",
        [["tet", "Tetrahedra — works on any shape"],
         ["hex", "Hexahedra where the shape sweeps"]],
        (v) => A.mutate(() => { m.elements = v; })),
      (m.elements || "tet") === "hex"
        ? el("div", { class: "hint" },
            "Hexahedra by sweeping. Any prism works \u2014 a plate with bolt "
            + "holes, an L-section, a channel \u2014 and a stack of them is "
            + "swept as one chain so the interface stays conformal. Two 8 mm "
            + "bolted plates: 50,640 DOF against 112,059 for tetrahedra, and "
            + "a better worst element. Every solid has to sweep along the same "
            + "axis; if one does not, the whole model falls back to tetrahedra "
            + "and the job log says why.")
        : null),
    sec("Local refinement",
      ...(m.local || []).map((loc, i) => el("div", {},
        el("div", { class: "frm-row2" },
          numInput("Size (mm)", loc.size_mm, (v) => A.mutate(() => { loc.size_mm = v; })),
          el("div", { class: "btnrow" },
            el("button", { class: "btn btn-small", onclick: () => A.pickFaces(loc, "faces") },
              `Faces (${loc.faces?.length || 0})`),
            el("button", { class: "btn btn-small btn-danger",
              onclick: () => A.mutate(() => { m.local.splice(i, 1); }) }, "✕"))))),
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small",
          onclick: () => A.mutate(() => { (m.local ||= []).push({ faces: [], size_mm: null }); }) },
          "+ refinement region"))),
    sec(null, el("div", { class: "btnrow" },
      el("button", { class: "btn btn-accent", onclick: () => A.runMesh() }, "Generate mesh"))),
  ];
  if (stats) {
    // one implementation of "is the mesh current", shared with the tree badge
    // and the run blockers, so the three can never contradict each other
    const issues = meshIssues(S);
    if (issues.length) {
      secs.push(sec(null,
        el("div", { class: "hint bad" }, "⚠ This mesh no longer matches the model:"),
        ...issues.map((i) => el("div", { class: "hint bad" }, `· ${i.text}`)),
        el("div", { class: "btnrow" },
          el("button", { class: "btn btn-accent", onclick: () => A.runMesh() },
            "Re-mesh now"))));
    }
    secs.push(sec("Current mesh", dl([
      ["Nodes", stats.nodes.toLocaleString()],
      ["Elements", stats.elements.toLocaleString()],
      ["DOF", stats.dof.toLocaleString()],
      ["Order", stats.order === 2 ? "quadratic" : "linear"],
      ...(stats.element_kinds
        ? [["Element type", Object.entries(stats.element_kinds)
              .map(([k, v]) => `${v.toLocaleString()} ${k}`).join(", ")]] : []),
      ...(stats.quality_min != null
        ? [["Element quality (min / avg)",
            `${stats.quality_min.toFixed(2)} / ${stats.quality_avg.toFixed(2)}`]] : []),
      ["Mesh time", `${stats.wall_s}s`],
      ["Est. solve memory", `${stats.mem_gb_est} GB`],
    ])));
    if (stats.mem_gb_est > memLimit * 0.8) {
      secs.push(sec(null, el("div", { class: "hint warn" },
        `⚠ Estimated factorization memory (${stats.mem_gb_est} GB) is close to the ` +
        `solver limit (${memLimit} GB). Consider a coarser mesh.`)));
    }
    const lost = stats.missing_groups || [];
    if (lost.length) {
      secs.push(sec(null, el("div", { class: "hint bad" },
        `\u26a0 ${plural(lost.length, "boundary-condition group")} could not be `
        + `written into this mesh: ${lost.join(", ")}. The faces they refer to `
        + "are not in the geometry that was meshed \u2014 re-pick them, or "
        + "re-mesh if the geometry was re-imported. An analysis using them "
        + "will not run.")));
    }
    const q = stats.quality_counts || {};
    if (q.inverted) {
      secs.push(sec(null, el("div", { class: "hint bad" },
        `\u26a0 ${q.inverted} element${q.inverted > 1 ? "s are" : " is"} inverted. `
        + "The stiffness matrix would be wrong and the results would look "
        + "normal anyway, so this mesh will not solve. Re-mesh with a smaller "
        + "element size, or without the hex sweep.")));
    } else if (q.sliver) {
      secs.push(sec(null, el("div", { class: "hint warn" },
        `\u26a0 ${q.sliver} sliver element${q.sliver > 1 ? "s" : ""} `
        + `(quality below ${0.05}). Displacement is usually still sound; `
        + "stress read at or near them is noise. Refine there, or read the "
        + "stress somewhere else.")));
    } else if (q.poor > stats.elements * 0.02) {
      secs.push(sec(null, el("div", { class: "hint warn" },
        `\u26a0 ${q.poor.toLocaleString()} elements `
        + `(${(100 * q.poor / stats.elements).toFixed(1)} %) are below 0.2 `
        + "quality. Stress in those regions is not worth reading closely.")));
    }
    // bodies the mesh was built from; older meshes did not record it
    const active = stats.volumes ?? ((S.project.geometry.solids || []).length
                 - (S.project.setup.suppressed_solids || []).length);
    const groups = unjoinedGroups(S);
    if (stats.islands > active) {
      // more pieces than bodies: a body is itself in pieces, which no
      // connection can fix — a geometry defect, not a missing contact
      secs.push(sec(null, el("div", { class: "hint bad" },
        `\u26a0 The mesh is in ${stats.islands} pieces but the model has `
        + `${plural(active, "body", "bodies")}, so at least one body is itself `
        + "split. Check the geometry for slivers or gaps.")));
    } else if (groups.length > 1) {
      const names = groups.map((g) => g.map((t) => solidName(S, t)).join(" + "));
      secs.push(sec(null, el("div", { class: "hint bad" },
        `\u26a0 Nothing joins ${names.join(" and ")}. Add a contact, tie or `
        + "bolt between them, or each needs its own support.")));
    }
  }
  put("Mesh", stats ? `${stats.nodes.toLocaleString()} nodes` : "not meshed", ...secs);
}

// ---------- analyses ----------

export function defaultAnalysis(type) {
  const base = { id: uid(), type, name: "" };
  if (type === "static") return { ...base, name: "Static structural", config: {} };
  if (type === "modal") return { ...base, name: "Modal", config: { n_modes: 10 } };
  if (type === "random") {
    return { ...base, name: "Random vibration",
             config: { spec: [[20, 0.01], [80, 0.04], [350, 0.04], [2000, 0.007]],
                       base_dir: [0, 0, 1], damping: 0.02, n_steps: 600,
                       field_freqs: [] } };
  }
  if (type === "shock") {
    // Q = 10 (zeta = 0.05) is the damping nearly every shock spec is written
    // at; a spectrum read at one Q and applied at another is a different
    // spectrum. 20 g / 11 ms half-sine is the MIL-STD-810 workhorse.
    return { ...base, name: "Shock",
             config: { input: "pulse", pulse: "half_sine",
                       pulse_g: 20, pulse_ms: 11,
                       spec: [[100, 20], [1000, 200], [10000, 200]],
                       axis: 2, rule: "srss", damping: 0.05, n_modes: 30 } };
  }
  return { ...base, name: "Harmonic response",
           config: { f_min: 20, f_max: 2000, n_steps: 600, spacing: "log",
                     damping: 0.02, excitation: "force", base_dir: [0, 0, 1],
                     base_g: 1.0, field_freqs: [] } };
}

/** A peak is only resolved if several sweep points fall inside its
 *  half-power band (width ~ f/Q). Too coarse a sweep silently UNDER-reports Q,
 *  which would understate resonant response. */
function gramsOf(spec) {
  // matches random_vib.grms_input: exact integral of log-log segments
  const pts = spec.map((r) => [Number(r[0]), Number(r[1])])
    .filter((r) => r[0] > 0).sort((a, b) => a[0] - b[0]);
  let tot = 0;
  for (let i = 1; i < pts.length; i++) {
    const [f0, w0] = pts[i - 1], [f1, w1] = pts[i];
    if (f1 <= f0) continue;
    if (w0 <= 0 || w1 <= 0) { tot += 0.5 * (w0 + w1) * (f1 - f0); continue; }
    const m = Math.log(w1 / w0) / Math.log(f1 / f0);
    tot += Math.abs(m + 1) < 1e-9
      ? w0 * f0 * Math.log(f1 / f0)
      : (w0 / Math.pow(f0, m)) * (Math.pow(f1, m + 1) - Math.pow(f0, m + 1)) / (m + 1);
  }
  return Math.sqrt(Math.max(tot, 0)).toFixed(2);
}

/**
 * The sweep the solver will actually run, with the solver's own defaults.
 *
 * Neither analysis type stores every field: a harmonic that never had its
 * spacing touched has none, and a random analysis stores no band at all — the
 * deck writer derives it from the first and last spectrum breakpoints. Reading
 * the raw config therefore computed with undefined: NaN for every random
 * analysis, and LINEAR spacing for a harmonic the solver sweeps
 * logarithmically, which reported 0.5 points per peak and asked for 2,069
 * steps where the real sweep had 1.7 and needed about 590. Mirrors
 * comm_writer.write_harmonic / write_random.
 */
export function effectiveSweep(a) {
  const c = a?.config || {};
  const zeta = c.damping ?? 0.02;
  if (a?.type === "random") {
    const f = (c.spec || []).map((r) => Number(r[0]))
      .filter((x) => x > 0).sort((x, y) => x - y);
    if (f.length < 2) return null;
    return { f_min: f[0], f_max: f[f.length - 1], n_steps: c.n_steps ?? 600,
             spacing: "log", damping: zeta };
  }
  if (a?.type === "harmonic") {
    return { f_min: c.f_min ?? 20, f_max: c.f_max ?? 2000,
             n_steps: c.n_steps ?? 600, spacing: c.spacing || "log", damping: zeta };
  }
  return null;
}

function resolutionWarning(a, peaks) {
  const sw = effectiveSweep(a);
  if (!sw || !peaks.length || !(sw.damping > 0) || !(sw.f_max > sw.f_min)) return null;
  const f = peaks[0].f;
  const gaps = Math.max(sw.n_steps - 1, 1);   // n points span n - 1 intervals
  const stepPct = sw.spacing === "log"
    ? (Math.pow(sw.f_max / sw.f_min, 1 / gaps) - 1) * 100
    : ((sw.f_max - sw.f_min) / gaps) / Math.max(f, 1e-9) * 100;
  // half-power bandwidth is f/Q = 2 zeta f, i.e. 200 zeta percent of f
  const ptsInBand = (200 * sw.damping) / Math.max(stepPct, 1e-9);
  if (!Number.isFinite(ptsInBand) || ptsInBand >= 5) return null;
  const need = Math.ceil(sw.n_steps * 5 / Math.max(ptsInBand, 1e-9));
  return el("div", { class: "hint warn" },
    `\u26a0 The sweep puts ${ptsInBand.toFixed(1)} points across each resonance `
    + `at \u03b6 = ${sw.damping}; five are needed to catch the peak. Q and peak `
    + `amplitude read low. Use about ${need.toLocaleString()} steps, or sweep a `
    + "narrow band around each mode.");
}

/**
 * Can this engine run this analysis, and if not, why?
 *
 * Mirrors ccx_writer.unsupported_reason on the server, from the capability
 * table the server publishes — so what the panel says before you run is what
 * the deck writer will enforce when you do. Guessing separately here is how
 * the two drift apart.
 */
export function engineBlockers(S, a, engineId) {
  const caps = S.config?.capabilities?.[engineId];
  if (!caps) return [];                       // aster: no restrictions modelled
  const setup = S.project.setup;
  const out = [];
  const name = ENGINE_LABEL[engineId] || engineId;

  if (!caps.types.includes(a.type)) {
    out.push(`${name} runs ${caps.types.join(" and ")} in Lattice — ` +
             `“${a.name || a.type}” is ${a.type}.`);
  }
  if (setup.bolts?.length && !caps.features.includes("bolts")) {
    out.push(`${name} cannot model bolts yet (they need a distributing ` +
             `coupling and a pre-tension section).`);
  }
  if (setup.ties?.length && !caps.features.includes("ties")) {
    out.push(`${name} does not emit tie constraints yet.`);
  }
  for (const l of a.loads || []) {
    if (l.faces?.length === 0 && !["gravity", "rotation"].includes(l.type)) continue;
    if (!caps.loads.includes(l.type)) {
      out.push(`${name} does not support “${l.name || l.type}” (${l.type} load).`);
    }
  }
  for (const sup of a.supports || []) {
    if (!caps.supports.includes(sup.type)) {
      out.push(`${name} does not support “${sup.name || sup.type}” (${sup.type}).`);
    }
  }
  return out;
}

export const ENGINE_LABEL = { aster: "code_aster", ccx: "CalculiX" };

/** The engine this analysis will actually use. */
export function engineOf(S, a) {
  const engines = S.config?.solver?.engines || [];
  const want = a.config?.engine;
  if (want && engines.some((e) => e.id === want)) return want;
  if (want) return want;                        // chosen but unavailable here
  return engines.some((e) => e.id === "aster") ? "aster" : (engines[0]?.id || "aster");
}

/** An engine present here that CAN run this analysis, if any. */
export function engineThatCanRun(S, a) {
  for (const e of S.config?.solver?.engines || []) {
    if (!engineBlockers(S, a, e.id).length) return e.id;
  }
  return null;
}


/**
 * A number the solver has a default for. Blank shows that default and stores
 * nothing, so there is one source of truth for it. This used to write its own
 * fallback on clear, and the fallbacks disagreed with the solver's: clearing
 * "f max" wrote 1000 Hz where the solver's default is 2000.
 */
function optNum(A, label, c, key, dflt, attrs = {}) {
  return numInput(label, c[key], (v) => A.mutate(() => {
    if (v == null) delete c[key]; else c[key] = v;
  }), { placeholder: String(dflt), ...attrs });
}

/** Direction of a base excitation, as a vector. */
function dirRow(A, c) {
  const d = () => [c.base_dir?.[0] ?? 0, c.base_dir?.[1] ?? 0, c.base_dir?.[2] ?? 1];
  const set = (k) => (v) => A.mutate(() => { const x = d(); x[k] = v ?? 0; c.base_dir = x; });
  return el("div", { class: "frm-row" },
    numInput("Direction X", d()[0], set(0)),
    numInput("Y", d()[1], set(1)),
    numInput("Z", d()[2], set(2)));
}

/** An editable [frequency, level] table with its three buttons. */
function spectrumTable(A, c, unit, typical, typicalLabel) {
  const spec = c.spec || (c.spec = []);
  return [
    el("table", { class: "rtable psd" },
      el("tr", {}, el("th", {}, "Hz"), el("th", {}, unit), el("th", {}, "")),
      spec.map((_row, i) => el("tr", {},
        el("td", {}, specCell(spec[i], 0, () => A.saveOnly())),
        el("td", {}, specCell(spec[i], 1, () => A.saveOnly())),
        el("td", {}, el("button", {
          class: "btn btn-small btn-danger", title: "Remove this row",
          onclick: () => A.mutate(() => { spec.splice(i, 1); }) }, "✕"))))),
    el("div", { class: "btnrow" },
      el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
        const last = spec[spec.length - 1] || typical[0];
        spec.push([Math.round(last[0] * 2), last[1]]);
      }) }, "+ Row"),
      el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
        c.spec = typical.map((r) => [...r]);
      }) }, typicalLabel),
      el("button", { class: "btn btn-small", onclick: () => A.pasteSpec(c) }, "Paste…")),
  ];
}

/** Does this study have anything to set beyond what the analysis panel shows? */
export function hasSettings(a) {
  return a.type !== "static";
}

/**
 * How the study is solved: modes, sweep, damping, combination.
 *
 * What drives a base-excited study — direction, level, spectrum — is its own
 * tree row (panelExcitation), and the solver is picked on the analysis panel.
 * Both used to be here as well, so two rows and two places opened the same
 * controls.
 */
function panelSettings(S, A, put, id) {
  const a = S.project.setup.analyses.find((x) => x.id === id);
  if (!a) return put("Analysis settings", "");
  const c = (a.config ||= {});
  const secs = [];

  if (a.type === "modal") {
    secs.push(sec("Extraction",
      optNum(A, "Number of modes", c, "n_modes", 10, { min: 1, max: 100, step: 1 }),
      el("div", { class: "hint" }, "The lowest modes of the supported model.")));
  }
  if (a.type === "harmonic") {
    const base = c.excitation === "base";
    secs.push(sec("Excitation",
      selInput("Driven by", c.excitation || "force",
        [["force", "Loads in the tree"], ["base", "Base acceleration"]],
        (v) => A.mutate(() => { c.excitation = v; })),
      base
        ? el("div", { class: "btnrow" },
            el("button", { class: "btn btn-small",
              onclick: () => A.select("excitation", a.id) }, "Base excitation…"))
        : el("div", { class: "hint" },
            "Response scales with the load, so 1 N reads directly as a "
            + "transfer function.")));
    secs.push(sec("Sweep",
      el("div", { class: "frm-row2" },
        optNum(A, "From (Hz)", c, "f_min", 20, { min: 0 }),
        optNum(A, "To (Hz)", c, "f_max", 2000, { min: 0 })),
      el("div", { class: "frm-row2" },
        optNum(A, "Steps", c, "n_steps", 600, { min: 2, step: 1 }),
        selInput("Spacing", c.spacing, [["log", "Logarithmic"], ["lin", "Linear"]],
          (v) => A.mutate(() => { c.spacing = v; }))),
      optNum(A, "Modal damping ratio ζ", c, "damping", 0.02,
        { min: 0, max: 1, step: 0.005 }),
      el("div", { class: "hint" },
        "Modal superposition on the modes up to 1.6 × the top frequency, "
        + "read at the probes.")));
    secs.push(sec("Contour frequencies",
      textInput("Hz, comma-separated", (c.field_freqs || []).join(", "),
        (v) => A.mutate(() => {
          c.field_freqs = v.split(",").map((x) => Number(x.trim())).filter((x) => x > 0);
        })),
      el("div", { class: "hint" },
        "Full displacement fields are kept at these frequencies for contour plots.")));
  }
  if (a.type === "random") {
    secs.push(sec("Response",
      optNum(A, "Modal damping ratio ζ", c, "damping", 0.02,
        { min: 0, max: 1, step: 0.005 }),
      optNum(A, "Sweep steps", c, "n_steps", 600, { min: 2, step: 1 }),
      el("div", { class: "hint" },
        "The response PSD is |H(f)|² × the input, from a 1 g base "
        + "sweep across the spectrum. Too few steps across a resonance "
        + "under-reports the RMS.")));
  }
  if (a.type === "shock") {
    const zeta = c.damping ?? 0.05;
    secs.push(sec("Modal combination",
      selInput("Rule", c.rule || "srss",
        [["srss", "SRSS — modes independent"],
         ["nrl", "NRL — largest at full value"],
         ["abs", "Absolute sum — upper bound"]],
        (v) => A.mutate(() => { c.rule = v; })),
      el("div", { class: "frm-row2" },
        optNum(A, "Spectrum damping ζ", c, "damping", 0.05,
          { min: 0, max: 1, step: 0.005 }),
        optNum(A, "Modes", c, "n_modes", 30, { min: 1, step: 1 })),
      zeta > 0
        ? el("div", { class: "hint" },
            `ζ = ${zeta} is Q = ${(1 / (2 * zeta)).toFixed(0)}. Use the Q `
            + "the spectrum was written at.")
        : null,
      el("div", { class: "hint" },
        "Keep enough modes to carry the effective mass in the driven axis. "
        + "What they miss is added back at the ZPA.")));
  }
  if (!secs.length) {
    secs.push(sec(null, el("div", { class: "hint" },
      "Nothing to set for this analysis type.")));
  }
  put("Analysis settings", a.name || a.type, ...secs);
}

/** What drives a base-excited study: direction, level or spectrum. */
function panelExcitation(S, A, put, id) {
  const a = S.project.setup.analyses.find((x) => x.id === id);
  if (!a) return put("Base excitation", "");
  const c = (a.config ||= {});
  const secs = [];

  if (a.type === "harmonic") {
    if (c.excitation !== "base") {
      secs.push(sec(null,
        el("div", { class: "hint warn" },
          "This sweep is driven by the loads in the tree, not the base."),
        el("div", { class: "btnrow" },
          el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
            c.excitation = "base";
          }) }, "Drive it through the base"))));
    } else {
      secs.push(sec("Base acceleration",
        optNum(A, "Amplitude (g)", c, "base_g", 1, { min: 0 }),
        dirRow(A, c),
        el("div", { class: "hint" },
          "Every fixed support moves together as the shaker table, and loads "
          + "in the tree are ignored. Response is relative to the base. At "
          + "1 g the curve reads directly as transmissibility.")));
    }
  }
  if (a.type === "random") {
    const spec = c.spec || [];
    secs.push(sec("Input PSD",
      ...spectrumTable(A, c, "g²/Hz",
        [[20, 0.01], [80, 0.04], [350, 0.04], [2000, 0.007]], "Typical spec"),
      el("div", { class: "hint" },
        "Log-log between rows, zero outside them. Rows sort by frequency."),
      el("div", { class: "hint good" }, `Overall: ${gramsOf(spec)} g RMS`)));
    secs.push(sec("Direction", dirRow(A, c)));
  }
  if (a.type === "shock") {
    const pulse = (c.input || "spectrum") === "pulse";
    secs.push(sec("Input",
      selInput("Specified as", c.input || "spectrum",
        [["pulse", "Classical pulse"], ["spectrum", "SRS table"]],
        (v) => A.mutate(() => { c.input = v; })),
      selInput("Axis", String(c.axis ?? 2),
        [["0", "X"], ["1", "Y"], ["2", "Z"]],
        (v) => A.mutate(() => { c.axis = Number(v); }))));
    if (pulse) {
      secs.push(sec("Pulse",
        selInput("Shape", c.pulse || "half_sine",
          [["half_sine", "Half-sine"], ["sawtooth", "Terminal-peak sawtooth"],
           ["trapezoid", "Trapezoid"]],
          (v) => A.mutate(() => { c.pulse = v; })),
        el("div", { class: "frm-row2" },
          optNum(A, "Amplitude (g)", c, "pulse_g", 20, { min: 0, step: 1 }),
          optNum(A, "Duration (ms)", c, "pulse_ms", 11, { min: 0, step: 0.5 })),
        el("div", { class: "hint" },
          "The pulse's shock response spectrum is what is applied; the pulse "
          + "is not integrated in time.")));
    } else {
      // Reachable without a spec (made through the API, or switched over from
      // a pulse): the table still shows, and "Typical SRS" fills it.
      secs.push(sec("Shock response spectrum",
        ...spectrumTable(A, c, "g", [[100, 20], [1000, 200], [10000, 200]],
          "Typical SRS"),
        el("div", { class: "hint" },
          "Log-log between rows. Beyond the table the end value is held, "
          + "not zeroed: every stiffer mode sees the plateau.")));
    }
  }
  if (!secs.length) {
    secs.push(sec(null, el("div", { class: "hint" },
      "This analysis is not driven through its base.")));
  }
  put("Base excitation", a.name || a.type, ...secs);
}

/** Everything the run needs to be allowed to start. Stated explicitly — a
 *  greyed-out button with no explanation is a dead end. */
function runBlockers(S, a) {
  const blockers = [];
  const engines = S.config?.solver?.engines || [];
  if (!engines.length) {
    blockers.push("No solver detected. Set one up (README → Solver setup), " +
                  "then use Recheck solver — the check runs when the server starts.");
  } else {
    const eng = engineOf(S, a);
    if (!engines.some((e) => e.id === eng)) {
      blockers.push(`This analysis is set to run on ${ENGINE_LABEL[eng] || eng}, ` +
                    "which is not installed here. Pick another solver above.");
    }
    // exactly what the deck writer would refuse, said before the run
    for (const t of engineBlockers(S, a, eng)) blockers.push(t);
  }
  if (!S.meshData?.stats) {
    blockers.push("The model is not meshed yet — open Mesh and press Generate mesh.");
  } else {
    // Same check the backend makes, plus the elements that are built at mesh
    // time (bolt beams, probe nodes) — the solver would otherwise abort
    // minutes in, or worse, run without a joint that is in the tree.
    const vib = ["harmonic", "random", "shock"].includes(a.type);
    for (const i of meshIssues(S)) {
      if (i.scope === "all" || vib) blockers.push(`Re-mesh needed — ${i.text}.`);
    }
  }
  for (const s of S.project.geometry.solids) {
    if (!S.project.setup.assignments[String(s.tag)]) {
      blockers.push(`Solid "${s.name || s.tag}" has no material assigned.`);
    }
  }
  // BCs are per-analysis, so these blockers are about THIS analysis only
  if (!(a.supports || []).some((x) => x.faces?.length)) {
    blockers.push("This analysis has no support with faces — it would be unconstrained.");
  }
  const hasLoad = (a.loads || []).some(
    (x) => ["gravity", "rotation"].includes(x.type) || x.faces?.length);
  const hasPreload = (S.project.setup.bolts || []).some((x) => x.preload_N > 0);
  if (needsLoads(a) && !hasLoad && !hasPreload) {
    blockers.push("This analysis has no load — add one, or a bolt preload.");
  }
  if (["harmonic", "random"].includes(a.type) && !(S.project.setup.probes || []).length) {
    blockers.push("Add a probe — frequency response is extracted at probes.");
  }
  if (a.type === "random" && ((a.config || {}).spec || []).length < 2) {
    blockers.push("The input spectrum needs at least two breakpoints.");
  }
  // what the deck writer would refuse, said before the run rather than after
  const sw = a.type === "harmonic" ? effectiveSweep(a) : null;
  if (sw && !(sw.f_min > 0 && sw.f_max > sw.f_min)) {
    blockers.push(`The sweep runs from ${sw.f_min} to ${sw.f_max} Hz; it needs `
                  + "0 < From < To.");
  }
  if (sw && !(sw.n_steps >= 2)) blockers.push("The sweep needs at least 2 steps.");
  if (a.type === "shock") {
    const c = a.config || {};
    if ((c.input || "spectrum") === "spectrum" && (c.spec || []).length < 2) {
      blockers.push("The shock spectrum needs at least two breakpoints.");
    }
    if ((c.input || "spectrum") === "pulse"
        && !((c.pulse_g ?? 20) > 0 && (c.pulse_ms ?? 11) > 0)) {
      blockers.push("The pulse needs an amplitude and a duration above zero.");
    }
    // no shock-specific support check: the general one above already stops
    // an unrestrained model, and said the same thing twice
  }
  return blockers;
}

/**
 * Which solver runs this study.
 *
 * They are not interchangeable. code_aster covers every analysis type here;
 * CalculiX installs from a package manager and so is often the only one
 * present on macOS, but this tool only drives it for static and modal. An
 * engine that cannot run the study is offered disabled with the reason rather
 * than hidden, so the limitation is legible instead of mysterious.
 */
function solverPick(S, A, a) {
  const engines = S.config?.solver?.engines || [];
  if (!engines.length) return null;          // runBlockers says what to do
  const current = engineOf(S, a);
  const alt = engineThatCanRun(S, a);
  const blocked = engineBlockers(S, a, current).length > 0;
  return el("div", {},
    selInput("Solver", current,
      engines.map((e) => [e.id, engineBlockers(S, a, e.id).length
        ? `${e.label} \u2014 cannot run this` : e.label]),
      (v) => A.mutate(() => { (a.config ||= {}).engine = v; })),
    el("div", { class: "hint" }, engines.find((e) => e.id === current)?.detail || ""),
    blocked && alt && alt !== current
      ? el("div", { class: "btnrow" },
          el("button", { class: "btn btn-small btn-accent",
            onclick: () => A.mutate(() => { (a.config ||= {}).engine = alt; }) },
            `Switch to ${ENGINE_LABEL[alt] || alt}`))
      : null);
}

/** The analysis node: what this study is, and whether it can run. */
function panelAnalysis(S, A, put, id) {
  const a = S.project.setup.analyses.find((x) => x.id === id);
  if (!a) return put("Analysis", "");
  const running = S.runStatus[a.id] === "running";
  const blockers = runBlockers(S, a);
  const items = solutionItems(S, a);

  const secs = [
    sec("Definition",
      textInput("Name", a.name, (v) => A.mutate(() => { a.name = v; })),
      dl([["Type", TYPE_NAMES[a.type] || a.type],
          ["Driven by", drivenBy(a)]]),
      solverPick(S, A, a)),
    sec(null,
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-accent", disabled: running || blockers.length > 0,
          onclick: () => A.runAnalysis(a.id) }, running ? "Running…" : "Run analysis"),
        hasSettings(a)
          ? el("button", { class: "btn", onclick: () => A.select("settings", a.id) },
              "Settings") : null,
        // a solve can finish and a later step still fail — offer recovery
        S.runStatus[a.id] === "failed"
          ? el("button", { class: "btn", onclick: () => A.recoverResults(a.id) },
               "Recover results") : null,
        !S.config?.solver?.available
          ? el("button", { class: "btn btn-small", onclick: () => A.recheckSolver() },
               "Recheck solver") : null),
      ...blockers.map((t) => el("div", { class: "hint warn" }, "⚠ " + t)),
      !blockers.length && !running && !items.length
        ? el("div", { class: "hint good" }, "✓ Ready to run.") : null),
  ];

  if (items.length) {
    secs.push(sec("Solution",
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small",
          onclick: () => A.select("solution", a.id) }, "Open solution"),
        el("button", { class: "btn btn-small",
          onclick: () => A.exportResults(a.id, "all") }, "Export all (CSV)"))));
  }

  secs.push(sec(null, delBtn("analysis", () => A.removeItem("analyses", id))));
  put(a.name || a.type, TYPE_NAMES[a.type] || a.type, ...statusHead(S, A, a), ...secs);
}

export const TYPE_NAMES = { static: "Static structural", modal: "Modal",
                     shock: "Shock response spectrum",
                     harmonic: "Harmonic response", random: "Random vibration" };

function drivenBy(a) {
  if (a.type === "modal") return "nothing — free vibration";
  if (a.type === "random") return "base PSD";
  if (a.type === "harmonic") {
    return (a.config?.excitation || "force") === "base" ? "base acceleration" : "applied force";
  }
  // Shock is applied at the restrained base — the deck refuses to build
  // without a support for exactly that reason. It was falling through to
  // "applied loads", which is the one thing it is not.
  if (a.type === "shock") {
    return (a.config?.input || "spectrum") === "pulse"
      ? "base pulse" : "base shock spectrum";
  }
  return "applied loads";
}

/** The Solution node: an index of what the run produced, and the one place
 *  that says plainly whether it still matches the model. */
function panelSolution(S, A, put, id) {
  const a = S.project.setup.analyses.find((x) => x.id === id);
  if (!a) return put("Solution", "");
  const meta = S.results[a.id];
  if (!meta) {
    // Reached before a run, or after the run directory was removed. Say so
    // rather than sitting on "Loading…" forever waiting for a 404.
    if (S.runStatus[a.id] !== "done") {
      return put("Solution", a.name || a.type,
        sec(null,
          el("div", { class: "hint" }, "This analysis has not produced results yet."),
          el("div", { class: "btnrow" },
            el("button", { class: "btn btn-accent", onclick: () => A.runAnalysis(a.id) },
              "Run analysis"))));
    }
    A.openResults(a.id, { select: false });
    return put("Solution", a.name || a.type,
               sec(null, el("div", { class: "hint" }, "Loading results…")));
  }
  const items = solutionItems(S, a);
  const rows = items.map((it) => el("button", {
    class: "listrow", onclick: () => A.select("result", `${a.id}|${it.what}`) },
    el("span", { class: "nm" }, it.label),
    el("span", { class: "mt" }, it.meta || "")));

  put("Solution", a.name || a.type, ...statusHead(S, A, a),
    sec("Run", dl([
      ["Solved by", ENGINE_LABEL[meta.engine] || meta.engine || "code_aster"],
      ...(meta.equilibrium
        ? [["Equilibrium residual",
            `${(meta.equilibrium.residual_rel * 100).toFixed(3)} %`]] : []),
      ...(meta.peak_disp != null
        ? [["Peak displacement", `${fmtVal(meta.peak_disp)} mm`]] : []),
    ]),
      ),
    sec("Outputs", el("div", { class: "listrows" }, rows)),
    sec(null,
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-accent",
          onclick: () => A.exportResults(a.id, "all") }, "Export all (CSV)"),
        el("button", { class: "btn",
          onclick: () => A.exportResults(a.id, "tables") }, "Export tables (CSV)"),
        el("button", { class: "btn", onclick: () => A.runAnalysis(a.id) }, "Run again"))));
}

/** Banner shown above every panel that presents results, saying whether they
 *  still describe the model on screen. Stale numbers presented as live is the
 *  one failure mode that produces wrong engineering conclusions. */
function statusHead(S, A, a) {
  const meta = S.results[a.id];
  if (!meta) return [];
  // One line and its action, on every result panel. These were paragraphs —
  // the demo notice alone was 36 words, repeated on all 32 result panels of a
  // five-analysis project, under a red bar that already said it. The reasons
  // live where someone goes to read them: "Why it failed", the changelog,
  // METHODS.md.
  const rerun = el("button", { class: "btn btn-small btn-accent",
    onclick: () => A.runAnalysis(a.id) }, "Run again");
  // A failed run outranks everything: whatever is below it is a fragment.
  if (meta.failed) {
    // the headline, without the log path that follows it — that is on the
    // Why-it-failed panel, where there is room to read it
    const why = String(meta.error || `exit code ${meta.exit_code}`).split("  (exit")[0];
    return [el("div", { class: "stalebar fakebar" },
      el("b", {}, meta.recovered
        ? "\u26a0 Run failed \u2014 partial results only. "
        : "\u26a0 Run failed \u2014 nothing recovered. "),
      el("span", { class: "why" }, why),
      el("div", { class: "btnrow" }, rerun,
        el("button", { class: "btn btn-small",
          onclick: () => A.select("result", `${a.id}|warnings`) }, "Why it failed")))];
  }
  // Demo is a property of the run, not the session, so it survives a restart
  // with a real solver — which is why it cannot rely on the top bar.
  if (meta.demo) {
    return [el("div", { class: "stalebar fakebar" },
      el("b", {}, "\u26a0 Fabricated by the demo solver"),
      " \u2014 not engineering data." + (meta.stale ? " The model has also changed since." : ""),
      meta.stale ? el("div", { class: "btnrow" }, rerun) : null)];
  }
  if (meta.stale) {
    return [el("div", { class: "stalebar" },
      el("b", {}, "\u26a0 Out of date"),
      " \u2014 the model has changed since this ran.",
      el("div", { class: "btnrow" }, rerun))];
  }
  if (meta.no_signature) {
    return [el("div", { class: "stalebar" },
      "Run before change tracking existed \u2014 it may not match the model.",
      el("div", { class: "btnrow" }, rerun))];
  }
  return [];
}

// ---------- results ----------
//
// Each output gets its own panel, reached from its own row in the tree. They
// used to be concatenated below the run button in one endless scroll, which
// meant finding a number involved scrolling past every other number.

const RESULT_TITLES = {
  contours: "Contours", modes: "Modes", frf: "Frequency response",
  random: "Random response", bolts: "Bolt forces", reactions: "Reactions",
  shock: "Shock response", slip: "Slip check",
  warnings: "Solver messages", sizing: "Bolt sizing",
};

/** Pointer to the reference for this result, for when it is wanted.
 *
 *  Pinned to the tag of the version that is running. It pointed at `main`,
 *  so after an update the method described could be newer than the method
 *  that produced the numbers on screen. */
function methodRef(S, anchor, label) {
  const v = S.config?.version;
  const ref = v ? `v${v}` : "main";
  return el("div", { class: "methodref" },
    el("a", { href: `https://github.com/vraj549/lattice/blob/${ref}/docs/METHODS.md#${anchor}`,
              target: "_blank", rel: "noopener" }, label || "How this is computed \u2197"));
}

function panelResult(S, A, put, id) {
  const [aid, what] = String(id).split("|");
  const a = (S.project.setup.analyses || []).find((x) => x.id === aid);
  if (!a) return put("Results", "");
  const meta = S.results[aid];
  if (!meta) {
    // fill in data for a row the user already clicked — do not move them
    A.openResults(aid, { select: false });
    return put(RESULT_TITLES[what] || "Results", a.name || a.type,
               sec(null, el("div", { class: "hint" }, "Loading results…")));
  }
  const body = {
    contours: () => secContours(S, A, a),
    modes: () => secModes(S, A, a),
    frf: () => secFRF(S, A, a),
    random: () => randomSections(S, A, a),
    shock: () => shockSections(S, A, a),
    slip: () => slipSections(S, A, a),
    bolts: () => secBolts(S, A, a),
    sizing: () => secSizing(S, A, a),
    reactions: () => secReactions(S, A, a),
    warnings: () => secWarnings(S, A, a),
  }[what]?.() || [];

  // An export button says what it exports. Every result panel used to end in
  // "Export this (CSV)", and on Contours, Slip check, Bolt sizing and Solver
  // messages "this" was the tables file — not the field, the margins or the
  // messages on screen. Contours has its own nodal-values export; the others
  // have no export of what they show, so they offer none rather than a
  // different file under that name. "Export all" and the link back to the
  // analysis lived here too, on every panel, one click from the tree and the
  // Solution node that already carry both.
  const exp = {
    frf: ["frf", "Export this (CSV)"], random: ["random", "Export this (CSV)"],
    shock: ["shock", "Export this (CSV)"],
    modes: ["tables", "Export tables (CSV)"], bolts: ["tables", "Export tables (CSV)"],
    reactions: ["tables", "Export tables (CSV)"],
  }[what];
  const anchor = { contours: "contours", modes: "modes", frf: "frequency-response",
                   random: "random-vibration", bolts: "bolt-forces-and-stress",
                   shock: "shock", slip: "friction-without-a-newton-loop",
                   sizing: "bolt-sizing", reactions: "reactions" }[what];
  const tail = sec(null,
    exp ? el("div", { class: "btnrow" },
      el("button", { class: "btn btn-small",
        onclick: () => A.exportResults(aid, exp[0]) }, exp[1])) : null,
    anchor ? methodRef(S, anchor) : null);

  put(RESULT_TITLES[what] || "Results", a.name || a.type,
      ...statusHead(S, A, a), ...body, tail);
}

/** The reaction-force block, if the run produced one. */
function reactionRow(meta) {
  for (const b of meta.tables?.tables || []) {
    const ix = ["DX", "DY", "DZ"].map((c) => b.columns.indexOf(c));
    if (ix.every((i) => i >= 0) && b.rows.length) {
      return { row: b.rows[b.rows.length - 1], ix };
    }
  }
  return null;
}

function secModes(S, A, a) {
  const meta = S.results[a.id];
  const secs = [];
  const R = S.activeResult;

  const modes = meta.tables?.modes?.[0];
  if (modes && a.type !== "static") {
    const fi = modes.columns.indexOf("FREQ");   // NUME_MODE numbers the rows
    const rows = modes.rows.map((r, i) => ({ n: i + 1, f: r[fi] })).filter((r) => r.f != null);
    const fmax = Math.max(...rows.map((r) => r.f), 1);
    const part = meta.tables?.participation?.[0];
    // A mode at essentially zero frequency is a direction the structure is
    // free to move in. It is the most diagnostic number in a modal run and
    // the table showed it as "0.00 Hz" with a short bar and no comment.
    //
    // It is not automatically a fault: a free-free check is run precisely to
    // produce them, and six is the complete set for an unheld body. So this
    // says what it means and lets the engineer decide, rather than calling a
    // deliberate check an error.
    const rigid = rows.filter((r) => r.f <= 1e-3);
    if (rigid.length) {
      secs.push(sec(null, el("div", { class: "hint warn" },
        `\u26a0 ${plural(rigid.length, "mode")} at zero frequency `
        + `(${rigid.map((r) => "#" + r.n).join(", ")}): the model is free to move`
        + `${rigid.length === 6 ? " in every direction" : ""}. Expected in a `
        + "free-free check; otherwise the supports are not holding it.")));
    }
    secs.push(sec("Modes",
      el("div", { class: "modes" }, rows.map((r) =>
        el("button", {
          class: "mode",
          "aria-selected": R?.aid === a.id && R?.stepIdx === r.n - 1 ? "true" : "false",
          onclick: () => A.showMode(a.id, r.n - 1),
        },
          el("span", { class: "mi" }, String(r.n).padStart(2, "0")),
          el("span", { class: "mbar" }, el("i", { style: `width:${8 + (r.f / fmax) * 88}%` })),
          el("span", { class: "mf" }, `${fmtVal(r.f)} Hz`)))),
      part ? partTable(part) : null));
  }
  return secs;
}

function secFRF(S, A, a) {
  const meta = S.results[a.id];
  const secs = [];
  if (meta.frf?.length) {
    const probes = S.project.setup.probes;
    // What the curves can be shown as depends on what drove them. A base
    // sweep's raw curve is displacement RELATIVE to the base; what a shaker
    // test reads is transmissibility, so that is its default view. Per unit
    // force is offered only when there is a force to divide by — for a base
    // sweep the loads in the tree were never applied.
    const baseDriven = a.config?.excitation === "base";
    const totalF = baseDriven ? 0 : (a.loads || []).reduce((acc, l) => {
      if (!["force", "remote"].includes(l.type)) return acc;
      return acc + Math.hypot(l.fx || 0, l.fy || 0, l.fz || 0);
    }, 0);
    const views = baseDriven
      ? [["trans", "Transmissibility (absolute \u00f7 input)", "transmissibility"],
         ["amp", "Amplification (\u00d7 quasi-static)", "\u00d7 quasi-static"],
         ["raw", "Relative displacement (mm)", "mm, relative"]]
      : [["raw", "Displacement (mm)", "mm"],
         ["amp", "Amplification (\u00d7 quasi-static)", "\u00d7 quasi-static"],
         ...(totalF > 0 ? [["perN", "Per unit force (mm/N)", "mm/N"]] : [])];
    const norm = views.some(([v]) => v === S.frfNorm) ? S.frfNorm : views[0][0];
    const curves = meta.frf.map((f, i) => {
      let mod = f.module;
      if (norm === "perN") mod = f.module.map((v) => v / totalF);
      if (norm === "trans") {
        mod = transmissibility(f.freq, f.module, f.phase, a.config?.base_g ?? 1);
      }
      if (norm === "amp") {
        // dynamic amplification: response ÷ the low-frequency (quasi-static)
        // response of the same curve, which is the textbook definition
        const base = f.module[0] || 1;
        mod = f.module.map((v) => v / base);
      }
      return {
        label: `${probes[f.probe - 1]?.name || "P" + f.probe}·${f.comp}`,
        freq: f.freq, module: mod, color: seriesColor(i),
      };
    });

    const canvas = el("canvas", { class: "frfbig" });
    const unitTxt = views.find(([v]) => v === norm)[2];

    // Peaks are computed synchronously from the data. Appending them later
    // from a rAF callback raced with the panel re-render that openResults
    // triggers, and the table was silently lost.
    const peaks = [];
    curves.forEach((c) => {
      for (const pk of findPeaks(c.freq, c.module)) {
        peaks.push({ ...pk, label: c.label, color: c.color });
      }
    });
    peaks.sort((a, b) => a.f - b.f);

    secs.push(sec(`Frequency response \u2014 ${unitTxt}`,
      selInput("Y axis", norm, views.map(([v, label]) => [v, label]),
        (v) => { S.frfNorm = v; A.refreshPanel(); }),
      canvas,
      el("div", { class: "hint" }, curves.map((c) =>
        el("span", { style: `color:${c.color};margin-right:8px` }, c.label))),
      peaks.length ? el("div", {},
        el("span", { class: "lbl", style: "display:block;margin:10px 0 4px" }, "Peaks"),
        el("table", { class: "rtable" },
          el("tr", {}, ["Hz", "Curve", "Amplitude", "Q", "\u03b6"].map((t) => el("th", {}, t))),
          peaks.map((pk) => el("tr", {},
            el("td", {}, fmtVal(pk.f)),
            el("td", { style: `color:${pk.color}` }, pk.label),
            el("td", {}, fmtVal(pk.amp)),
            el("td", {}, pk.q ? pk.q.toFixed(1) : "\u2014"),
            el("td", {}, pk.q ? (1 / (2 * pk.q)).toFixed(4) : "\u2014")))),
        resolutionWarning(a, peaks)) : null,
    ));

    // the canvas needs layout before it can size itself, so only the DRAW
    // is deferred — nothing is appended to the DOM from here
    requestAnimationFrame(() => { if (canvas.isConnected) frfPlot(canvas, curves); });
  }
  return secs;
}

function secContours(S, A, a) {
  const meta = S.results[a.id];
  const realFields = meta.fields?.filter((f) => f.part !== "I") || [];
  if (!realFields.length) return [];
  // Field, component, step, deformation and animate are on the command bar.
  // They are what you change WHILE looking at the result, and reaching across
  // to a panel to do it meant looking away from the thing being changed —
  // then pressing a second button to apply it. What is left here is styling,
  // which is set once, and the export.
  return [sec("Contour style",
    el("div", { class: "frm-row2" },
      selInput("Bands", String(contourStyle.bands),
        [["9", "9 (default)"], ["5", "5"], ["13", "13"], ["18", "18"],
         ["27", "27"], ["0", "Smooth"]],
        (v) => { contourStyle.bands = Number(v); A.restyleContours(); }),
      selInput("Palette", contourStyle.palette,
        [["rainbow", "Rainbow"], ["turbo", "Turbo"]],
        (v) => { contourStyle.palette = v; A.restyleContours(); })),
    el("div", { class: "btnrow" },
      el("button", { class: "btn btn-small",
        onclick: () => A.exportField(a.id) }, "Export nodal values (CSV)")))];
}

function secBolts(S, A, a) {
  const meta = S.results[a.id];
  const secs = [];
  const P = meta.preload;
  if (P) {
    // An imposed strain does not deliver the force it is derived from; the
    // clamped parts take part of it back. What the run actually contains is
    // the number every margin below depends on, so it is reported, not assumed.
    const bolts = S.project.setup.bolts;
    const ids = Object.keys(P.requested).sort((x, y) => Number(x) - Number(y));
    secs.push(sec("Preload in the model",
      el("table", { class: "rtable" },
        el("tr", {}, ["Bolt", "Requested", "In model", "Error"]
          .map((h) => el("th", {}, h))),
        ids.map((k) => {
          const req = P.requested[k];
          const got = P.achieved?.[k];
          const err = got == null ? null : got / req - 1;
          return el("tr", {},
            el("td", {}, bolts[Number(k) - 1]?.name || `Bolt ${k}`),
            el("td", {}, fmtVal(req)),
            el("td", {}, got == null ? "\u2014" : fmtVal(got)),
            el("td", { class: err != null && Math.abs(err) > 0.01 ? "bad" : "" },
               err == null ? "\u2014" : `${(100 * err).toFixed(1)} %`));
        })),
      P.calibrated ? null
        : P.achieved == null
          ? el("div", { class: "hint bad" },
               "Not calibrated \u2014 the bolts carry less than the requested force.")
          : el("div", { class: "hint bad" },
               `Calibration ran out of passes ${(100 * P.max_error).toFixed(1)} % ` +
               `from the requested force (tolerance ${(100 * (P.tol ?? 0.01)).toFixed(1)} %). ` +
               "The correction is applied and the column above is what the run " +
               "actually contains \u2014 treat the preload as approximate.")));
  }
  const boltBlocks = meta.tables?.bolt_forces || [];
  if (boltBlocks.length) {
    const rows = [];
    for (const blk of boltBlocks) {
      const ii = blk.columns.indexOf("INTITULE");
      const iN = blk.columns.indexOf("N");
      const iVY = blk.columns.indexOf("VY");
      const iVZ = blk.columns.indexOf("VZ");
      const iMY = blk.columns.indexOf("MFY");
      const iMZ = blk.columns.indexOf("MFZ");
      if (iN < 0) continue;
      for (const r of blk.rows) {
        const label = ii >= 0 ? String(r[ii]) : "bolt";
        const num = (i) => (i >= 0 && typeof r[i] === "number" ? r[i] : 0);
        rows.push({ label, N: num(iN),
                    V: Math.hypot(num(iVY), num(iVZ)),
                    M: Math.hypot(num(iMY), num(iMZ)) });
      }
    }
    // one row per bolt end is enough; collapse to worst case per bolt
    const per = new Map();
    for (const r of rows) {
      const bolt = r.label.replace(/_[AB]$/, "");
      const cur = per.get(bolt);
      if (!cur || Math.abs(r.N) > Math.abs(cur.N)) per.set(bolt, r);
    }
    if (per.size) {
      const bolts = S.project.setup.bolts;
      secs.push(sec("Bolt forces and stress",
        el("table", { class: "rtable" },
          el("tr", {}, ["Bolt", "Axial N", "Shear N", "\u03c3 axial", "\u03c4",
                        "\u03c3 bend", "\u03c3 eqv", "% yield"].map((h) => el("th", {}, h))),
          [...per.entries()].map(([label, r]) => {
            // label is "BOLT<n>" \u2014 map by index parsed from the label, not row order
            const n = Number((label.match(/BOLT(\d+)/) || [])[1]);
            const cfg = n ? bolts[n - 1] : null;
            const st = boltStress(cfg, r);
            return el("tr", {},
              el("td", {}, cfg?.name || label),
              el("td", {}, fmtVal(r.N)),
              el("td", {}, fmtVal(r.V)),
              el("td", {}, st ? fmtVal(st.axial) : "\u2014"),
              el("td", {}, st ? fmtVal(st.shear) : "\u2014"),
              el("td", {}, st ? fmtVal(st.bend) : "\u2014"),
              el("td", {}, st ? fmtVal(st.eqv) : "\u2014"),
              el("td", { class: st && st.pct > 100 ? "bad" : "" },
                 st ? `${st.pct.toFixed(0)} %` : "\u2014"));
          })),
        ));
    }
  }
  return secs;
}

/**
 * Bolt stresses from beam end forces.
 *
 * All on the equivalent circular section of the tensile stress area — the
 * section the beam was actually given — so N, V and M are already consistent
 * with it and no second assumption enters.
 */
function boltStress(cfg, r) {
  if (!cfg) return null;
  const A = boltArea(cfg);
  if (!(A > 0)) return null;
  const rad = Math.sqrt(A / Math.PI);
  const I = Math.PI * rad ** 4 / 4;
  const axial = r.N / A;
  const shear = r.V / A;
  const bend = I > 0 ? (r.M * rad) / I : 0;
  const eqv = Math.sqrt((axial + bend) ** 2 + 3 * shear ** 2);
  const sy = boltYield(cfg);
  return { axial, shear, bend, eqv, pct: sy > 0 ? (100 * eqv) / sy : 0 };
}

/**
 * Required preload per bolt.
 *
 * The FE gives each bolt its share of the external load; VDI 2230 turns that
 * into the preload the joint has to be assembled with. Computed on the server,
 * where the arithmetic is unit-tested.
 */
function secSizing(S, A, a) {
  const R = derived(S, "sizing", a.id);
  if (!R) {
    A.loadSizing(a.id);
    return [sec(null, el("div", { class: "hint" }, "Sizing\u2026"))];
  }
  const cfg = R.assumptions || {};
  const secs = [];

  for (const w of R.warnings || []) {
    secs.push(sec(null, el("div", { class: "hint bad" }, "\u26a0 " + w)));
  }

  if (R.blocked) return secs;
  secs.push(sec("Assumptions",
    el("div", { class: "frm-row2" },
      numInput("Faying friction \u03bc", cfg.mu_joint,
        (v) => A.setSizing(a.id, { mu_joint: v ?? 0.15 }), { step: 0.01 }),
      numInput("Friction interfaces", cfg.n_friction,
        (v) => A.setSizing(a.id, { n_friction: v ?? 1 }), { min: 1, step: 1 })),
    selInput("Tightening method", cfg.tightening,
      (R.tightening_options || []).map((t) => [t.id, `${t.label} (\u00d7${t.alpha_A})`]),
      (v) => A.setSizing(a.id, { tightening: v })),
    el("div", { class: "frm-row2" },
      numInput("Thread \u03bc", cfg.mu_thread,
        (v) => A.setSizing(a.id, { mu_thread: v ?? 0.14 }), { step: 0.01 }),
      numInput("Embedding (\u00b5m)", cfg.embedding_um,
        (v) => A.setSizing(a.id, { embedding_um: v ?? 6 }), { step: 1 })),
    el("div", { class: "frm-row2" },
      numInput("Slip factor", cfg.S_slip,
        (v) => A.setSizing(a.id, { S_slip: v ?? 1.2 }), { step: 0.05 }),
      numInput("Bearing limit (MPa)", cfg.p_G,
        (v) => A.setSizing(a.id, { p_G: v }), { step: 10 }))));

  const rows = R.rows || [];
  if (!rows.length) {
    secs.push(sec(null, el("div", { class: "hint" },
      "No bolts with mesh records in this run.")));
    return secs;
  }

  secs.push(sec("Required preload",
    el("table", { class: "rtable" },
      el("tr", {}, ["Bolt", "F_A N", "F_Q N", "\u03a6", "clamp N",
                    "preload N", "max N", "limit N"].map((h) => el("th", {}, h))),
      rows.map((r) => el("tr", {},
        el("td", {}, r.name),
        el("td", {}, fmtVal(r.F_A ?? 0)),
        el("td", {}, fmtVal(r.F_Q ?? 0)),
        el("td", {}, r.phi.toFixed(3)),
        el("td", {}, fmtVal(r.F_KR)),
        el("td", {}, fmtVal(r.F_Mmin)),
        el("td", { class: r.feasible ? "" : "bad" }, fmtVal(r.F_Mmax)),
        el("td", {}, fmtVal(r.F_Mzul)))))));

  secs.push(sec("Tightening and margins",
    el("table", { class: "rtable" },
      el("tr", {}, ["Bolt", "torque N\u00b7m", "% yield", "head MPa",
                    "\u03c3a MPa", "fatigue", "verdict"].map((h) => el("th", {}, h))),
      rows.map((r) => el("tr", {},
        el("td", {}, r.name),
        el("td", {}, r.M_A_Nm.toFixed(1)),
        el("td", { class: r.utilisation > 1 ? "bad" : "" },
           (r.utilisation * 100).toFixed(0)),
        el("td", {}, fmtVal(r.p_max)),
        el("td", {}, r.sigma_a == null ? "\u2014" : fmtVal(r.sigma_a)),
        el("td", { class: r.fatigue_margin != null && r.fatigue_margin < 1 ? "bad" : "" },
           r.fatigue_margin == null ? "\u2014" : r.fatigue_margin.toFixed(2)),
        el("td", { class: r.passes ? "" : "bad" },
           r.passes ? "ok" : (r.feasible ? "check" : "no window")))))));

  const problems = rows.flatMap((r) => (r.checks || []).map((c) => `${r.name}: ${c}`));
  if (problems.length) {
    secs.push(sec(null, ...problems.map((t) => el("div", { class: "hint bad" }, "\u26a0 " + t))));
  }
  return secs;
}

function secReactions(S, A, a) {
  const meta = S.results[a.id];
  const hit = reactionRow(meta);
  if (!hit) return [];
  const { row: r, ix } = hit;
  const sum = [0, 1, 2].map((k) => r[ix[k]] ?? 0);
  return [sec("Reaction forces (supports)",
    dl([["ΣFx", `${fmtVal(sum[0])} N`],
        ["ΣFy", `${fmtVal(sum[1])} N`],
        ["ΣFz", `${fmtVal(sum[2])} N`],
        ["|ΣF|", `${fmtVal(Math.hypot(...sum))} N`]]),
    )];
}

function secWarnings(S, A, a) {
  const meta = S.results[a.id];
  if (!meta.warnings?.length && !meta.error) return [];
  const secs = [];
  if (meta.error) {
    secs.push(sec("Why the run failed",
      el("div", { class: "hint bad" }, meta.error),
      meta.log_path
        ? el("div", { class: "hint" },
            "The solver's full output is in " + meta.log_path)
        : null));
  }
  if (meta.warnings?.length) {
    secs.push(sec("Solver messages",
      ...meta.warnings.map((w) => el("div", { class: "hint warn" }, w))));
  }
  return secs;
}

/**
 * Slip check.
 *
 * The solve glued every checked frictional interface. This says whether it
 * was allowed to: a stuck frictional interface and a bonded one are the same
 * constraint, so if friction held everywhere the linear result is not an
 * approximation of the nonlinear one, it is the nonlinear one.
 */
function slipSections(S, A, a) {
  const R = derived(S, "slipResults", a.id);
  if (!R) {
    A.loadSlip(a.id);
    return [sec("Slip check", el("div", { class: "hint" }, "Checking\u2026"))];
  }
  const secs = [];
  for (const r of R.rows || []) {
    if (r.error) {
      secs.push(sec(r.name, el("div", { class: "hint warn" }, "\u26a0 " + r.error)));
      continue;
    }
    secs.push(sec(r.name,
      el("div", { class: r.held ? "hint good" : "hint bad" },
         (r.held ? "\u2713 " : "\u26a0 ") + r.verdict),
      dl([["Worst margin \u03bc\u00b7p/\u03c4",
           isFinite(r.min_margin) ? r.min_margin.toFixed(2) : "\u221e"],
          ["\u03bc needed", r.mu_required.toFixed(3)],
          ["\u03bc assumed", r.mu.toFixed(3)],
          ["Area slipping", `${(100 * r.area_slipping).toFixed(1)} %`],
          ["Area in tension", `${(100 * r.area_open).toFixed(1)} %`],
          ["Peak pressure", `${fmtVal(r.p_max)} MPa`],
          ["Peak shear", `${fmtVal(r.tau_max)} MPa`]]),
      r.area_weighted === false
        ? el("div", { class: "hint" },
            "These fractions count nodes, not area: no nodal areas were "
            + "matched for this interface. Re-mesh to weight them by area.")
        : null,
      r.flatness < 0.98
        ? el("div", { class: "hint warn" },
            `\u26a0 This interface is not flat (${r.flatness.toFixed(3)}). One `
            + "normal is used for the whole face, so pressure and shear are "
            + "mixed where it curves.")
        : null,
      !r.held
        ? el("div", { class: "btnrow" },
            el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
              const c = (S.project.setup.contacts || [])[r.index - 1];
              if (c) c.solve = "nonlinear";
            }) }, "Switch this contact to nonlinear"))
        : null));
  }
  if (!(R.rows || []).length) {
    secs.push(sec(null, el("div", { class: "hint" },
      "No frictional interface was solved as stuck, so there is nothing to "
      + "check.")));
  }
  return secs;
}

/**
 * Shock response.
 *
 * Every number here is a PEAK with no sign and no time attached — an SRS
 * carries neither. Two of them are each defensible on their own; their ratio
 * is not, because they do not have to happen at the same instant.
 */
function shockSections(S, A, a) {
  const R = derived(S, "shockResults", a.id);
  if (!R) {
    A.loadShock(a.id);
    return [sec("Shock response", el("div", { class: "hint" }, "Computing\u2026"))];
  }
  const secs = [];
  for (const w of R.warnings || []) {
    secs.push(sec(null, el("div", { class: "hint warn" }, "\u26a0 " + w)));
  }

  secs.push(sec("Peak interface load",
    dl([["Along", R.axis],
        ["Total", `${fmtVal(R.force_N)} N`],
        ["Rigid (summed)", `${fmtVal(R.force_rigid_N)} N`],
        ["Periodic (combined)", `${fmtVal(R.force_periodic_N)} N`],
        ["Rigid share", `${(100 * (R.rigid_share ?? 0)).toFixed(0)} %`],
        ["Missing mass", `${(100 * R.missing_mass).toFixed(1)} % \u2192 ` +
                         `${fmtVal(R.missing_force_N)} N at ZPA`],
        ["Effective mass captured", `${(100 * R.mass_captured).toFixed(1)} %`],
        ["Combination", (R.rule || "srss").toUpperCase()],
        ["Input", R.input?.source || "\u2014"],
        ["ZPA", `${fmtVal(R.input?.zpa ?? 0)} g`]]),
    el("div", { class: "hint" },
      "Modes at the ZPA ride with the base rather than resonating. Those "
      + "responses are in phase and add algebraically; the resonant ones peak "
      + "at different instants and are combined statistically. The two are "
      + "then combined as \u221a(rigid\u00b2 + periodic\u00b2) \u2014 US NRC "
      + "RG 1.92 Rev. 2. Treating the whole basis as periodic understates a "
      + "high-frequency shock badly.")));

  if (R.bolts?.length) {
    secs.push(sec("Peak bolt loads",
      el("table", { class: "rtable" },
        el("tr", {}, ["Bolt", "End", "Axial N", "Shear N", "Moment N\u00b7mm"]
          .map((h) => el("th", {}, h))),
        R.bolts.map((b) => el("tr", {},
          el("td", {}, b.name || `Bolt ${b.bolt}`),
          el("td", {}, b.end),
          el("td", {}, fmtVal(b.N)),
          el("td", {}, fmtVal(b.V)),
          el("td", {}, fmtVal(b.M)))))));
  }

  if (R.probes?.length) {
    const names = S.project.setup.probes || [];
    secs.push(sec("Peak displacement at probes",
      el("table", { class: "rtable" },
        el("tr", {}, ["Probe", "|u| mm", "X", "Y", "Z"].map((h) => el("th", {}, h))),
        R.probes.map((p) => {
          const n = Number((p.probe.match(/\d+/) || [])[0]);
          return el("tr", {},
            el("td", {}, names[n - 1]?.name || p.probe),
            el("td", {}, fmtVal(p.mag)),
            el("td", {}, fmtVal(p.dx)),
            el("td", {}, fmtVal(p.dy)),
            el("td", {}, fmtVal(p.dz)));
        }))));
  }

  if (R.curve?.freq?.length) {
    const canvas = el("canvas", { class: "frfbig" });
    secs.push(sec("Input spectrum (g)", canvas));
    requestAnimationFrame(() => {
      if (!canvas.isConnected) return;
      const curves = [{ label: "input SRS", freq: R.curve.freq,
                        module: R.curve.srs, color: seriesColor(0) }];
      if (R.rows?.length) {
        // where the modes actually land on it — the whole answer comes from
        // these points, not from the curve between them
        curves.push({ label: "modes", freq: R.rows.map((r) => r.f),
                      module: R.rows.map((r) => r.srs_g),
                      color: "#e0803c", dots: true });
      }
      frfPlot(canvas, curves, { annotate: false });
    });
  }

  if (R.rows?.length) {
    secs.push(sec("Per-mode contribution",
      el("table", { class: "rtable" },
        el("tr", {}, ["#", "Hz", `m_eff ${R.axis}`, "SRS g", "rigid \u03b1",
                      "Force N"].map((h) => el("th", {}, h))),
        R.rows.map((r) => el("tr", {},
          el("td", {}, String(r.mode)),
          el("td", {}, fmtVal(r.f)),
          el("td", {}, `${(100 * r.eff_frac).toFixed(1)} %`),
          el("td", {}, fmtVal(r.srs_g)),
          el("td", {}, (r.alpha ?? 0).toFixed(2)),
          el("td", {}, fmtVal(r.force_N)))))));
  }
  return secs;
}

function randomSections(S, A, a) {
  const R = derived(S, "randomResults", a.id);
  if (!R) {
    A.loadRandom(a.id);
    return [sec("Random response", el("div", { class: "hint" }, "Computing…"))];
  }
  const secs = [];
  const worst = R.curves.reduce((m, c) => (c.grms > (m?.grms ?? -1) ? c : m), null);
  const probes = S.project.setup.probes;
  const nameOf = (c) => `${probes[c.probe - 1]?.name || "P" + c.probe}·${c.comp}`;

  secs.push(sec("Random response",
    el("table", { class: "rtable" },
      el("tr", {}, ["Probe", "g RMS", "3σ (g)"].map((t) => el("th", {}, t))),
      R.curves.map((c) => el("tr", {},
        el("td", {}, nameOf(c)),
        el("td", {}, c.grms.toFixed(2)),
        el("td", {}, c.three_sigma.toFixed(2))))),
    dl([["Input", `${R.grms_in.toFixed(2)} g RMS`],
        ["Worst response", `${worst ? worst.grms.toFixed(2) : "\u2014"} g RMS`],
        ["Amplification",
         worst && R.grms_in ? `${(worst.grms / R.grms_in).toFixed(1)}\u00d7` : "\u2014"]])));

  // response PSD plot
  const canvas = el("canvas", { class: "frfbig" });
  secs.push(sec("Response PSD (g²/Hz)", canvas));
  requestAnimationFrame(() => {
    if (!canvas.isConnected) return;
    const curves = R.curves.map((c, i) => ({
      label: nameOf(c), freq: c.freq, module: c.psd_out, color: seriesColor(i),
    }));
    if (R.curves[0]) {
      curves.push({ label: "input", freq: R.curves[0].freq,
                    module: R.curves[0].psd_in, color: "#8899aa" });
    }
    frfPlot(canvas, curves, { annotate: false });
  });

  // Miles cross-check
  if (R.miles?.length) {
    secs.push(sec("Miles' equation cross-check",
      el("table", { class: "rtable" },
        el("tr", {}, ["fn (Hz)", "PSD @ fn", "g RMS", "3σ"].map((t) => el("th", {}, t))),
        R.miles.filter((m) => m.psd_at_fn > 0).map((m) => el("tr", {},
          el("td", {}, fmtVal(m.fn)),
          el("td", {}, m.psd_at_fn.toFixed(4)),
          el("td", {}, m.grms.toFixed(2)),
          el("td", {}, m.three_sigma.toFixed(2))))),
      ));
  }

  // truncation check — warn, do not block
  const part = R.participation;
  if (part && part.length) {
    const last = part[part.length - 1];
    const dir = a.config.base_dir || [0, 0, 1];
    const k = dir.indexOf(Math.max(...dir.map(Math.abs))) >= 0
      ? [Math.abs(dir[0]), Math.abs(dir[1]), Math.abs(dir[2])].indexOf(
          Math.max(Math.abs(dir[0]), Math.abs(dir[1]), Math.abs(dir[2]))) : 2;
    const pct = (last[k] || 0) * 100;
    secs.push(sec("Modal truncation",
      dl([["Cumulative effective mass", `${pct.toFixed(1)} %`],
          ["Modes retained", String(part.length)]]),
      pct < 90 ? el("div", { class: "hint warn" },
        `\u26a0 Retained modes carry only ${pct.toFixed(1)} % of the effective mass ` +
        `in the drive direction. Base excitation acts through inertia, so the ` +
        `missing mass makes this result LOW. Extract more modes (raise f max) ` +
        `before trusting the RMS.`)
        : null));
  }
  return secs;
}

/**
 * Deformation scale.
 *
 * Auto-scale exaggerates to about 5 % of the model diagonal, which on a stiff
 * part can be a factor of thousands — useful for seeing the shape, useless
 * for judging whether a clearance closes. 1x (true scale) is therefore a
 * distinguished value: it is labelled, it is one click away, and the slider
 * snaps to it so you can find it by dragging.
 */
function deformControl(S, A, cur) {
  const R = cur || S.activeResult;
  const mult = R?.defMult ?? 1;
  const auto = R?.autoScale || 0;
  const trueMult = auto > 0 ? 1 / auto : null;      // slider value giving x1
  const total = auto * mult;

  const slider = el("input", {
    type: "range", min: -2, max: 3, step: 0.001,
    value: String(Math.log10(Math.max(mult, 1e-6))),
    oninput: (e) => {
      let m = Math.pow(10, Number(e.target.value));
      // snap to true scale within a few percent of it
      if (trueMult && Math.abs(Math.log10(m / trueMult)) < 0.04) m = trueMult;
      A.setDeform(m);
    },
  });

  return el("div", {},
    el("label", { class: "frm" },
      `Deformation ×${fmtVal(total)}${trueMult && Math.abs(mult - trueMult) < 1e-9 ? " — true scale" : ""}`,
      slider),
    el("div", { class: "btnrow" },
      trueMult ? el("button", { class: "btn btn-small",
        onclick: () => A.setDeform(trueMult) }, "True scale (1×)") : null,
      el("button", { class: "btn btn-small", onclick: () => A.setDeform(1) }, "Auto"),
      el("button", { class: "btn btn-small", onclick: () => A.setDeform(0) }, "Undeformed")),
  );
}

export function compOptions(f) {
  if (f.kind === "DEPL") {
    return [["MAG", "Magnitude |u|"], ...["DX", "DY", "DZ"]
      .filter((c) => f.comps.includes(c)).map((c) => [c, c.replace("D", "U")])];
  }
  const nice = { VMIS: "von Mises", VMIS_SG: "signed von Mises", TRESCA: "Tresca",
                 PRIN_1: "principal σ1", PRIN_2: "principal σ2", PRIN_3: "principal σ3",
                 SIXX: "σxx", SIYY: "σyy", SIZZ: "σzz", SIXY: "σxy", SIXZ: "σxz", SIYZ: "σyz" };
  return f.comps.filter((c) => c).map((c) => [c, nice[c] || c]);
}

function partTable(part) {
  const cols = part.columns;
  const fi = cols.indexOf("FREQ");
  const dx = cols.indexOf("MASS_EFFE_UN_DX");
  if (fi < 0 || dx < 0) return null;
  // MASS_EFFE_UN_D* is code_aster's UNITARY effective mass: already a
  // fraction of the model's mass. Dividing it by the total mass again — which
  // this did — printed percentages over 100 whenever the model weighed less
  // than a tonne. The cumulative-participation check elsewhere reads the same
  // column as a fraction, so the two disagreed.
  const th = ["#", "Hz", "mX", "mY", "mZ"];
  const t = el("table", { class: "rtable" },
    el("tr", {}, th.map((h) => el("th", {}, h))),
    part.rows.map((r, i) => el("tr", {},
      el("td", {}, String(i + 1)),
      el("td", {}, fmtVal(r[fi] ?? 0)),
      ...[0, 1, 2].map((k) => {
        const v = r[dx + k];
        return el("td", {}, v != null ? `${(100 * v).toFixed(1)} %` : "—");
      }))));
  return el("div", { style: "margin-top:9px" },
    el("span", { class: "lbl" }, "Effective mass"), t);
}
