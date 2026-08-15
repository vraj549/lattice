import { api } from "./api.js";
import { Viewer } from "./viewer.js";
import { renderTree, renderPanel, defaultAnalysis, el } from "./ui.js";
import { renderLegend, fmtVal, contourStyle } from "./colormap.js";

const uid = () => Math.random().toString(36).slice(2, 8);

// ---------------- state ----------------
const S = {
  config: null, library: [],
  project: null, tess: null, meshData: null,
  results: {},               // aid -> meta
  runStatus: {},             // aid -> running|done|failed
  selection: { kind: "model", id: "root" },
  hiddenSolids: new Set(),
  view: "geometry",
  activeResult: null,        // {aid, field, comp, stepIdx, defMult}
  animating: false,
  saveTimer: null,
};

let viewer = null;
let pickCtx = null;          // {item, key} | {probe}

// ---------------- actions ----------------
const A = {
  select(kind, id) {
    S.selection = { kind, id };
    if (kind === "analysis" && S.runStatus[id] === "done" && !S.results[id]) {
      A.openResults(id);
      return;
    }
    refresh();
  },

  mutate(fn) { fn(); scheduleSave(); refresh(); },

  // ---- setup items ----
  // Supports and loads belong to a specific analysis.
  findAnalysisOf(kind, id) {
    for (const a of S.project.setup.analyses || []) {
      const list = kind === "support" ? (a.supports || []) : (a.loads || []);
      const item = list.find((x) => x.id === id);
      if (item) return { analysis: a, item };
    }
    return { analysis: null, item: null };
  },
  currentAnalysis() {
    const setup = S.project.setup;
    const { kind, id } = S.selection;
    if (kind === "analysis") return setup.analyses.find((a) => a.id === id);
    if (kind === "support" || kind === "load") return A.findAnalysisOf(kind, id).analysis;
    return setup.analyses[0];
  },
  addSupport(aid) {
    const a = (S.project.setup.analyses || []).find((x) => x.id === aid) || A.currentAnalysis();
    if (!a) { logLine("Add an analysis first — supports belong to one.", "warnln"); return; }
    a.supports ||= [];
    const s = { id: uid(), name: `Support ${a.supports.length + 1}`, type: "fixed", faces: [] };
    a.supports.push(s);
    S.selection = { kind: "support", id: s.id };
    A.mutate(() => {});
    A.pickFaces(s, "faces");
  },
  addLoad(aid) {
    const a = (S.project.setup.analyses || []).find((x) => x.id === aid) || A.currentAnalysis();
    if (!a) { logLine("Add an analysis first — loads belong to one.", "warnln"); return; }
    a.loads ||= [];
    const l = { id: uid(), name: `Load ${a.loads.length + 1}`,
                type: "force", faces: [], fx: 0, fy: 0, fz: -100 };
    a.loads.push(l);
    S.selection = { kind: "load", id: l.id };
    A.mutate(() => {});
    A.pickFaces(l, "faces");
  },
  addBolt() {
    const bl = { id: uid(), name: `Bolt ${S.project.setup.bolts.length + 1}`,
                 side_a_faces: [], side_b_faces: [], d_mm: null,
                 E_GPa: 210, preload_N: null };
    (S.project.setup.bolts ||= []).push(bl);
    S.selection = { kind: "bolt", id: bl.id };
    A.mutate(() => {});
    A.pickFaces(bl, "side_a_faces");
  },
  addTie() {
    const t = { id: uid(), name: `Tie ${S.project.setup.ties.length + 1}`,
                slave_faces: [], master_solid: null };
    (S.project.setup.ties ||= []).push(t);
    S.selection = { kind: "tie", id: t.id };
    A.mutate(() => {});
  },
  addProbe() {
    const p = { id: uid(), name: `Probe ${S.project.setup.probes.length + 1}`,
                x: 0, y: 0, z: 0 };
    S.project.setup.probes.push(p);
    S.selection = { kind: "probe", id: p.id };
    A.mutate(() => {});
    A.pickPoint(p);
  },
  addAnalysis() {
    const type = prompt("Analysis type: static, modal, harmonic, or random", "static");
    if (!["static", "modal", "harmonic", "random"].includes(type)) return;
    const a = defaultAnalysis(type);
    a.supports = []; a.loads = [];
    S.project.setup.analyses.push(a);
    S.selection = { kind: "analysis", id: a.id };
    (S.openAnalyses ||= {})[a.id] = true;
    A.mutate(() => {});
    A.addSupport(a.id);          // every analysis needs at least one support
  },
  removeItem(listName, id) {
    if (listName === "supports" || listName === "loads") {
      const kind = listName === "supports" ? "support" : "load";
      const { analysis } = A.findAnalysisOf(kind, id);
      if (analysis) {
        const list = analysis[listName];
        const i = list.findIndex((x) => x.id === id);
        if (i >= 0) list.splice(i, 1);
      }
    } else {
      const list = S.project.setup[listName] || [];
      const i = list.findIndex((x) => x.id === id);
      if (i >= 0) list.splice(i, 1);
    }
    S.selection = { kind: "model", id: "root" };
    A.mutate(() => {});
  },

  assignMaterial(solidTag, value) {
    const setup = S.project.setup;
    if (!value) { delete setup.assignments[String(solidTag)]; A.mutate(() => {}); return; }
    let mid = value;
    if (value.startsWith("lib:")) {
      const libId = value.slice(4);
      const lib = S.library.find((m) => m.id === libId);
      mid = `lib-${libId}`;
      if (!setup.materials.find((m) => m.id === mid)) {
        setup.materials.push({ ...lib, id: mid, lib: libId });
      }
    }
    setup.assignments[String(solidTag)] = mid;
    A.mutate(() => {});
  },

  toggleSolid(tag) {
    if (S.hiddenSolids.has(tag)) S.hiddenSolids.delete(tag);
    else S.hiddenSolids.add(tag);
    viewer.setHiddenSolids(S.hiddenSolids);
    refresh();
  },

  // ---- picking ----
  pickFaces(item, key) {
    pickCtx = { item, key };
    setView("geometry");
    viewer.startPickFaces(item[key] || []);
    showPickBar(`Pick faces for “${item.name || "selection"}” — click to toggle`);
  },
  pickPoint(probe) {
    pickCtx = { probe };
    setView("geometry");
    viewer.startPickPoint();
    showPickBar("Click a point on any surface");
  },

  // ---- mesh & solve ----
  async runMesh() {
    await saveNow();
    try {
      const { job } = await api.post(`/api/projects/${S.project.id}/mesh`);
      watchJob(job, "Meshing", async () => {
        S.meshData = await api.get(`/api/projects/${S.project.id}/mesh`);
        setView("mesh");
        viewer.showMeshPreview(S.meshData.skin);
        updateStat();
        refresh();
      });
    } catch (e) { logLine(`mesh: ${e.message}`, "badln"); }
  },

  async runAnalysis(aid) {
    await saveNow();
    S.runStatus[aid] = "running";
    refresh();
    try {
      const { job } = await api.post(`/api/projects/${S.project.id}/solve/${aid}`);
      watchJob(job, "Solving", async (ok) => {
        S.runStatus[aid] = ok ? "done" : "failed";
        if (ok) await A.openResults(aid);
        else {
          // the solve may have completed and only a later step failed —
          // try to salvage whatever landed on disk before giving up
          logLine("Job reported failure; checking for usable output …", "warnln");
          await A.recoverResults(aid);
        }
        refresh();
      });
    } catch (e) {
      S.runStatus[aid] = "failed";
      logLine(`solve: ${e.message}`, "badln");
      refresh();
    }
  },

  async recoverResults(aid) {
    try {
      logLine("Re-parsing files the solver already wrote …");
      const r = await api.post(`/api/projects/${S.project.id}/results/${aid}/reparse`);
      logLine(`recovered: ${r.found.join(", ")}`, "");
      await A.openResults(aid);
    } catch (e) { logLine(`recover failed: ${e.message}`, "badln"); }
  },

  async openResults(aid) {
    try {
      S.results[aid] = await api.get(`/api/projects/${S.project.id}/results/${aid}`);
      S.runStatus[aid] = "done";
      S.selection = { kind: "analysis", id: aid };
      const f = S.results[aid].fields?.find((x) => x.part !== "I");
      S.activeResult = { aid, field: f?.name, comp: f?.kind === "DEPL" ? "MAG" : (f?.comps[0] || ""),
                         stepIdx: 0, defMult: 1 };
      refresh();
      if (f) await A.loadField(aid);
    } catch (e) { logLine(`results: ${e.message}`, "badln"); }
  },

  setResultField(aid, patch) {
    if (!S.activeResult || S.activeResult.aid !== aid) {
      S.activeResult = { aid, stepIdx: 0, defMult: 1 };
    }
    Object.assign(S.activeResult, patch);
    if (patch.field) {
      const f = S.results[aid].fields.find((x) => x.name === patch.field);
      S.activeResult.comp = f?.kind === "DEPL" ? "MAG" : (f?.comps[0] || "");
      S.activeResult.stepIdx = 0;
    }
    refresh();
  },

  async loadField(aid) {
    const R = S.activeResult;
    if (!R || R.aid !== aid) return;
    const meta = S.results[aid];
    const f = meta.fields.find((x) => x.name === R.field) || meta.fields[0];
    if (!f) return;
    const step = f.steps[Math.min(R.stepIdx || 0, f.steps.length - 1)];
    try {
      const payload = await api.get(
        `/api/projects/${S.project.id}/results/${aid}/field` +
        `?name=${encodeURIComponent(f.name)}&step=${encodeURIComponent(step.key)}` +
        `&comp=${encodeURIComponent(R.comp || "MAG")}`);
      R.payload = payload;
      const diag = S.project.geometry.diag;
      const autoScale = payload.disp_max > 1e-12 ? (diag * 0.05) / payload.disp_max : 0;
      R.autoScale = autoScale;
      setView("results");
      viewer.showResult(payload, { defScale: autoScale * (R.defMult || 1), animate: S.animating });
      const a = S.project.setup.analyses.find((x) => x.id === aid);
      const unit = f.kind === "DEPL" ? "mm" : "MPa";
      const stepTxt = f.steps.length > 1 ? ` @ ${fmtVal(step.value)} Hz` : "";
      renderLegend(payload.min, payload.max, `${R.comp || f.label} · ${unit}${stepTxt}`);
      document.getElementById("vpStat").innerHTML =
        `max <b>${fmtVal(payload.max)} ${unit}</b><br>min <b>${fmtVal(payload.min)} ${unit}</b>` +
        (payload.disp_max ? `<br>deform ×${fmtVal(autoScale * (R.defMult || 1))}` : "");
    } catch (e) { logLine(`field: ${e.message}`, "badln"); }
  },

  showMode(aid, stepIdx) {
    A.setResultField(aid, { stepIdx });
    S.animating = true;
    A.loadField(aid);
  },

  refreshPanel() { refresh(); },

  // typing in the PSD grid must not re-render the panel on every keystroke
  saveOnly() { scheduleSave(); },

  pasteSpec(cfg) {
    const txt = prompt(
      "Paste PSD rows — one 'Hz  g^2/Hz' pair per line (tab, comma or space separated):");
    if (!txt) return;
    const rows = [];
    for (const line of txt.split(/[\r\n]+/)) {
      const nums = line.trim().split(/[\s,;\t]+/).map(Number).filter((v) => !isNaN(v));
      if (nums.length >= 2 && nums[0] > 0) rows.push([nums[0], nums[1]]);
    }
    if (!rows.length) { logLine("No usable rows found in pasted text.", "warnln"); return; }
    rows.sort((a, b) => a[0] - b[0]);
    A.mutate(() => { cfg.spec = rows; });
    logLine(`PSD spec: ${rows.length} breakpoints loaded.`);
  },

  async loadRandom(aid) {
    if (S.randomPending?.[aid]) return;
    (S.randomPending ||= {})[aid] = true;
    try {
      const r = await api.get(`/api/projects/${S.project.id}/results/${aid}/random`);
      (S.randomResults ||= {})[aid] = r;
      refresh();
    } catch (e) {
      logLine(`random response: ${e.message}`, "badln");
    } finally { S.randomPending[aid] = false; }
  },

  restyleContours() {
    viewer.setContourStyle();
    const R = S.activeResult;
    if (R?.payload) {
      const f = S.results[R.aid]?.fields.find((x) => x.name === R.field);
      const unit = f?.kind === "DEPL" ? "mm" : "MPa";
      renderLegend(R.payload.min, R.payload.max, `${R.comp || f?.label || ""} · ${unit}`);
    }
    refresh();
  },

  setDeform(mult) {
    const R = S.activeResult;
    if (!R) return;
    R.defMult = mult;
    viewer.setDeform((R.autoScale || 0) * mult);
    if (R.payload?.disp_max) {
      const s = document.getElementById("vpStat");
      s.innerHTML = s.innerHTML.replace(/deform ×[^<]*/, `deform ×${fmtVal((R.autoScale || 0) * mult)}`);
    }
  },

  async recheckSolver() {
    try {
      const r = await api.post("/api/config/recheck");
      S.config.solver = r.solver;
      updateSolverChip();
      logLine(`solver: ${r.solver.detail}`, r.solver.available ? "" : "warnln");
      for (const n of r.solver.notes || []) logLine(`  ${n}`, "warnln");
      refresh();
    } catch (e) { logLine(`recheck failed: ${e.message}`, "badln"); }
  },

  toggleAnimate() {
    S.animating = !S.animating;
    viewer.setAnimate(S.animating);
    refresh();
  },
};

