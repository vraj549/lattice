// Tree + context-panel rendering. Pure DOM, no framework.
import { fmtVal, contourStyle } from "./colormap.js";
import { frfChart, frfPlot, findPeaks, seriesColor } from "./charts.js";
import { defaultReferenceFace, describeFace, candidateTargets } from "./pattern.js";

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


export const TYPE_SHORT = { static: "static", modal: "modal",
                     harmonic: "harmonic", random: "random",
                     shock: "shock" };

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
  if (a.type === "random") return `PSD ${gramsOf(c.spec || [])} g · ${ax}`;
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
  if (res && res.stale) {
    return { dot: "warn", title: "Results are OUT OF DATE",
      badge: { text: "!", cls: "stale",
        title: "OUT OF DATE — the mesh, materials, connections or boundary "
             + "conditions changed after this ran. Re-run before using these "
             + "numbers." } };
  }
  if (res) {
    return { dot: "ok", title: "Results are current for this model",
      badge: { text: "✓", cls: "ok", title: "Results are current for this model" } };
  }
  if (st === "failed") {
    return { dot: "bad", title: "The last run failed — see Job output",
      badge: { text: "✕", cls: "bad", title: "The last run failed — see Job output" } };
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
    out.push({ what: "contours", label: "Contours", meta: `${fields.length} field${fields.length > 1 ? "s" : ""}` });
  }
  const modes = meta.tables?.modes?.[0];
  if (modes && a.type !== "static") {
    out.push({ what: "modes", label: "Modes", meta: `${modes.rows.length}` });
  }
  if (meta.frf?.length) {
    out.push({ what: "frf", label: "Frequency response", meta: `${meta.frf.length} curve${meta.frf.length > 1 ? "s" : ""}` });
  }
  if (a.type === "random") {
    out.push({ what: "random", label: "Random response", meta: "g RMS" });
  }
  if (a.type === "shock") {
    out.push({ what: "shock", label: "Shock response", meta: "peak" });
  }
  if (a.type === "static" && meta.tables?.contact_check?.length) {
    out.push({ what: "slip", label: "Slip check", meta: "friction" });
  }
  if (meta.tables?.bolt_forces?.length) {
    out.push({ what: "bolts", label: "Bolt forces", meta: "N" });
    out.push({ what: "sizing", label: "Bolt sizing", meta: "preload" });
  }
  if (a.type === "static" && reactionRow(meta)) {
    out.push({ what: "reactions", label: "Reactions", meta: "N" });
  }
  if (meta.warnings?.length) {
    out.push({ what: "warnings", label: "Solver messages", meta: String(meta.warnings.length) });
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

/**
 * Everything about the current mesh that no longer matches the model.
 *
 * Boundary conditions become mesh groups, and bolts and probes become actual
 * elements and nodes, so all three are baked in at mesh time. Patterning a
 * bolt across a flange is the fastest way to get five joints that exist in
 * the tree and in none of the matrices — the load path would silently not be
 * there, which is worse than a failed run.
 */
export const MESH_FORMAT = 2;

export function meshIssues(S) {
  const stats = S.meshData?.stats;
  if (!stats) return [];
  const out = [];

  // A mesh from an older build may be unusable rather than merely stale.
  if ((stats.mesh_format || 0) < MESH_FORMAT) {
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
    case "connections": return panelConnections(S, A, put);
    case "support": return panelSupport(S, A, put, id);
    case "load": return panelLoad(S, A, put, id);
    case "bolt": return panelBolt(S, A, put, id);
    case "tie": return panelTie(S, A, put, id);
    case "contact": return panelContact(S, A, put, id);
    case "probe": return panelProbe(S, A, put, id);
    case "mesh": return panelMesh(S, A, put);
    case "analysis": return panelAnalysis(S, A, put, id);
    case "settings": return panelSettings(S, A, put, id);
    case "solution": return panelSolution(S, A, put, id);
    case "result": return panelResult(S, A, put, id);
    default: return panelModel(S, A, put);
  }
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
  const explains = body.some((k) => k?.classList?.contains?.("hint")
    && !k.classList.contains("warn") && !k.classList.contains("bad")
    && !k.classList.contains("good"));
  return el("div", { class: "sec" },
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
function selInput(label, value, options, onchange) {
  const s = el("select", { onchange: (e) => onchange(e.target.value) },
    options.map(([v, t]) => {
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
    el("div", { class: "hint" }, n ? `${n} face(s) assigned — shown highlighted in the viewport.`
                                   : "Click faces in the viewport, then press Done."));
}

// ---------- model / solids / connections ----------

function panelModel(S, A, put) {
  const geo = S.project.geometry;
  put("Model", S.project.name,
    sec("Geometry", dl([
      ["Solids", geo.solids.length],
      ["Faces", geo.faces.length],
      ["Bonded interfaces", geo.interfaces.length],
      ["Bounding box", `${fmtVal(geo.bbox[3] - geo.bbox[0])} × ${fmtVal(geo.bbox[4] - geo.bbox[1])} × ${fmtVal(geo.bbox[5] - geo.bbox[2])} mm`],
    ])),
    sec("Workflow", el("div", { class: "hint" },
      "1. Assign a material to every solid · 2. Add supports and loads · " +
      "3. Generate the mesh · 4. Add an analysis and run it.")),
    validation(S));
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
    ...setup.materials.filter((m) => !m.id.startsWith("lib-")).map((m) => [m.id, `${m.name} (custom)`])];

  const hidden = S.hiddenSolids.has(s.tag);
  const custom = setup.materials.filter((m) => !m.id.startsWith("lib-"));
  put(solidName(S, tag), `${s.faces.length} faces`,
    sec("Definition",
      textInput("Name", solidName(S, tag), (v) => A.renameSolid(s.tag, v))),
    sec("Material",
      selInput("Assign material", mid.startsWith("custom") ? mid : (mid ? `lib:${findLib(S, mid)}` : ""), opts,
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

function findLib(S, mid) {
  const m = S.project.setup.materials.find((x) => x.id === mid);
  return m?.lib || mid;
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

function panelConnections(S, A, put) {
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
        `Cylinder detected: ⌀${fmtVal(holeD)} mm hole`) : null),
    sec("Bolt",
      selGroups("Nominal size", bl.size ?? size?.id ?? "",
        [["", [["", "— choose —"]]],
         ["Metric (ISO coarse)", BOLT_SIZES.filter((s) => s.series === "metric")
           .map((s) => [s.id, s.label])],
         ["Unified (inch)", BOLT_SIZES.filter((s) => s.series === "unified")
           .map((s) => [s.id, s.label])]],
        (v) => A.mutate(() => { applyBoltSize(bl, v); })),
      holeD && !size ? el("div", { class: "hint" },
        `Hole ⌀${fmtVal(holeD)} suggests ${nearestBolt(holeD)?.label || "—"}`) : null,
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

/** Largest size that still clears the hole, across both series. */
function nearestBolt(holeD) {
  let best = null;
  for (const s of BOLT_SIZES) {
    if (s.d <= holeD - 0.15 && (!best || s.d > best.d)) best = s;
  }
  return best;
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
            ? "A friction joint is designed to stay STUCK, and a stuck "
              + "frictional interface is the same constraint as a bonded one — "
              + "so this glues it, solves linearly, and then reads the "
              + "interface tractions back to check that friction was actually "
              + "enough. If it was, this IS the nonlinear answer. If it was "
              + "not, the result says where it slipped and by how much."
            : "Solves the sliding itself: the load is stepped up and the "
              + "contact status iterated. Use this once the check says the "
              + "joint slips and you need to know how far.")
        : null),
    sec("Faces", dl([
      ["Side A", `${(c.faces_a || []).length} face(s) on ${nameOf((c.solids || [])[0])}`],
      ["Side B", `${(c.faces_b || []).length} face(s) on ${nameOf((c.solids || [])[1])}`],
      ["Interface area", c.area ? `${fmtVal(c.area)} mm²` : "—"],
    ]),
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
          const a = c.faces_a; c.faces_a = c.faces_b; c.faces_b = a;
          c.solids = [(c.solids || [])[1], (c.solids || [])[0]];
        }) }, "Swap sides"),
        el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
          c.suppressed = !c.suppressed;
        }) }, c.suppressed ? "Un-suppress" : "Suppress")),
      el("div", { class: "hint" },
        "Side B is the slave — give that side the finer mesh. Suppressing a "
        + "contact leaves the parts free of each other entirely.")),
    sliding
      ? sec(null, el("div", { class: "hint warn" },
          "⚠ This makes the solve NONLINEAR: whether the surfaces touch is "
          + "part of the answer, so the run steps the load up and iterates. "
          + "Expect it to take considerably longer, and it applies to static "
          + "only — modal and harmonic are linear by definition and use the "
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
    sec(null, dupRow(A, "probes", id, "probe"),
        delBtn("probe", () => A.removeItem("probes", id))));
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
      numInput(`Target element size (mm) — auto ≈ ${fmtVal(diag / 25)}`, m.size_mm,
        (v) => A.mutate(() => { m.size_mm = v; })),
      numInput("Curvature refinement (elements per 2π)", m.curvature,
        (v) => A.mutate(() => { m.curvature = v || 16; })),
      selInput("Element order", String(m.order),
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
        ? [["Elements of", Object.entries(stats.element_kinds)
              .map(([k, v]) => `${v.toLocaleString()} ${k}`).join(", ")]] : []),
      ...(stats.quality_min != null
        ? [["Quality min/avg (SICN)", `${stats.quality_min.toFixed(2)} / ${stats.quality_avg.toFixed(2)}`]] : []),
      ["Mesh time", `${stats.wall_s}s`],
      ["Est. solve memory", `${stats.mem_gb_est} GB`],
    ])));
    if (stats.mem_gb_est > memLimit * 0.8) {
      secs.push(sec(null, el("div", { class: "hint warn" },
        `⚠ Estimated factorization memory (${stats.mem_gb_est} GB) is close to the ` +
        `solver limit (${memLimit} GB). Consider a coarser mesh.`)));
    }
    if (stats.islands > 1) {
      secs.push(sec(null, el("div", { class: "hint bad" },
        `⚠ Mesh has ${stats.islands} disconnected part groups — unconstrained parts ` +
        "will produce rigid-body modes / singular static solves.")));
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
           config: { f_min: 20, f_max: 2000, n_steps: 150, spacing: "log",
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

function resolutionWarning(S, peaks) {
  const a = S.project.setup.analyses.find((x) => x.id === S.activeResult?.aid)
         || S.project.setup.analyses.find((x) => x.type === "harmonic");
  const c = a?.config;
  if (!c || !peaks.length) return null;
  const decades = Math.log10((c.f_max || 1) / Math.max(c.f_min || 1, 1e-9));
  const perDecade = (c.n_steps || 1) / Math.max(decades, 1e-9);
  const stepPct = c.spacing === "log"
    ? (Math.pow(10, 1 / perDecade) - 1) * 100
    : ((c.f_max - c.f_min) / (c.n_steps || 1)) / Math.max(peaks[0].f, 1e-9) * 100;
  const zeta = c.damping || 0.02;
  const bandPct = 100 / (2 * (1 / (2 * zeta)) ) * 2;   // half-power width = f/Q
  const ptsInBand = bandPct / Math.max(stepPct, 1e-9);
  if (ptsInBand >= 5) return null;
  const need = Math.ceil((c.n_steps || 1) * (5 / Math.max(ptsInBand, 1e-9)));
  return el("div", { class: "hint warn" },
    `\u26a0 Only ~${ptsInBand.toFixed(1)} sweep points fall inside a ` +
    `half-power band at \u03b6=${zeta}. Q is under-reported and peak ` +
    `amplitude is missed. Use about ${need} steps (or sweep a narrow band ` +
    `around each mode) to resolve resonances.`);
}

/** Everything that configures HOW the study is solved.
 *
 *  Split out of the analysis panel and given its own tree node, the way Ansys
 *  and SimScale both do it: the analysis row answers "can I run this", and the
 *  settings row answers "what exactly am I running". They were one panel, and
 *  it had grown to a screen and a half of scrolling. */
/**
 * Which solver runs this study.
 *
 * They are not interchangeable. code_aster covers every analysis type here;
 * CalculiX installs from a package manager and so is often the only one
 * present on macOS, but this tool only drives it for static and modal. An
 * engine that cannot run the study is offered disabled with the reason rather
 * than hidden, so the limitation is legible instead of mysterious.
 */
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

function engineSection(S, A, a, c) {
  const engines = S.config?.solver?.engines || [];
  if (!engines.length) {
    return sec("Solver", el("div", { class: "hint bad" },
      "No solver detected. See README \u2192 Solver setup."));
  }
  const current = engineOf(S, a);
  const blockers = engineBlockers(S, a, current);
  const alt = engineThatCanRun(S, a);

  return sec("Solver",
    selInput("Engine", current,
      engines.map((e) => {
        const bad = engineBlockers(S, a, e.id).length;
        return [e.id, bad ? `${e.label} \u2014 cannot run this` : e.label];
      }),
      (v) => A.mutate(() => { c.engine = v; })),
    el("div", { class: "hint" },
      engines.find((e) => e.id === current)?.detail || ""),
    ...blockers.map((t) => el("div", { class: "hint bad" }, "\u26a0 " + t)),
    blockers.length && alt && alt !== current
      ? el("div", { class: "btnrow" },
          el("button", { class: "btn btn-accent",
            onclick: () => A.mutate(() => { c.engine = alt; }) },
            `Switch to ${ENGINE_LABEL[alt] || alt}`))
      : null,
    el("div", { class: "hint" },
      "The model is the same either way \u2014 geometry, mesh, materials, "
      + "supports and loads are shared. Switching engines re-runs the same "
      + "setup and marks existing results out of date."));
}

function panelSettings(S, A, put, id) {
  const a = S.project.setup.analyses.find((x) => x.id === id);
  if (!a) return put("Analysis Settings", "");
  const c = a.config || {};
  const secs = [engineSection(S, A, a, c)];

  if (a.type === "modal") {
    secs.push(sec("Extraction",
      numInput("Number of modes", c.n_modes, (v) => A.mutate(() => { c.n_modes = v || 10; }), { min: 1, max: 100 }),
      el("div", { class: "hint" }, "Lowest modes above the supports (Sorensen/ARPACK).")));
  }
  if (a.type === "harmonic") {
    secs.push(sec("Excitation",
      selInput("Driven by", c.excitation || "force",
        [["force", "Force (loads in the tree)"],
         ["base", "Base acceleration (shaker)"]],
        (v) => A.mutate(() => { c.excitation = v; })),
      c.excitation === "base" ? el("div", {},
        el("div", { class: "frm-row" },
          numInput("dir X", c.base_dir?.[0] ?? 0, (v) => A.mutate(() => { c.base_dir = [v || 0, c.base_dir?.[1] ?? 0, c.base_dir?.[2] ?? 1]; })),
          numInput("dir Y", c.base_dir?.[1] ?? 0, (v) => A.mutate(() => { c.base_dir = [c.base_dir?.[0] ?? 0, v || 0, c.base_dir?.[2] ?? 1]; })),
          numInput("dir Z", c.base_dir?.[2] ?? 1, (v) => A.mutate(() => { c.base_dir = [c.base_dir?.[0] ?? 0, c.base_dir?.[1] ?? 0, v ?? 1]; }))),
        numInput("Input amplitude (g)", c.base_g ?? 1, (v) => A.mutate(() => { c.base_g = v ?? 1; })),
        el("div", { class: "hint" },
          "Every fixed support becomes the moving fixture (mono-support). " +
          "Forces in the tree are ignored. Drive at 1 g and the plot reads " +
          "directly as transmissibility."),
        el("div", { class: "hint" },
          "Response is RELATIVE to the base — that is what stresses the part. " +
          "Absolute acceleration adds the base motion back, which the " +
          "transmissibility view does for you."),
      ) : null),
    sec("Sweep",
      el("div", { class: "frm-row2" },
        numInput("f min (Hz)", c.f_min, (v) => A.mutate(() => { c.f_min = v || 1; })),
        numInput("f max (Hz)", c.f_max, (v) => A.mutate(() => { c.f_max = v || 1000; }))),
      el("div", { class: "frm-row2" },
        numInput("Steps", c.n_steps, (v) => A.mutate(() => { c.n_steps = v || 100; })),
        selInput("Spacing", c.spacing, [["log", "Logarithmic"], ["lin", "Linear"]],
          (v) => A.mutate(() => { c.spacing = v; }))),
      numInput("Modal damping ratio ζ", c.damping, (v) => A.mutate(() => { c.damping = v ?? 0.02; }),
        { min: 0, max: 1, step: 0.005 }),
      el("div", { class: "hint" },
        "Modal superposition on a basis up to 1.6 × f max; response is read " +
        "at the probes."),
      // Guidance follows the excitation actually selected. Showing the
      // force-driven advice next to a base-driven setup was worse than
      // showing nothing.
      c.excitation === "base"
        ? el("div", { class: "hint" },
            "Shaker qualification specifies base acceleration, which is what " +
            "this sweep applies. At 1 g the response curve reads directly as " +
            "transmissibility, and its peaks give fₙ and Q for a " +
            "Miles'-equation random estimate.")
        : el("div", { class: "hint" },
            "This is a FORCE-driven sweep. The analysis is linear, so response " +
            "scales exactly with input: use 1 N to read the result directly as a " +
            "transfer function. Peak frequencies and Q do not depend on the " +
            "magnitude you enter."),
      c.excitation === "base" ? null : el("div", { class: "hint warn" },
        "⚠ Shaker qualification specifies BASE acceleration, not force. " +
        "Switch “Driven by” to base acceleration to sweep the way the test " +
        "is actually run.")));
    secs.push(sec("Field export",
      textInput("Frequencies (Hz, comma-separated — optional)",
        (c.field_freqs || []).join(", "),
        (v) => A.mutate(() => {
          c.field_freqs = v.split(",").map((x) => Number(x.trim())).filter((x) => x > 0);
        })),
      el("div", { class: "hint" },
        "Exports full displacement fields at these frequencies for contour viewing.")));
    if (!S.project.setup.probes.length) {
      secs.push(sec(null, el("div", { class: "hint warn" },
        "⚠ Add at least one probe — FRF curves are extracted at probe locations.")));
    }
  }
  if (a.type === "random") {
    const spec = c.spec || [];
    const specTable = el("table", { class: "rtable psd" },
      el("tr", {},
        el("th", {}, "Hz"), el("th", {}, "g²/Hz"), el("th", {}, "")),
      spec.map((rowv, i) => el("tr", {},
        el("td", {}, el("input", {
          type: "number", step: "any", value: rowv[0],
          oninput: (e) => { spec[i][0] = Number(e.target.value) || 0; A.saveOnly(); } })),
        el("td", {}, el("input", {
          type: "number", step: "any", value: rowv[1],
          oninput: (e) => { spec[i][1] = Number(e.target.value) || 0; A.saveOnly(); } })),
        el("td", {}, el("button", {
          class: "btn btn-small btn-danger",
          onclick: () => A.mutate(() => { spec.splice(i, 1); }) }, "✕")))));

    secs.push(sec("Input spectrum",
      specTable,
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
          const last = spec[spec.length - 1] || [20, 0.01];
          spec.push([Math.round(last[0] * 2), last[1]]);
        }) }, "+ row"),
        el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
          c.spec = [[20, 0.01], [80, 0.04], [350, 0.04], [2000, 0.007]];
        }) }, "Typical spec"),
        el("button", { class: "btn btn-small", onclick: () => A.pasteSpec(c) }, "Paste…")),
      el("div", { class: "hint" },
        "Log-log interpolated between rows, zero outside — the standard " +
        "qualification-spec format. Rows sort themselves by frequency."),
      el("div", { class: "hint good" }, `Input overall: ${gramsOf(spec)} g RMS`)));

    secs.push(sec("Direction and damping",
      el("div", { class: "frm-row" },
        numInput("dir X", c.base_dir?.[0] ?? 0, (v) => A.mutate(() => { c.base_dir = [v || 0, c.base_dir?.[1] ?? 0, c.base_dir?.[2] ?? 1]; })),
        numInput("dir Y", c.base_dir?.[1] ?? 0, (v) => A.mutate(() => { c.base_dir = [c.base_dir?.[0] ?? 0, v || 0, c.base_dir?.[2] ?? 1]; })),
        numInput("dir Z", c.base_dir?.[2] ?? 1, (v) => A.mutate(() => { c.base_dir = [c.base_dir?.[0] ?? 0, c.base_dir?.[1] ?? 0, v ?? 1]; }))),
      numInput("Modal damping ratio ζ", c.damping, (v) => A.mutate(() => { c.damping = v ?? 0.02; }),
        { min: 0, max: 1, step: 0.005 }),
      numInput("Sweep steps", c.n_steps, (v) => A.mutate(() => { c.n_steps = v || 600; })),
      el("div", { class: "hint" },
        "Solved as a 1 g base sweep across the spectrum; the response PSD is " +
        "|T(f)|² × input PSD, integrated for g RMS. Resolution matters — a " +
        "resonance spread over too few points under-reports the RMS.")));
    if (!S.project.setup.probes.length) {
      secs.push(sec(null, el("div", { class: "hint warn" },
        "⚠ Add at least one probe — response is extracted there.")));
    }
  }
  if (a.type === "shock") {
    const pulse = (c.input || "spectrum") === "pulse";
    secs.push(sec("Input",
      selInput("Specified as", c.input || "spectrum",
        [["pulse", "Classical pulse"], ["spectrum", "SRS table"]],
        (v) => A.mutate(() => { c.input = v; }))));

    if (pulse) {
      secs.push(sec("Pulse",
        selInput("Shape", c.pulse || "half_sine",
          [["half_sine", "Half-sine"], ["sawtooth", "Terminal-peak sawtooth"],
           ["trapezoid", "Trapezoid"]],
          (v) => A.mutate(() => { c.pulse = v; })),
        el("div", { class: "frm-row2" },
          numInput("Amplitude (g)", c.pulse_g,
            (v) => A.mutate(() => { c.pulse_g = v ?? 20; }), { step: 1 }),
          numInput("Duration (ms)", c.pulse_ms,
            (v) => A.mutate(() => { c.pulse_ms = v ?? 11; }), { step: 0.5 })),
        el("div", { class: "hint" },
          "Its spectrum is computed and applied — the pulse is not integrated " +
          "in time. MIL-STD-810 Method 516 shapes.")));
    } else {
      // An analysis can reach here without a spec — created through the API,
      // or switched over from a pulse. Show the table anyway; "Typical SRS"
      // is the way back from empty.
      const spec = c.spec || (c.spec = []);
      secs.push(sec("Input spectrum",
        el("table", { class: "rtable psd" },
          el("tr", {}, el("th", {}, "Hz"), el("th", {}, "g"), el("th", {}, "")),
          spec.map((rowv, i) => el("tr", {},
            el("td", {}, el("input", {
              type: "number", step: "any", value: rowv[0],
              oninput: (e) => { spec[i][0] = Number(e.target.value) || 0; A.saveOnly(); } })),
            el("td", {}, el("input", {
              type: "number", step: "any", value: rowv[1],
              oninput: (e) => { spec[i][1] = Number(e.target.value) || 0; A.saveOnly(); } })),
            el("td", {}, el("button", {
              class: "btn btn-small btn-danger",
              onclick: () => A.mutate(() => { spec.splice(i, 1); }) }, "\u2715"))))),
        el("div", { class: "btnrow" },
          el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
            const last = spec[spec.length - 1] || [100, 20];
            spec.push([Math.round(last[0] * 2), last[1]]);
          }) }, "+ row"),
          el("button", { class: "btn btn-small", onclick: () => A.mutate(() => {
            c.spec = [[100, 20], [1000, 200], [10000, 200]];
          }) }, "Typical SRS"),
          el("button", { class: "btn btn-small", onclick: () => A.pasteSpec(c) }, "Paste\u2026")),
        el("div", { class: "hint" },
          "Log-log between rows. Outside the table the end value is HELD, not " +
          "zeroed \u2014 an SRS ends at its plateau, and every stiffer mode sees it.")));
    }

    secs.push(sec("Direction and combination",
      selInput("Axis", String(c.axis ?? 2),
        [["0", "X"], ["1", "Y"], ["2", "Z"]],
        (v) => A.mutate(() => { c.axis = Number(v); })),
      selInput("Mode combination", c.rule || "srss",
        [["srss", "SRSS \u2014 modes independent"],
         ["nrl", "NRL \u2014 largest at full value"],
         ["abs", "Absolute sum \u2014 upper bound"]],
        (v) => A.mutate(() => { c.rule = v; })),
      el("div", { class: "frm-row2" },
        numInput("Spectrum damping \u03b6", c.damping,
          (v) => A.mutate(() => { c.damping = v ?? 0.05; }),
          { min: 0, max: 1, step: 0.005 }),
        numInput("Modes", c.n_modes,
          (v) => A.mutate(() => { c.n_modes = v || 30; }), { min: 1, step: 1 })),
      el("div", { class: "hint" },
        `\u03b6 = ${c.damping ?? 0.05} is Q = ${(1 / (2 * (c.damping || 0.05))).toFixed(0)}. ` +
        "A spectrum read at one Q and applied at another is a different spectrum."),
      el("div", { class: "hint" },
        "Extract enough modes to carry the effective mass in the driven axis. " +
        "What the basis misses is added back at the ZPA, but a large residual " +
        "means the shape of the response is not resolved.")));
    if (!S.project.setup.probes.length) {
      secs.push(sec(null, el("div", { class: "hint" },
        "Add probes to get peak response at specific points. Interface load " +
        "and bolt loads do not need them.")));
    }
  }
  if (a.type === "static") {
    secs.push(sec("Output", el("div", { class: "hint" },
      "Displacement, von Mises / principal stresses, stress tensor, and reaction " +
      "forces at the supports.")));
  }
  secs.push(sec(null, el("div", { class: "btnrow" },
    el("button", { class: "btn", onclick: () => A.select("analysis", a.id) },
      "Back to analysis"))));
  put("Analysis Settings", a.name || a.type, ...secs);
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
                    "which is not installed here. Change it in Analysis Settings.");
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
  if (a.type === "shock") {
    const c = a.config || {};
    if ((c.input || "spectrum") === "spectrum" && (c.spec || []).length < 2) {
      blockers.push("The shock spectrum needs at least two breakpoints.");
    }
    if ((c.input || "spectrum") === "pulse" && !(c.pulse_g > 0 && c.pulse_ms > 0)) {
      blockers.push("The pulse needs an amplitude and a duration.");
    }
    if (!(a.supports || []).length) {
      // The spectrum is applied AT the base. With nothing restrained there is
      // no base, and the modes are free-free — the answer would be nonsense
      // rather than merely inaccurate.
      blockers.push("Add a support — a shock spectrum is applied at the "
                    + "restrained base.");
    }
  }
  return blockers;
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
      // the engine belongs here, not buried in settings: it is the first
      // thing you check when a run behaves differently from yesterday
      selInput("Solver", engineOf(S, a),
        (S.config?.solver?.engines || []).map((e) => {
          const bad = engineBlockers(S, a, e.id).length;
          return [e.id, bad ? `${e.label} — cannot run this` : e.label];
        }),
        (v) => A.mutate(() => { (a.config ||= {}).engine = v; }))),
    sec(null,
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-accent", disabled: running || blockers.length > 0,
          onclick: () => A.runAnalysis(a.id) }, running ? "Running…" : "Run analysis"),
        el("button", { class: "btn", onclick: () => A.select("settings", a.id) },
          "Settings"),
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

