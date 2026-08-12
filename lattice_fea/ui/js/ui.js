// Tree + context-panel rendering. Pure DOM, no framework.
import { fmtVal } from "./colormap.js";
import { frfChart, seriesColor } from "./charts.js";

export function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else if (k === "html") n.innerHTML = v;
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

export function renderTree(S, A) {
  const tree = document.getElementById("tree");
  tree.innerHTML = "";
  const geo = S.project?.geometry;
  if (!geo) return;
  const setup = S.project.setup;
  const sel = S.selection;

  const row = (kind, id, label, meta, opts = {}) => {
    const r = el("button", {
      class: "row", role: "option",
      "aria-selected": sel.kind === kind && String(sel.id) === String(id) ? "true" : "false",
      "aria-label": label,
      onclick: () => A.select(kind, id),
    });
    if (opts.dotClass) r.append(el("span", { class: `dot ${opts.dotClass}` }));
    if (opts.swatch) r.append(el("span", { class: "swatch", style: `background:${opts.swatch}` }));
    r.append(el("span", { class: "nm" }, label));
    if (meta) r.append(el("span", { class: `mt${opts.warnMeta ? " warn" : ""}` }, meta));
    return r;
  };

  const grp = (label, addFn, ...rows) => {
    const hd = el("div", { class: "grp-hd" }, el("span", { class: "lbl" }, label));
    if (addFn) hd.append(el("button", { class: "grp-add", title: `Add ${label}`, onclick: addFn }, "+ add"));
    return el("div", { class: "grp" }, hd, ...rows);
  };

  // geometry
  tree.append(grp(`Geometry`, null,
    row("model", "root", S.project.name,
        `${geo.solids.length} solid${geo.solids.length > 1 ? "s" : ""}`),
    ...geo.solids.map((s) => {
      const mid = setup.assignments[String(s.tag)];
      const mat = setup.materials.find((m) => m.id === mid);
      return row("solid", s.tag, s.name || `Solid ${s.tag}`,
                 mat ? mat.name : "no material", { warnMeta: !mat });
    }),
    geo.interfaces.length
      ? row("connections", "all", "Bonded interfaces", `${geo.interfaces.length}`)
      : null,
  ));

  tree.append(grp("Supports", () => A.addSupport(),
    ...setup.supports.map((s, i) =>
      row("support", s.id, s.name || `Support ${i + 1}`,
          s.faces?.length ? `${s.faces.length} face${s.faces.length > 1 ? "s" : ""}` : "no faces",
          { swatch: "var(--constraint)", warnMeta: !s.faces?.length })),
  ));

  tree.append(grp("Loads", () => A.addLoad(),
    ...setup.loads.map((l, i) =>
      row("load", l.id, l.name || `Load ${i + 1}`, loadMeta(l),
          { swatch: "var(--accent)",
            warnMeta: !["gravity", "rotation"].includes(l.type) && !l.faces?.length })),
  ));

  setup.bolts ||= [];
  setup.ties ||= [];
  tree.append(grp("Bolts", () => A.addBolt(),
    ...setup.bolts.map((bl, i) =>
      row("bolt", bl.id, bl.name || `Bolt ${i + 1}`,
          bl.side_a_faces?.length && bl.side_b_faces?.length
            ? `M${bl.d_mm ?? "?"} · ${fmtVal(bl.preload_N || 0)} N`
            : "pick faces",
          { swatch: "#7fb2d8",
            warnMeta: !(bl.side_a_faces?.length && bl.side_b_faces?.length) })),
  ));

  if ((setup.ties || []).length || geo.solids.length > 1) {
    tree.append(grp("Ties", () => A.addTie(),
      ...(setup.ties || []).map((t, i) =>
        row("tie", t.id, t.name || `Tie ${i + 1}`,
            t.slave_faces?.length && t.master_solid
              ? `→ solid ${t.master_solid}` : "incomplete",
            { swatch: "var(--ok)", warnMeta: !(t.slave_faces?.length && t.master_solid) })),
    ));
  }

  tree.append(grp("Probes", () => A.addProbe(),
    ...setup.probes.map((p, i) =>
      row("probe", p.id, p.name || `Probe ${i + 1}`,
          `${fmtVal(p.x)}, ${fmtVal(p.y)}, ${fmtVal(p.z)}`)),
  ));

  const ms = S.meshData?.stats;
  tree.append(grp("Mesh", null,
    row("mesh", "mesh", ms ? `Tet${ms.order === 2 ? "10" : "4"} · ${fmtVal(ms.size_mm)} mm` : "Not meshed",
        ms ? `${ms.nodes.toLocaleString()} n` : "configure",
        { dotClass: ms ? "ok" : "idle" })));

  tree.append(grp("Analyses", () => A.addAnalysis(),
    ...setup.analyses.map((a) => {
      const st = S.runStatus[a.id];
      const dot = st === "running" ? "run" : st === "done" ? "ok" : st === "failed" ? "bad" : "idle";
      return row("analysis", a.id, a.name || a.type, a.type, { dotClass: dot });
    }),
  ));
}