// ---------------- pick bar ----------------
function showPickBar(msg) {
  const bar = document.getElementById("pickBar");
  document.getElementById("pickMsg").textContent = msg;
  bar.hidden = false;
}
function hidePickBar() { document.getElementById("pickBar").hidden = true; }

document.getElementById("pickDone").addEventListener("click", () => {
  if (pickCtx?.item) {
    pickCtx.item[pickCtx.key] = viewer.endPick();
    scheduleSave();
  } else viewer.endPick();
  pickCtx = null;
  hidePickBar();
  refresh();
});
document.getElementById("pickCancel").addEventListener("click", () => {
  viewer.endPick();
  pickCtx = null;
  hidePickBar();
  refresh();
});

// ---------------- jobs & log ----------------
let activeJob = null;
function logLine(text, cls = "") {
  const body = document.getElementById("logBody");
  const div = document.createElement("div");
  if (cls) div.className = cls;
  div.textContent = text;
  body.append(div);
  body.scrollTop = body.scrollHeight;
}

function watchJob(jid, label, onFinish) {
  activeJob = jid;
  document.getElementById("logDrawer").dataset.open = "true";
  document.getElementById("logToggle").setAttribute("aria-expanded", "true");
  document.getElementById("logMeta").textContent = label;
  document.getElementById("logCancel").hidden = false;
  let offset = 0;
  const poll = async () => {
    try {
      const j = await api.get(`/api/jobs/${jid}?offset=${offset}`);
      for (const ln of j.log) {
        const low = ln.toLowerCase();
        logLine(ln, low.includes("error") || low.includes("<f>") ? "badln"
                  : low.includes("warn") || low.includes("<a>") ? "warnln" : "");
      }
      offset = j.log_offset;
      if (j.status === "running") { setTimeout(poll, 900); return; }
      document.getElementById("logMeta").textContent = `${label} — ${j.status}`;
      document.getElementById("logCancel").hidden = true;
      activeJob = null;
      onFinish?.(j.status === "done");
    } catch (e) {
      logLine(`job poll failed: ${e.message}`, "badln");
      document.getElementById("logCancel").hidden = true;
      activeJob = null;
    }
  };
  poll();
}