const TYPE_NAMES = { static: "Static structural", modal: "Modal",
                     shock: "Shock response spectrum",
                     harmonic: "Harmonic response", random: "Random vibration" };

function drivenBy(a) {
  if (a.type === "modal") return "nothing — free vibration";
  if (a.type === "random") return "base PSD";
  if (a.type === "harmonic") {
    return (a.config?.excitation || "force") === "base" ? "base acceleration" : "applied force";
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
    sec("Export",
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-accent",
          onclick: () => A.exportResults(a.id, "all") }, "Export all (CSV)"),
        el("button", { class: "btn",
          onclick: () => A.exportResults(a.id, "tables") }, "Tables only")),
      ),
    sec("Re-run",
      el("div", { class: "btnrow" },
        el("button", { class: "btn", onclick: () => A.runAnalysis(a.id) }, "Run again"),
        el("button", { class: "btn", onclick: () => A.select("settings", a.id) },
          "Analysis Settings"))));
}

/** Banner shown above every panel that presents results, saying whether they
 *  still describe the model on screen. Stale numbers presented as live is the
 *  one failure mode that produces wrong engineering conclusions. */
function statusHead(S, A, a) {
  const meta = S.results[a.id];
  if (!meta) return [];
  if (meta.stale) {
    return [el("div", { class: "stalebar" },
      el("b", {}, "⚠ Out of date. "),
      "The model changed after this ran — mesh, materials, connections or " +
      "boundary conditions. These numbers describe the older model.",
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small btn-accent",
          onclick: () => A.runAnalysis(a.id) }, "Re-run analysis")))];
  }
  if (meta.no_signature) {
    return [el("div", { class: "stalebar" },
      "These results predate change tracking, so Lattice cannot tell whether " +
      "they still match the model. Re-run to be certain.")];
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
 *  The panels show numbers; the method notes live in docs/METHODS.md. */
function methodRef(anchor, label) {
  return el("div", { class: "methodref" },
    el("a", { href: `https://github.com/vraj549/lattice/blob/main/docs/METHODS.md#${anchor}`,
              target: "_blank", rel: "noopener" }, label || "method notes"));
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

  const exportWhat = { frf: "frf", random: "random", shock: "shock" }[what] || "tables";
  const anchor = { contours: "contours", modes: "modes", frf: "frequency-response",
                   random: "random-vibration", bolts: "bolt-forces-and-stress",
                   shock: "shock", slip: "friction-without-a-newton-loop",
                   sizing: "bolt-sizing", reactions: "reactions" }[what];
  const tail = sec(null,
    anchor ? methodRef(anchor) : null,
    el("div", { class: "btnrow" },
      el("button", { class: "btn btn-small",
        onclick: () => A.exportResults(aid, exportWhat) }, "Export this (CSV)"),
      el("button", { class: "btn btn-small",
        onclick: () => A.exportResults(aid, "all") }, "Export all (CSV)"),
      el("button", { class: "btn btn-small",
        onclick: () => A.select("analysis", aid) }, "Analysis setup")));

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
    // Total applied force, so the response can be shown per unit input —
    // amplification is what a sine sweep is actually read for.
    const totalF = (a.loads || []).reduce((acc, l) => {
      if (!["force", "remote"].includes(l.type)) return acc;
      return acc + Math.hypot(l.fx || 0, l.fy || 0, l.fz || 0);
    }, 0);
    const norm = S.frfNorm || "raw";
    const curves = meta.frf.map((f, i) => {
      let mod = f.module;
      if (norm === "perN" && totalF > 0) mod = f.module.map((v) => v / totalF);
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
    const unitTxt = norm === "amp" ? "amplification (x quasi-static)"
                  : norm === "perN" ? "mm / N" : "mm";

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
      selInput("Y axis", norm, [
        ["amp", "Amplification (\u00d7 quasi-static)"],
        ["perN", "Response per unit force (mm/N)"],
        ["raw", "Raw response (mm)"]],
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
        resolutionWarning(S, peaks)) : null,
    ));

    // the canvas needs layout before it can size itself, so only the DRAW
    // is deferred — nothing is appended to the DOM from here
    requestAnimationFrame(() => { if (canvas.isConnected) frfPlot(canvas, curves); });
  }
  return secs;
}