function loadMeta(l) {
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

  switch (kind) {
    case "solid": return panelSolid(S, A, put, id);
    case "connections": return panelConnections(S, A, put);
    case "support": return panelSupport(S, A, put, id);
    case "load": return panelLoad(S, A, put, id);
    case "bolt": return panelBolt(S, A, put, id);
    case "tie": return panelTie(S, A, put, id);
    case "probe": return panelProbe(S, A, put, id);
    case "mesh": return panelMesh(S, A, put);
    case "analysis": return panelAnalysis(S, A, put, id);
    default: return panelModel(S, A, put);
  }
}

const sec = (label, ...kids) => el("div", { class: "sec" },
  label ? el("span", { class: "lbl" }, label) : null, ...kids);

const dl = (rows) => el("dl", {}, rows.map(([k, v]) =>
  el("div", { class: "fld" }, el("dt", {}, k), el("dd", {}, String(v)))));

function numInput(label, value, oninput, attrs = {}) {
  return el("label", { class: "frm" }, label,
    el("input", { type: "number", value: value ?? "", step: "any", ...attrs,
      oninput: (e) => oninput(e.target.value === "" ? null : Number(e.target.value)) }));
}
function textInput(label, value, oninput) {
  return el("label", { class: "frm" }, label,
    el("input", { type: "text", value: value ?? "",
      oninput: (e) => oninput(e.target.value) }));
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
  if (!setup.supports.some((s) => s.faces?.length)) w.push("No support with faces assigned.");
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
  put(s.name || `Solid ${tag}`, `${s.faces.length} faces`,
    sec("Material",
      selInput("Assign material", mid.startsWith("custom") ? mid : (mid ? `lib:${findLib(S, mid)}` : ""), opts,
        (v) => A.assignMaterial(tag, v)),
      matProps(S, mid)),
    sec("Properties", dl([
      ["Volume", `${fmtVal(s.volume)} mm³`],
      ["Mass", massOf(S, s)],
    ])),
    sec("Display", el("div", { class: "btnrow" },
      el("button", { class: "btn", onclick: () => A.toggleSolid(s.tag) },
        hidden ? "Show solid" : "Hide solid"))));
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
  const solidName = (t) => geo.solids.find((s) => s.tag === t)?.name || `Solid ${t}`;
  put("Connections", `${geo.interfaces.length} bonded`,
    sec("Bonded (conformal)",
      el("div", { class: "hint" },
        "Shared faces found while importing. The mesh is continuous across " +
        "them — parts are bonded with no tie constraints needed."),
      dl(geo.interfaces.map((i) => [
        `Face ${i.face}`, `${solidName(i.solids[0])} ↔ ${solidName(i.solids[1])}`]))),
    sec(null, el("div", { class: "hint" },
      "Parts that only touch without sharing a face are NOT connected. " +
      "The mesh step warns if the model comes out in disconnected pieces.")));
}

// ---------- supports / loads / probes ----------

function panelSupport(S, A, put, id) {
  const s = S.project.setup.supports.find((x) => x.id === id);
  if (!s) return put("Support", "");
  put(s.name || "Support", "fixed",
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
    sec(null, delBtn("support", () => A.removeItem("supports", id))));
}

function panelLoad(S, A, put, id) {
  const l = S.project.setup.loads.find((x) => x.id === id);
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
  secs.push(sec(null, delBtn("load", () => A.removeItem("loads", id))));
  put(l.name || "Load", l.type, ...secs);
}