document.getElementById("logCancel").addEventListener("click", () => {
  if (activeJob) api.post(`/api/jobs/${activeJob}/cancel`).catch(() => {});
});
document.getElementById("logToggle").addEventListener("click", (e) => {
  const d = document.getElementById("logDrawer");
  const open = d.dataset.open !== "false";
  d.dataset.open = open ? "false" : "true";
  e.target.setAttribute("aria-expanded", String(!open));
});

// ---------------- save ----------------
function scheduleSave() {
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(saveNow, 700);
}
async function saveNow() {
  clearTimeout(S.saveTimer);
  if (!S.project) return;
  try { await api.put(`/api/projects/${S.project.id}/setup`, S.project.setup); }
  catch (e) { logLine(`save failed: ${e.message}`, "badln"); }
}

// ---------------- views ----------------
function setView(v) {
  S.view = v;
  for (const b of document.querySelectorAll(".vtab")) {
    b.setAttribute("aria-selected", String(b.dataset.view === v));
  }
  renderLegend(null);
  if (v === "geometry") { viewer.showGeometry(); updateStat(); }
  if (v === "mesh") {
    if (S.meshData) viewer.showMeshPreview(S.meshData.skin);
    else viewer.showMeshPreview(null);
    updateStat();
  }
  // results view is driven by loadField
}