function secContours(S, A, a) {
  const meta = S.results[a.id];
  const secs = [];
  const R = S.activeResult;
  const realFields = meta.fields?.filter((f) => f.part !== "I") || [];
  if (realFields.length) {
    const cur = R?.aid === a.id ? R : null;
    const fsel = selInput("Field", cur?.field || realFields[0].name,
      realFields.map((f) => [f.name, f.label + (f.part === "R" ? " (real part)" : "")]),
      (v) => A.setResultField(a.id, { field: v }));
    const f = realFields.find((x) => x.name === (cur?.field || realFields[0].name)) || realFields[0];
    const comps = compOptions(f);
    const csel = selInput("Component", cur?.comp || comps[0][0], comps,
      (v) => A.setResultField(a.id, { comp: v }));
    const steps = f.steps.map((s, i) => [String(i),
      f.steps.length > 1 ? `${s.ndt} — ${fmtVal(s.value)} ${a.type === "static" ? "" : "Hz"}` : "result"]);
    const ssel = f.steps.length > 1
      ? selInput("Step / mode / frequency", String(cur?.stepIdx ?? 0), steps,
          (v) => A.setResultField(a.id, { stepIdx: Number(v) }))
      : null;
    secs.push(sec("Contours", fsel, csel, ssel,
      el("div", { class: "frm-row2" },
        selInput("Bands", String(contourStyle.bands),
          [["9", "9 (default)"], ["5", "5"], ["13", "13"], ["18", "18"],
           ["27", "27"], ["0", "Smooth"]],
          (v) => { contourStyle.bands = Number(v); A.restyleContours(); }),
        selInput("Palette", contourStyle.palette,
          [["rainbow", "Rainbow"], ["turbo", "Turbo"]],
          (v) => { contourStyle.palette = v; A.restyleContours(); })),
      deformControl(S, A, cur),
      // Animation is not a modal-only idea: watching a static deflection grow
      // and relax is the fastest way to see where a part is actually moving.
      el("div", { class: "btnrow" },
        el("button", { class: "btn", onclick: () => A.toggleAnimate() },
          S.animating ? "Stop animation" : "Animate")),
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-accent", onclick: () => A.loadField(a.id) }, "Show contours"),
        el("button", { class: "btn btn-small",
          onclick: () => A.exportField(a.id) }, "Export nodal values (CSV)"))));
  }
  return secs;
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
               err == null ? "\u2014" : `${(100 * err).toFixed(1)}%`));
        })),
      P.calibrated
        ? null
        : el("div", { class: "hint bad" },
             "Not calibrated \u2014 the bolts carry less than the requested force.")));
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
                 st ? `${st.pct.toFixed(0)}%` : "\u2014"));
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
  const R = S.sizing?.[a.id];
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
  if (R.blocked) return secs;              // the warning above says why
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
                    "slip", "verdict"].map((h) => el("th", {}, h))),
      rows.map((r) => el("tr", {},
        el("td", {}, r.name),
        el("td", {}, r.M_A_Nm.toFixed(1)),
        el("td", { class: r.utilisation > 1 ? "bad" : "" },
           (r.utilisation * 100).toFixed(0)),
        el("td", {}, fmtVal(r.p_max)),
        el("td", {}, r.slip_margin == null ? "\u2014" : r.slip_margin.toFixed(2)),
        el("td", { class: r.feasible ? "" : "bad" },
           r.feasible ? "ok" : "no window"))))));

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
  if (!meta.warnings?.length) return [];
  return [sec("Solver messages",
    ...meta.warnings.map((w) => el("div", { class: "hint warn" }, w)))];
}