export const BOLT_SIZES = [
  ["M3", 3], ["M4", 4], ["M5", 5], ["M6", 6], ["M8", 8], ["M10", 10],
  ["M12", 12], ["M14", 14], ["M16", 16], ["M20", 20], ["M24", 24],
];
// tensile stress areas (mm²) for preload suggestion, keyed by d
const AS = { 3: 5.03, 4: 8.78, 5: 14.2, 6: 20.1, 8: 36.6, 10: 58.0,
             12: 84.3, 14: 115, 16: 157, 20: 245, 24: 353 };

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

  const suggested = AS[bl.d_mm] ? Math.round(0.65 * AS[bl.d_mm] * 640) : null;

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
      selInput("Nominal size", String(bl.d_mm ?? ""),
        [["", "— choose —"], ...BOLT_SIZES.map(([n, d]) => [String(d), n])],
        (v) => A.mutate(() => { bl.d_mm = v ? Number(v) : null; })),
      holeD && !bl.d_mm ? el("div", { class: "hint" },
        `Hole ⌀${fmtVal(holeD)} suggests ${nearestBolt(holeD)}`) : null,
      numInput("Preload (N)", bl.preload_N, (v) => A.mutate(() => { bl.preload_N = v; })),
      suggested ? el("div", { class: "btnrow" },
        el("button", { class: "btn btn-small",
          onclick: () => A.mutate(() => { bl.preload_N = suggested; }) },
          `Suggest ${suggested.toLocaleString()} N (65 % yield, class 8.8)`)) : null,
      numInput("Bolt modulus E (GPa)", bl.E_GPa ?? 210, (v) => A.mutate(() => { bl.E_GPa = v ?? 210; }))),
    sec(null, el("div", { class: "hint" },
      "Preload acts in static analyses (axial pre-strain). Modal/harmonic use the " +
      "bolt stiffness but not the preload — linear analyses have no stress stiffening.")),
    sec(null, delBtn("bolt", () => A.removeItem("bolts", id))));
}