document.getElementById("viewTabs").addEventListener("click", (e) => {
  const b = e.target.closest(".vtab");
  if (!b) return;
  if (b.dataset.view === "results") {
    const done = Object.entries(S.runStatus).find(([, st]) => st === "done");
    if (done) A.openResults(done[0]);
    return;
  }
  setView(b.dataset.view);
});

function updateStat() {
  const s = document.getElementById("vpStat");
  if (S.view === "mesh" && S.meshData) {
    const m = S.meshData.stats;
    s.innerHTML = `<b>${m.nodes.toLocaleString()}</b> nodes · <b>${m.elements.toLocaleString()}</b> elems<br>` +
                  `${m.dof.toLocaleString()} DOF · est ${m.mem_gb_est} GB`;
  } else if (S.project?.geometry) {
    const g = S.project.geometry;
    s.innerHTML = `${g.solids.length} solid(s) · ${g.faces.length} faces`;
  } else s.innerHTML = "";
}

// ---------------- face states for BC visualization ----------------
function faceStates() {
  const map = new Map();
  if (!S.project) return map;
  // only the analysis in context — showing every analysis's BCs at once was
  // the thing that made a shared list confusing in the first place
  const a = A.currentAnalysis();
  for (const sup of a?.supports || []) {
    for (const f of sup.faces || []) map.set(Number(f), "support");
  }
  for (const l of a?.loads || []) {
    for (const f of l.faces || []) map.set(Number(f), "load");
  }
  for (const bl of S.project.setup.bolts || []) {
    for (const f of [...(bl.side_a_faces || []), ...(bl.side_b_faces || [])]) {
      map.set(Number(f), "bolt");
    }
  }
  return map;
}