/**
 * Shock response.
 *
 * Every number here is a PEAK with no sign and no time attached — an SRS
 * carries neither. Two of them are each defensible on their own; their ratio
 * is not, because they do not have to happen at the same instant.
 */
/**
 * Slip check.
 *
 * The solve glued every checked frictional interface. This says whether it
 * was allowed to: a stuck frictional interface and a bonded one are the same
 * constraint, so if friction held everywhere the linear result is not an
 * approximation of the nonlinear one, it is the nonlinear one.
 */
function slipSections(S, A, a) {
  const R = S.slipResults?.[a.id];
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
            "These fractions count NODES, not area — no nodal areas were "
            + "matched for this interface. Re-mesh to weigh them by area; "
            + "refinement clusters nodes where stress concentrates, so a node "
            + "count reads high exactly where it matters.")
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

function shockSections(S, A, a) {
  const R = S.shockResults?.[a.id];
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
        ["Modal part", `${fmtVal(R.force_modal_N)} N`],
        ["Missing mass", `${(100 * R.missing_mass).toFixed(1)} % \u2192 ` +
                         `${fmtVal(R.missing_force_N)} N at ZPA`],
        ["Effective mass captured", `${(100 * R.mass_captured).toFixed(1)} %`],
        ["Combination", (R.rule || "srss").toUpperCase()],
        ["Input", R.input?.source || "\u2014"],
        ["ZPA", `${fmtVal(R.input?.zpa ?? 0)} g`]])));

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
        el("tr", {}, ["#", "Hz", `m_eff ${R.axis}`, "SRS g", "Force N"]
          .map((h) => el("th", {}, h))),
        R.rows.map((r) => el("tr", {},
          el("td", {}, String(r.mode)),
          el("td", {}, fmtVal(r.f)),
          el("td", {}, `${(100 * r.eff_frac).toFixed(1)}%`),
          el("td", {}, fmtVal(r.srs_g)),
          el("td", {}, fmtVal(r.force_N)))))));
  }
  return secs;
}

function randomSections(S, A, a) {
  const R = S.randomResults?.[a.id];
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

function compOptions(f) {
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
        return el("td", {}, v != null ? `${(100 * v).toFixed(1)}%` : "—");
      }))));
  return el("div", { style: "margin-top:9px" },
    el("span", { class: "lbl" }, "Effective mass"), t);
}