function nearestBolt(holeD) {
  let best = BOLT_SIZES[0];
  for (const b of BOLT_SIZES) if (b[1] <= holeD - 0.3) best = b;
  return best[0];
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
        [["", "— choose —"], ...geo.solids.map((s) => [String(s.tag), s.name || `Solid ${s.tag}`])],
        (v) => A.mutate(() => { t.master_solid = v ? Number(v) : null; }))),
    sec(null, delBtn("tie", () => A.removeItem("ties", id))));
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
    sec(null, delBtn("probe", () => A.removeItem("probes", id))));
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
        [["2", "Quadratic (Tet10) — recommended"], ["1", "Linear (Tet4)"]],
        (v) => A.mutate(() => { m.order = Number(v); }))),
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
    secs.push(sec("Current mesh", dl([
      ["Nodes", stats.nodes.toLocaleString()],
      ["Elements", stats.elements.toLocaleString()],
      ["DOF", stats.dof.toLocaleString()],
      ["Order", stats.order === 2 ? "quadratic (Tet10)" : "linear (Tet4)"],
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
  return { ...base, name: "Harmonic response",
           config: { f_min: 20, f_max: 2000, n_steps: 150, spacing: "log",
                     damping: 0.02, field_freqs: [] } };
}

function panelAnalysis(S, A, put, id) {
  const a = S.project.setup.analyses.find((x) => x.id === id);
  if (!a) return put("Analysis", "");
  const c = a.config;
  const running = S.runStatus[a.id] === "running";
  const done = S.runStatus[a.id] === "done";

  const secs = [sec("Definition",
    textInput("Name", a.name, (v) => A.mutate(() => { a.name = v; })),
    dl([["Type", a.type], ["Solver", "code_aster · MUMPS"]]))];

  if (a.type === "modal") {
    secs.push(sec("Extraction",
      numInput("Number of modes", c.n_modes, (v) => A.mutate(() => { c.n_modes = v || 10; }), { min: 1, max: 100 }),
      el("div", { class: "hint" }, "Lowest modes above the supports (Sorensen/ARPACK).")));
  }
  if (a.type === "harmonic") {
    secs.push(sec("Sweep",
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
        "Modal superposition on a basis up to 1.6 × f max. Loads defined above " +
        "are applied as the harmonic excitation; response is read at the probes.")));
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
  if (a.type === "static") {
    secs.push(sec("Output", el("div", { class: "hint" },
      "Displacement, von Mises / principal stresses, stress tensor, and reaction " +
      "forces at the supports.")));
  }

  const solverOk = S.config?.solver?.available;
  secs.push(sec(null,
    el("div", { class: "btnrow" },
      el("button", { class: "btn btn-accent", disabled: running || !solverOk,
        onclick: () => A.runAnalysis(a.id) }, running ? "Running…" : "Run analysis"),
      done ? el("button", { class: "btn", onclick: () => A.openResults(a.id) }, "View results") : null),
    !solverOk ? el("div", { class: "hint warn" },
      "⚠ No code_aster solver detected — see README → Solver setup.") : null));

  if (done && S.results[a.id]) secs.push(...resultSections(S, A, a));
  secs.push(sec(null, delBtn("analysis", () => A.removeItem("analyses", id))));
  put(a.name || a.type, a.type, ...secs);
}

// ---------- results ----------

function resultSections(S, A, a) {
  const meta = S.results[a.id];
  const secs = [];
  const R = S.activeResult;

  // tables
  const modes = meta.tables?.modes?.[0];
  if (modes && a.type !== "static") {
    const fi = modes.columns.indexOf("FREQ");
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
      el("div", { class: "hint" }, "Select a mode to view / animate its shape."),
      part ? partTable(part, meta.tables?.tables) : null));
  }

  // FRF
  if (meta.frf?.length) {
    const canvas = el("canvas", { class: "frf" });
    const probes = S.project.setup.probes;
    const curves = meta.frf.map((f, i) => ({
      label: `${probes[f.probe - 1]?.name || "P" + f.probe}·${f.comp}`,
      freq: f.freq, module: f.module, color: seriesColor(i),
    }));
    secs.push(sec("Frequency response (displacement, mm)",
      canvas,
      el("div", { class: "hint" }, curves.map((c, i) =>
        el("span", { style: `color:${c.color};margin-right:8px` }, c.label))),
    ));
    requestAnimationFrame(() => frfChart(canvas, curves));
  }

  // fields
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
      el("label", { class: "frm" }, "Deformation scale ×",
        el("input", { type: "range", min: 0, max: 3, step: 0.01,
          value: cur ? Math.log10((cur.defMult || 1) * 10) : 1,
          oninput: (e) => A.setDeform(Math.pow(10, Number(e.target.value)) / 10) })),
      a.type !== "static"
        ? el("div", { class: "btnrow" },
            el("button", { class: "btn", onclick: () => A.toggleAnimate() },
              S.animating ? "Stop animation" : "Animate")) : null,
      el("div", { class: "btnrow" },
        el("button", { class: "btn btn-accent", onclick: () => A.loadField(a.id) }, "Show contours"))));
  }

  // bolt forces for static
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
      secs.push(sec("Bolt forces",
        el("table", { class: "rtable" },
          el("tr", {}, ["Bolt", "Axial N", "Shear N", "Bending N·mm", "vs preload"].map((h) => el("th", {}, h))),
          [...per.entries()].map(([label, r]) => {
            // label is "BOLT<n>" — map by index parsed from the label, not row order
            const n = Number((label.match(/BOLT(\d+)/) || [])[1]);
            const cfg = n ? bolts[n - 1] : null;
            const pct = cfg?.preload_N ? `${(100 * r.N / cfg.preload_N).toFixed(0)}%` : "—";
            return el("tr", {},
              el("td", {}, cfg?.name || label),
              el("td", {}, fmtVal(r.N)),
              el("td", {}, fmtVal(r.V)),
              el("td", {}, fmtVal(r.M)),
              el("td", {}, pct));
          })),
        el("div", { class: "hint" },
          "Beam end forces from EFGE_ELNO. Axial includes the preload you applied; " +
          "check shank stress and joint margins per your bolt spec (e.g. VDI 2230).")));
    }
  }

  // reactions for static
  const reactions = meta.tables?.tables?.find?.((b) => b.columns.includes("DX") && b.columns.includes("INTITULE"));
  if (a.type === "static" && meta.tables?.tables) {
    for (const b of meta.tables.tables) {
      const ix = ["DX", "DY", "DZ"].map((c) => b.columns.indexOf(c));
      if (ix.every((i) => i >= 0) && b.rows.length) {
        const r = b.rows[b.rows.length - 1];
        secs.push(sec("Reaction forces (supports)", dl([
          ["ΣFx", `${fmtVal(r[ix[0]] ?? 0)} N`],
          ["ΣFy", `${fmtVal(r[ix[1]] ?? 0)} N`],
          ["ΣFz", `${fmtVal(r[ix[2]] ?? 0)} N`],
        ])));
        break;
      }
    }
  }

  if (meta.warnings?.length) {
    secs.push(sec("Warnings", ...meta.warnings.map((w) => el("div", { class: "hint warn" }, w))));
  }
  return secs;
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

function partTable(part, massBlocks) {
  const cols = part.columns;
  const fi = cols.indexOf("FREQ");
  const dx = cols.indexOf("MASS_EFFE_UN_DX");
  if (fi < 0 || dx < 0) return null;
  let total = null;
  for (const b of massBlocks || []) {
    const mi = b.columns.indexOf("MASSE");
    if (mi >= 0 && b.rows.length) total = b.rows[0][mi];
  }
  const th = ["#", "Hz", "mX", "mY", "mZ"];
  const t = el("table", { class: "rtable" },
    el("tr", {}, th.map((h) => el("th", {}, h))),
    part.rows.map((r, i) => el("tr", {},
      el("td", {}, String(i + 1)),
      el("td", {}, fmtVal(r[fi] ?? 0)),
      ...[0, 1, 2].map((k) => {
        const v = r[dx + k];
        const pct = total && v != null ? ` (${(100 * v / total).toFixed(0)}%)` : "";
        return el("td", {}, v != null ? fmtVal(v) + pct : "—");
      }))));
  return el("div", { style: "margin-top:9px" },
    el("span", { class: "lbl" }, "Effective mass"), t);
}