// ---------------- refresh ----------------
function refresh() {
  if (!S.project) return;
  renderTree(S, A);
  renderPanel(S, A);
  viewer.setFaceStates(faceStates());
  viewer.setGlyphs({ ...S.project.setup, ...(A.currentAnalysis() || { supports: [], loads: [] }) },
                   S.project.geometry);
}

// ---------------- project bootstrap ----------------
async function openProject(pid) {
  S.project = await api.get(`/api/projects/${pid}`);
  document.getElementById("projName").textContent = S.project.name;
  document.getElementById("overlay").hidden = true;
  S.results = {}; S.runStatus = {}; S.meshData = null; S.activeResult = null;
  S.selection = { kind: "model", id: "root" };

  S.tess = await api.get(`/api/projects/${pid}/tessellation`);
  viewer.setGeometry(S.tess, S.project.geometry);

  try { S.meshData = await api.get(`/api/projects/${pid}/mesh`); } catch { /* not meshed */ }

  // discover existing results
  for (const a of S.project.setup.analyses) {
    try {
      S.results[a.id] = await api.get(`/api/projects/${pid}/results/${a.id}`);
      S.runStatus[a.id] = "done";
    } catch { /* no results yet */ }
  }
  updateStat();
  refresh();
}

async function showOverlay() {
  const ov = document.getElementById("overlay");
  ov.hidden = false;
  const list = document.getElementById("projList");
  list.innerHTML = "";
  const projects = await api.get("/api/projects");
  if (!projects.length) list.append(el("div", { class: "hint" }, "No projects yet — import a STEP to start."));
  for (const p of projects) {
    const item = el("button", { class: "proj-item", onclick: () => openProject(p.id) },
      el("span", {}, p.name),
      el("span", { class: "mt" }, p.has_geometry ? "" : "importing…"));
    const del = el("button", { class: "proj-del", title: "Delete project",
      onclick: async (e) => {
        e.stopPropagation();
        if (confirm(`Delete project “${p.name}”?`)) {
          await api.del(`/api/projects/${p.id}`);
          showOverlay();
        }
      } }, "✕");
    item.append(del);
    list.append(item);
  }
}

document.getElementById("btnProjects").addEventListener("click", showOverlay);

document.getElementById("btnResources").addEventListener("click", () => showResources());

function showResources() {
  const sv = S.config?.solver || {};
  const gb = (mb) => (mb ? (mb / 1024).toFixed(1) + " GB" : "unknown");
  const wsl = sv.mode === "wsl";
  const ceiling = wsl && sv.vm_ram_mb ? sv.vm_ram_mb : sv.host_ram_mb;
  const tight = ceiling && sv.memory_mb > ceiling * 0.85;

  const rows = [
    ["Solver mode", sv.mode + (sv.wsl_distro ? ` · ${sv.wsl_distro}` : "")],
    ["Threads given to the solver", `${sv.ncpus} of ${sv.host_cores || "?"} cores`],
    ["Memory limit for the solver", gb(sv.memory_mb)],
    [wsl ? "RAM inside the WSL VM" : "RAM on this machine", gb(ceiling)],
    ["Time limit per run", `${Math.round((sv.time_limit_s || 0) / 60)} min`],
  ];

  const body = el("div", {},
    el("h1", { class: "modal-title" }, "Solver resources"),
    el("p", { class: "modal-sub" },
      "What code_aster is allowed to use, and how to change it."),
    el("table", { class: "rtable" },
      rows.map(([k, v]) => el("tr", {}, el("td", {}, k), el("td", {}, v)))),
    tight ? el("div", { class: "hint warn" },
      `⚠ The solver limit (${gb(sv.memory_mb)}) is close to the ${wsl ? "WSL VM's" : "machine's"} `
      + `${gb(ceiling)}. If a run is killed rather than failing cleanly, this is why.`) : null,

    el("div", { class: "sec" },
      el("span", { class: "lbl" }, "Change threads and memory"),
      el("p", { class: "hint" },
        "Set these before starting Lattice, then restart it:"),
      el("pre", { class: "codeblock" },
        "set LATTICE_NCPUS=12\nset LATTICE_MEMORY_MB=10000\npython -m lattice_fea"),
      el("p", { class: "hint" },
        "Or put them in lattice.toml next to where you launch, so they stick:"),
      el("pre", { class: "codeblock" },
        "[solver]\nncpus = 12\nmemory_mb = 10000\ntime_limit_s = 14400")),

    wsl ? el("div", { class: "sec" },
      el("span", { class: "lbl" }, "Give WSL more RAM (the real ceiling)"),
      el("p", { class: "hint" },
        "WSL2 takes about half the machine's RAM by default, so it — not Windows — "
        + "is usually what limits a solve. Create or edit "),
      el("pre", { class: "codeblock" },
        "%UserProfile%\\.wslconfig\n\n[wsl2]\nmemory=12GB\nprocessors=12"),
      el("p", { class: "hint" },
        "Then run wsl --shutdown and start Lattice again. Leave a few GB for "
        + "Windows itself; the solver memory limit above should stay below this number.")) : null,

    el("div", { class: "sec" },
      el("span", { class: "lbl" }, "How much do I need?"),
      el("p", { class: "hint" },
        "The Mesh panel estimates factorization memory for the current mesh. "
        + "Modal needs noticeably more than static — it holds the factorization, "
        + "the mass matrix and every extracted mode at once. More threads speed up "
        + "MUMPS but do not reduce memory.")),

    el("div", { class: "btnrow" },
      el("button", { class: "btn", onclick: () => A.recheckSolver() }, "Recheck solver"),
      el("button", { class: "btn btn-accent", onclick: () => closeDialog() }, "Close")));

  openDialog(body);
}

function openDialog(node) {
  closeDialog();
  const ov = el("div", { class: "overlay", id: "dialog",
                         onclick: (e) => { if (e.target.id === "dialog") closeDialog(); } },
    el("div", { class: "modal" }, node));
  document.body.append(ov);
  document.addEventListener("keydown", escClose);
}
function closeDialog() {
  document.getElementById("dialog")?.remove();
  document.removeEventListener("keydown", escClose);
}
function escClose(e) { if (e.key === "Escape") closeDialog(); }

document.getElementById("btnTheme").addEventListener("click", () => {
  const root = document.documentElement;
  const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  localStorage.setItem("lattice-theme", next);
  viewer.applyTheme();
  const R = S.activeResult;
  if (R?.payload) renderLegend(R.payload.min, R.payload.max,
    document.getElementById("legendCap").textContent);
});

document.getElementById("newForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("npName").value.trim();
  const file = document.getElementById("npFile").files[0];
  const status = document.getElementById("npStatus");
  if (!name || !file) return;
  status.textContent = "Uploading…";
  try {
    const { id, job } = await api.upload("/api/projects", { name, step: file });
    status.textContent = "Importing geometry…";
    const poll = async () => {
      const j = await api.get(`/api/jobs/${job}`);
      status.textContent = j.log[j.log.length - 1] || "working…";
      if (j.status === "running") { setTimeout(poll, 700); return; }
      if (j.status === "done") await openProject(id);
      else status.textContent = `Import failed: ${j.error}`;
    };
    poll();
  } catch (err) { status.textContent = `Upload failed: ${err.message}`; }
});

// ---------------- viewport controls ----------------
document.getElementById("btnFit").addEventListener("click", () => viewer.fit());

const btnResMesh = document.getElementById("btnResMesh");
btnResMesh.addEventListener("click", () => {
  const on = btnResMesh.getAttribute("aria-pressed") !== "true";
  btnResMesh.setAttribute("aria-pressed", String(on));
  viewer.setResultMesh(on);
});

const btnProbe = document.getElementById("btnProbe");
btnProbe.addEventListener("click", () => {
  const on = btnProbe.getAttribute("aria-pressed") !== "true";
  btnProbe.setAttribute("aria-pressed", String(on));
  viewer.probeMode = on;
  if (!on) document.getElementById("nodeProbe").hidden = true;
});
const clipAxis = document.getElementById("clipAxis");
const clipPos = document.getElementById("clipPos");
clipAxis.addEventListener("change", () => {
  clipPos.style.display = clipAxis.value ? "" : "none";
  viewer.setClip(clipAxis.value || null, Number(clipPos.value) / 1000);
});
clipPos.addEventListener("input", () => {
  viewer.setClip(clipAxis.value || null, Number(clipPos.value) / 1000);
});

// The UI and the Python process are versioned together. If the server was
// started before a `git pull`, it is still running the old code in memory —
// restarting it is the fix, and this makes that state visible instead of
// looking like a mysteriously dead button.
const UI_BUILD = "0.10.0";

function checkVersionSkew() {
  const server = S.config?.version;
  const brand = document.querySelector(".brand b");
  if (brand) brand.title = `UI ${UI_BUILD} · server ${server}`;
  if (!server || server === UI_BUILD) return;
  const bar = document.createElement("div");
  bar.className = "skewbar";
  bar.textContent =
    `Version mismatch — browser UI ${UI_BUILD}, server ${server}. ` +
    `Restart the Lattice server (Ctrl+C, then re-run it), then hard-refresh this page.`;
  document.getElementById("app").prepend(bar);
}

function updateSolverChip() {
  const chip = document.getElementById("solverChip");
  const chipText = document.getElementById("solverChipText");
  const sv = S.config?.solver;
  if (!sv) return;
  // cores + memory the solver is actually allowed, next to its status
  const res = document.getElementById("solverRes");
  if (res) {
    if (sv.available) {
      const gb = (mb) => (mb / 1024).toFixed(1);
      const ceiling = sv.mode === "wsl" && sv.vm_ram_mb ? sv.vm_ram_mb : sv.host_ram_mb;
      res.textContent = `${sv.ncpus}/${sv.host_cores || "?"} cores · `
        + `${gb(sv.memory_mb)}${ceiling ? " / " + gb(ceiling) : ""} GB`;
      res.hidden = false;
    } else {
      res.hidden = true;
    }
  }
  if (sv.demo) {
    chip.querySelector(".dot").className = "dot run";
    chipText.textContent = "DEMO SOLVER — results are fake";
    if (!document.querySelector(".demobar")) {
      const bar = document.createElement("div");
      bar.className = "skewbar demobar";
      bar.textContent =
        "Demo solver: results below are fabricated to exercise the interface, " +
        "not computed. Never use them for engineering decisions.";
      document.getElementById("app").prepend(bar);
    }
  } else if (sv.available) {
    chip.querySelector(".dot").className = "dot ok";
    chipText.textContent = `code_aster · ${sv.mode}${sv.wsl_distro ? " · " + sv.wsl_distro : ""}`;
  } else {
    chip.querySelector(".dot").className = "dot bad";
    chipText.textContent = "no solver — demo mode";
  }
  chip.title = sv.detail + (sv.notes?.length ? "\n" + sv.notes.join("\n") : "");
  const ov = document.getElementById("ovSolver");
  if (ov) {
    ov.textContent = sv.available
      ? `Solver: ${sv.detail}`
      : `⚠ ${sv.detail} — meshing and setup still work; see README to enable solving.`;
  }
}

// ---------------- boot ----------------
async function boot() {
  const saved = localStorage.getItem("lattice-theme");
  if (saved) document.documentElement.setAttribute("data-theme", saved);

  viewer = new Viewer(document.getElementById("scene"), {
    onHover: (h) => {
      document.getElementById("vpHover").textContent =
        h ? `face ${h.tag} · ${fmtVal(h.area)} mm²` : "";
    },
    onProbe: (p) => {
      const box = document.getElementById("nodeProbe");
      if (!p) { box.hidden = true; return; }
      const R = S.activeResult;
      const meta = R ? S.results[R.aid] : null;
      const f = meta?.fields.find((x) => x.name === R.field);
      const unit = f?.kind === "DEPL" ? "mm" : "MPa";
      box.innerHTML =
        `<b>${fmtVal(p.value)} ${unit}</b>` +
        `<span>${p.xyz.map((v) => fmtVal(v)).join(", ")}</span>`;
      box.style.left = `${p.screen[0]}px`;
      box.style.top = `${p.screen[1]}px`;
      box.hidden = false;
    },
    onPickChange: () => {},
    onPickPoint: (pt) => {
      if (pickCtx?.probe) {
        Object.assign(pickCtx.probe, { x: pt.x, y: pt.y, z: pt.z });
        viewer.endPick();
        pickCtx = null;
        hidePickBar();
        scheduleSave();
        refresh();
      }
    },
  });

  S.config = await api.get("/api/config");
  S.library = await api.get("/api/materials");

  updateSolverChip();
  checkVersionSkew();
  await showOverlay();
}

// debug / scripting handle
window.lattice = { S, A, viewer: () => viewer };

boot().catch((e) => {
  document.getElementById("ovSolver").textContent = `Failed to start: ${e.message}`;
  document.getElementById("overlay").hidden = false;
});
