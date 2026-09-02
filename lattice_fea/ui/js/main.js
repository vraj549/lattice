import { api } from "./api.js";
import { Viewer } from "./viewer.js";
import { renderPanel, defaultAnalysis, solutionItems, el, solidName,
         panelIsFrozen, setPanelThaw } from "./ui.js";
import { renderTree, installTreeKeys } from "./tree.js";
import { mapBoltToTarget, defaultReferenceFace, describeFace,
         claimedFaces } from "./pattern.js";
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
  expanded: {},              // tree node key -> open?
};

let viewer = null;
let pickCtx = null;          // {item, key} | {probe}

// ---------------- actions ----------------
const A = {
  // Selecting is now only selecting. It used to jump straight into results
  // and switch the viewport, so you could not open an analysis to edit it.
  select(kind, id) {
    S.selection = { kind, id };
    if (kind === "result") A.ensureActiveResult(String(id).split("|")[0]);
    refresh();
  },

  /** Expand or collapse one tree node. Persisted per project, because a
   *  collapsed branch is a decision about how you want to work, not a
   *  transient view state that should reset on every reload. */
  toggleNode(key, open) {
    S.expanded[key] = open;
    if (S.project) {
      try {
        localStorage.setItem(`lattice-tree-${S.project.id}`, JSON.stringify(S.expanded));
      } catch { /* private browsing, quota — not worth failing over */ }
    }
    refresh();
    // keep focus on the row that was just toggled
    requestAnimationFrame(() => {
      const row = document.querySelector(`.tnode[data-key="${CSS.escape(key)}"]`);
      if (row) { row.tabIndex = 0; row.focus({ preventScroll: true }); }
    });
  },

  openInsertMenu(anchor, items) { showMenu(anchor, items); },

  /** Point the result controls at this analysis, defaulting to its first
   *  real field. Without this, opening a result panel left activeResult null
   *  and every contour action quietly no-opped. */
  ensureActiveResult(aid) {
    if (S.activeResult?.aid === aid) return S.activeResult;
    const f = S.results[aid]?.fields?.find((x) => x.part !== "I");
    S.activeResult = {
      aid, field: f?.name, stepIdx: 0, defMult: 1,
      comp: f?.kind === "DEPL" ? "MAG" : (f?.comps?.[0] || ""),
    };
    return S.activeResult;
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
    const setup = S.project?.setup;
    if (!setup) return null;
    const list = setup.analyses || [];
    const { kind, id } = S.selection;
    if (kind === "analysis") return list.find((a) => a.id === id);
    if (kind === "result") {
      const aid = String(id).split("|")[0];
      return list.find((a) => a.id === aid);
    }
    if (kind === "support" || kind === "load") return A.findAnalysisOf(kind, id).analysis;
    return list[0];
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
  /** Turn the interfaces found at import into editable contacts. */
  detectContacts() {
    const geo = S.project.geometry;
    const pairs = geo.contact_pairs || [];
    const setup = S.project.setup;
    setup.contacts ||= [];
    if (!pairs.length) {
      if (geo.fragmented) {
        logLine("This assembly was imported CONJOINED: coincident faces were "
              + "merged, so the parts share nodes and are already bonded. "
              + "Re-import with separate parts to define sliding contact.",
                "warnln");
      } else {
        logLine("No touching faces were found between parts.", "warnln");
      }
      return;
    }
    const seen = new Set(setup.contacts.map(
      (c) => (c.faces_a || []).join() + "|" + (c.faces_b || []).join()));
    let made = 0;
    for (const pr of pairs) {
      const key = pr.faces_a.join() + "|" + pr.faces_b.join();
      if (seen.has(key)) continue;
      const nm = (t) => solidName(S, t);
      setup.contacts.push({
        id: uid(), name: nm(pr.solids[0]) + " \u2194 " + nm(pr.solids[1]),
        kind: "bonded", mu: 0.2, solve: "linear", solids: pr.solids,
        faces_a: pr.faces_a, faces_b: pr.faces_b, area: pr.area,
      });
      made++;
    }
    A.mutate(() => {});
    logLine(made
      ? "Detected " + made + " contact interface(s), all set to bonded. "
        + "Change any that can slide or separate."
      : "Every detected interface already has a contact.");
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
  addAnalysis() { showAnalysisDialog(); },

  createAnalysis(type, { name, excitation } = {}) {
    const a = defaultAnalysis(type);
    if (name) a.name = name;
    if (type === "harmonic" && excitation) a.config.excitation = excitation;
    a.supports = []; a.loads = [];
    S.project.setup.analyses.push(a);
    S.selection = { kind: "analysis", id: a.id };
    S.expanded[`an:${a.id}`] = true;   // a new branch opens to show its parts
    A.mutate(() => {});
    A.addSupport(a.id);          // every analysis needs at least one support
  },
  /** Copy an item in place, keeping every setting. */
  duplicateItem(listName, id) {
    const { list, index } = A.locateItem(listName, id);
    if (!list || index < 0) return;
    const copy = structuredClone(list[index]);
    copy.id = uid();
    copy.name = nextCopyName(list, list[index].name || listName);
    list.splice(index + 1, 0, copy);
    S.selection = { kind: SINGULAR[listName] || listName, id: copy.id };
    A.mutate(() => {});
    logLine(`Duplicated “${list[index].name || listName}” → “${copy.name}”.`);
  },

  locateItem(listName, id) {
    if (listName === "supports" || listName === "loads") {
      const { analysis } = A.findAnalysisOf(SINGULAR[listName], id);
      const list = analysis?.[listName];
      return { list, index: list ? list.findIndex((x) => x.id === id) : -1 };
    }
    const list = S.project.setup[listName] || [];
    return { list, index: list.findIndex((x) => x.id === id) };
  },

  /** Copy a bolt onto other holes: pick targets, one new bolt per face. */
  patternBolt(bid) {
    const bl = S.project.setup.bolts.find((x) => x.id === bid);
    if (!bl) return;
    const geo = S.project.geometry;
    const ref = bl.ref_faces?.[0] ?? defaultReferenceFace(geo, bl);
    if (!ref) { logLine("This bolt has no reference face to measure from.", "warnln"); return; }
    pickCtx = { patternBolt: bl, ref };
    setView("geometry");
    viewer.startPickFaces([]);
    showPickBar(`Pick target holes for “${bl.name || "bolt"}” — reference: ${describeFace(geo, ref)}`);
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

  /** Define a material that is not in the library. */
  newMaterial(assignTo) {
    showMaterialDialog(null, assignTo);
  },
  editMaterial(mid) {
    const m = S.project.setup.materials.find((x) => x.id === mid);
    if (m) showMaterialDialog(m, null);
  },

  toggleSolid(tag) {
    if (S.hiddenSolids.has(tag)) S.hiddenSolids.delete(tag);
    else S.hiddenSolids.add(tag);
    viewer.setHiddenSolids(S.hiddenSolids);
    refresh();
  },

  /** Show this one and nothing else. The fastest way to a buried face. */
  isolateSolid(tag) {
    const all = (S.project.geometry.solids || []).map((s) => s.tag);
    S.hiddenSolids = new Set(all.filter((t) => t !== tag));
    viewer.setHiddenSolids(S.hiddenSolids);
    refresh();
  },

  showAllSolids() {
    S.hiddenSolids = new Set();
    viewer.setHiddenSolids(S.hiddenSolids);
    refresh();
  },

  /**
   * Rename a solid.
   *
   * Stored in setup.solid_names, keyed by tag, because geometry is re-derived
   * from the STEP on import and setup is the user's own data — the same place
   * material assignments already live. Blank clears it back to "Solid <tag>"
   * rather than storing an empty string that would render as nothing.
   */
  renameSolid(tag, name) {
    const names = (S.project.setup.solid_names ||= {});
    const v = String(name || "").trim();
    if (v) names[String(tag)] = v;
    else delete names[String(tag)];
    A.saveOnly();
    renderTree(S, A);
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
        // a new mesh invalidates every result that was computed on the old one
        await refreshResultStatus();
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

  /** Load a run's metadata. `select` picks the first solution item in the
   *  tree — wanted after a run finishes, unwanted when a panel is merely
   *  filling in data for a row the user already clicked. */
  async openResults(aid, { select = true, show = true } = {}) {
    if (S.resultsPending?.[aid]) return;
    (S.resultsPending ||= {})[aid] = true;
    try {
      S.results[aid] = await api.get(`/api/projects/${S.project.id}/results/${aid}`);
      S.runStatus[aid] = "done";
      S.activeResult = null;
      const f = A.ensureActiveResult(aid).field
        ? S.results[aid].fields.find((x) => x.name === S.activeResult.field) : null;
      if (select) {
        const a = S.project.setup.analyses.find((x) => x.id === aid);
        const first = a ? solutionItems(S, a)[0] : null;
        S.selection = first ? { kind: "result", id: `${aid}|${first.what}` }
                            : { kind: "analysis", id: aid };
      }
      refresh();
      if (f && show) await A.loadField(aid);
    } catch (e) {
      logLine(`results: ${e.message}`, "badln");
    } finally { S.resultsPending[aid] = false; }
  },

  /** Hand the browser a CSV. The endpoint sets Content-Disposition, so a
   *  plain navigation downloads it rather than replacing the app. */
  exportResults(aid, what = "all") {
    const url = `/api/projects/${S.project.id}/results/${aid}/export?what=${encodeURIComponent(what)}`;
    downloadUrl(url);
    logLine(`export: ${what} — check your downloads folder.`);
  },

  /** Every nodal value of the field currently on screen. */
  exportField(aid) {
    const R = S.activeResult;
    const meta = S.results[aid];
    if (!R || R.aid !== aid || !meta) { logLine("Show a field first.", "warnln"); return; }
    const f = meta.fields.find((x) => x.name === R.field) || meta.fields[0];
    if (!f) return;
    const step = f.steps[Math.min(R.stepIdx || 0, f.steps.length - 1)];
    downloadUrl(`/api/projects/${S.project.id}/results/${aid}/field.csv` +
                `?name=${encodeURIComponent(f.name)}&step=${encodeURIComponent(step.key)}`);
    logLine(`export: ${f.name} nodal values — this can be a large file.`);
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
    const R = A.ensureActiveResult(aid);
    const meta = S.results[aid];
    if (!meta) return;
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

  async loadSizing(aid) {
    if (S.sizingPending?.[aid]) return;
    (S.sizingPending ||= {})[aid] = true;
    try {
      (S.sizing ||= {})[aid] =
        await api.get(`/api/projects/${S.project.id}/results/${aid}/bolt-sizing`);
      refresh();
    } catch (e) {
      logLine(`bolt sizing: ${e.message}`, "badln");
    } finally { S.sizingPending[aid] = false; }
  },

  /** Sizing assumptions live with the model, not the run — they are how you
   *  read the result, and they should survive a re-solve. */
  setSizing(aid, patch) {
    const cfg = (S.project.setup.bolt_sizing ||= {});
    Object.assign(cfg, patch);
    scheduleSave();
    delete (S.sizing || {})[aid];
    A.loadSizing(aid);
  },

  async loadSlip(aid) {
    if (S.slipPending?.[aid]) return;
    (S.slipPending ||= {})[aid] = true;
    try {
      const r = await api.get(`/api/projects/${S.project.id}/results/${aid}/slip`);
      (S.slipResults ||= {})[aid] = r;
      refresh();
    } catch (e) {
      logLine(`slip check: ${e.message}`, "badln");
    } finally { S.slipPending[aid] = false; }
  },

  async loadShock(aid) {
    if (S.shockPending?.[aid]) return;
    (S.shockPending ||= {})[aid] = true;
    try {
      const r = await api.get(`/api/projects/${S.project.id}/results/${aid}/shock`);
      (S.shockResults ||= {})[aid] = r;
      refresh();
    } catch (e) {
      logLine(`shock response: ${e.message}`, "badln");
    } finally { S.shockPending[aid] = false; }
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
    // the slider label reports the factor, so it has to follow the value —
    // but only when nothing is being typed into the panel
    if (!panelIsFrozen()) renderPanel(S, A);
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

// ---------------- insert menu ----------------
// The "+" on a tree row. Options that do not apply to the node are shown
// disabled with the reason, rather than hidden — a missing option reads as a
// bug, an explained one teaches how the tool works.

function showMenu(anchor, items) {
  closeMenu();
  // A row scrolled out of the tree still has a rect, so a menu anchored to it
  // would open outside the window. Bring the row into view first.
  anchor.scrollIntoView({ block: "nearest", inline: "nearest" });
  const r = anchor.getBoundingClientRect();
  const menu = el("div", { class: "menu", id: "insertMenu", role: "menu" },
    items.map((it) => el("button", {
      class: "menu-item", role: "menuitem", disabled: !!it.disabled,
      title: it.hint || null,
      onclick: () => { closeMenu(); it.onclick?.(); } },
      el("span", {}, it.label),
      it.hint ? el("span", { class: "menu-hint" }, it.hint) : null)));
  document.body.append(menu);
  // flip up when there is no room below
  const h = menu.offsetHeight;
  const below = window.innerHeight - r.bottom;
  const top = below > h + 8 ? r.bottom + 2 : r.top - h - 2;
  menu.style.left = `${Math.max(4, Math.min(r.left, window.innerWidth - menu.offsetWidth - 8))}px`;
  // clamped both ways: a menu that opens off-screen is a dead control
  menu.style.top = `${Math.max(4, Math.min(top, window.innerHeight - h - 4))}px`;
  setTimeout(() => {
    document.addEventListener("pointerdown", onMenuOutside, true);
    document.addEventListener("keydown", onMenuKey, true);
  }, 0);
}
function closeMenu() {
  document.getElementById("insertMenu")?.remove();
  document.removeEventListener("pointerdown", onMenuOutside, true);
  document.removeEventListener("keydown", onMenuKey, true);
}
function onMenuOutside(e) { if (!e.target.closest?.("#insertMenu")) closeMenu(); }
function onMenuKey(e) { if (e.key === "Escape") { e.stopPropagation(); closeMenu(); } }

function downloadUrl(url) {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.append(a);
  a.click();
  a.remove();
}

// ---------------- new-analysis dialog ----------------
// A prompt() asking the user to type "harmonic" was the least defensible
// thing in the interface: no discoverability, no explanation of what each
// study does, and a typo silently did nothing.

const ANALYSIS_TYPES = [
  { type: "static", name: "Static structural",
    blurb: "Stress and deflection under steady loads. Bolt preload acts here." },
  { type: "modal", name: "Modal",
    blurb: "Natural frequencies and mode shapes. Supports only — no loads." },
  { type: "harmonic", name: "Harmonic response",
    blurb: "Sine sweep for amplification and Q. Driven by force, or by base " +
           "acceleration the way a shaker test is run." },
  { type: "random", name: "Random vibration",
    blurb: "PSD in g²/Hz, response in g RMS and 3σ. Base-driven; needs a probe." },
  { type: "shock", name: "Shock",
    blurb: "SRS or a classical pulse — half-sine, sawtooth, trapezoid. " +
           "Peak interface load, bolt loads and probe response." },
];

function showAnalysisDialog() {
  let type = "static";
  let excitation = "base";
  const nameIn = el("input", { type: "text", placeholder: "optional" });

  const exRow = el("div", { class: "sec", hidden: true },
    el("span", { class: "lbl" }, "Driven by"),
    el("label", { class: "frm" }, "",
      el("select", { onchange: (e) => { excitation = e.target.value; } },
        el("option", { value: "base" }, "Base acceleration (shaker)"),
        el("option", { value: "force" }, "Force applied to faces"))),
    el("div", { class: "hint" },
      "A base-driven study takes no applied loads, so Lattice will not offer " +
      "them in the tree. You can change this later."));

  const cards = ANALYSIS_TYPES.map((t) => {
    const card = el("label", { class: "optcard" },
      el("input", { type: "radio", name: "atype", value: t.type,
        onchange: () => {
          type = t.type;
          exRow.hidden = t.type !== "harmonic";
          for (const c of cards) c.setAttribute("aria-selected", String(c === card));
          if (!nameIn.value.trim() || ANALYSIS_TYPES.some((x) => x.name === nameIn.value)) {
            nameIn.value = t.name;
          }
        } }),
      el("div", {},
        el("b", {}, t.name),
        el("div", { class: "hint" }, t.blurb)));
    return card;
  });
  cards[0].querySelector("input").checked = true;
  cards[0].setAttribute("aria-selected", "true");
  nameIn.value = ANALYSIS_TYPES[0].name;

  const create = () => {
    closeDialog();
    A.createAnalysis(type, { name: nameIn.value.trim(), excitation });
  };

  openDialog(el("div", {},
    el("h1", { class: "modal-title" }, "New analysis"),
    el("p", { class: "modal-sub" },
      "Each analysis owns its own supports and loads, so only what that study " +
      "needs appears under it."),
    el("div", { class: "optcards" }, ...cards),
    exRow,
    el("div", { class: "sec" },
      el("label", { class: "frm" }, "Name", nameIn)),
    el("div", { class: "btnrow" },
      el("button", { class: "btn btn-accent", onclick: create }, "Create analysis"),
      el("button", { class: "btn", onclick: () => closeDialog() }, "Cancel"))));
}

// ---------------- custom material dialog ----------------
// The library covers common stock; anything with a datasheet in hand — a
// specific temper, a filled polymer, a composite treated as isotropic — has
// to be typeable, or the tool only works on materials someone else chose.

const MAT_FIELDS = [
  ["name", "Name", "text", null],
  ["E_GPa", "Young's modulus E", "number", "GPa"],
  ["nu", "Poisson's ratio ν", "number", null],
  ["rho_kgm3", "Density", "number", "kg/m³"],
  ["yield_MPa", "Yield strength (optional)", "number", "MPa"],
];

function showMaterialDialog(existing, assignTo) {
  const draft = existing
    ? { ...existing }
    : { id: `custom-${uid()}`, name: "", E_GPa: null, nu: 0.3,
        rho_kgm3: null, yield_MPa: null };
  const err = el("div", { class: "hint bad" });
  const inputs = {};

  const body = el("div", {},
    el("h1", { class: "modal-title" }, existing ? "Edit material" : "New material"),
    el("p", { class: "modal-sub" },
      "Linear elastic and isotropic — the only model Lattice solves. Values "
      + "are stored with the project, not in the shared library."),
    el("div", { class: "sec" },
      ...MAT_FIELDS.map(([key, label, type, unit]) => {
        const inp = el("input", {
          type, step: "any", value: draft[key] ?? "",
          oninput: (e) => {
            draft[key] = type === "number"
              ? (e.target.value === "" ? null : Number(e.target.value))
              : e.target.value;
          },
        });
        inputs[key] = inp;
        return el("label", { class: "frm" }, unit ? `${label} (${unit})` : label, inp);
      }),
      err),
    el("div", { class: "btnrow" },
      el("button", { class: "btn btn-accent", onclick: () => {
        const problem = validateMaterial(draft);
        if (problem) { err.textContent = "⚠ " + problem; return; }
        const list = S.project.setup.materials;
        const i = list.findIndex((m) => m.id === draft.id);
        if (i >= 0) list[i] = draft; else list.push(draft);
        if (assignTo != null) S.project.setup.assignments[String(assignTo)] = draft.id;
        closeDialog();
        A.mutate(() => {});
        logLine(`Material “${draft.name}” saved.`);
      } }, existing ? "Save" : "Create"),
      el("button", { class: "btn", onclick: () => closeDialog() }, "Cancel")));
  openDialog(body);
  setTimeout(() => inputs.name?.focus(), 0);
}

/** Mirrors the check the solver-unit conversion makes, so a bad number is
 *  caught while it is still editable rather than at solve time. */
function validateMaterial(m) {
  if (!String(m.name || "").trim()) return "Give the material a name.";
  if (!(m.E_GPa > 0)) return "Young's modulus must be greater than zero.";
  if (m.nu == null || !(m.nu > -1 && m.nu < 0.5)) {
    return "Poisson's ratio must be between -1 and 0.5. At 0.5 the material "
         + "is incompressible and the stiffness matrix is singular.";
  }
  if (!(m.rho_kgm3 > 0)) {
    return "Density must be greater than zero — modal and harmonic analyses "
         + "need mass.";
  }
  return null;
}

// ---------------- pick bar ----------------
function showPickBar(msg) {
  const bar = document.getElementById("pickBar");
  document.getElementById("pickMsg").textContent = msg;
  bar.hidden = false;
}
function hidePickBar() { document.getElementById("pickBar").hidden = true; }

document.getElementById("pickDone").addEventListener("click", () => {
  if (pickCtx?.patternBolt) {
    applyBoltPattern(pickCtx.patternBolt, pickCtx.ref, viewer.endPick());
  } else if (pickCtx?.item) {
    pickCtx.item[pickCtx.key] = viewer.endPick();
    scheduleSave();
  } else viewer.endPick();
  pickCtx = null;
  hidePickBar();
  refresh();
});

/**
 * Turn each picked face into a copy of the template bolt.
 *
 * Every outcome is reported. A hole that already has a bolt is skipped, and a
 * target whose mating face could not be identified still produces a bolt —
 * flagged, and left in the tree with "pick faces" on it — because silently
 * creating a one-sided joint would be a load path that does not exist.
 */
function applyBoltPattern(template, ref, targets) {
  const geo = S.project.geometry;
  const claimed = claimedFaces(S.project.setup.bolts);
  let made = 0;
  const skipped = [];
  const partial = [];

  for (const t of targets) {
    const tag = Number(t);
    if (claimed.has(tag)) { skipped.push(`${describeFace(geo, tag)} already has a bolt`); continue; }
    const m = mapBoltToTarget(geo, template, ref, tag);
    if (!m.ok) { skipped.push(`${describeFace(geo, tag)}: ${m.warnings.join("; ")}`); continue; }

    const bolt = {
      ...structuredClone(template),
      id: uid(),
      name: nextCopyName(S.project.setup.bolts, template.name || "Bolt"),
      side_a_faces: m.side_a_faces,
      side_b_faces: m.side_b_faces,
      ref_faces: [],
    };
    S.project.setup.bolts.push(bolt);
    for (const f of [...m.side_a_faces, ...m.side_b_faces]) claimed.add(f);
    made++;
    if (m.warnings.length) partial.push(`${bolt.name}: ${m.warnings.join("; ")}`);
  }

  if (made) {
    logLine(`Pattern: created ${made} bolt${made === 1 ? "" : "s"} from ` +
            `“${template.name || "bolt"}” at ${fmtVal(template.preload_N || 0)} N.`);
  }
  for (const s of skipped) logLine(`  skipped — ${s}`, "warnln");
  for (const p of partial) logLine(`  incomplete — ${p}`, "warnln");
  if (!made) logLine("Pattern: nothing created.", "warnln");
  if (made) logLine("  Re-mesh before running: bolt beams are built at mesh time.", "warnln");
  scheduleSave();
}

/** "Bolt @25" -> "Bolt @25 (2)", avoiding names already in the list. */
function nextCopyName(list, base) {
  const stem = String(base).replace(/\s*\(\d+\)$/, "");
  const taken = new Set(list.map((x) => x.name));
  for (let i = 2; i < 999; i++) {
    const name = `${stem} (${i})`;
    if (!taken.has(name)) return name;
  }
  return `${stem} copy`;
}

const SINGULAR = { bolts: "bolt", ties: "tie", probes: "probe",
                   supports: "support", loads: "load", contacts: "contact" };
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
  try {
    await api.put(`/api/projects/${S.project.id}/setup`, S.project.setup);
    await refreshResultStatus();
  } catch (e) { logLine(`save failed: ${e.message}`, "badln"); }
}

/** Re-check whether existing results still match the model.
 *
 *  The server owns the comparison — a second implementation here would
 *  eventually disagree with it, and the whole point of the badge is that it
 *  is trustworthy. */
async function refreshResultStatus() {
  if (!S.project) return;
  try {
    const st = await api.get(`/api/projects/${S.project.id}/results-status`);
    let changed = false;
    for (const [aid, s] of Object.entries(st)) {
      const meta = S.results[aid];
      if (!meta) continue;
      if (meta.stale !== s.stale) { meta.stale = s.stale; changed = true; }
      meta.no_signature = s.no_signature;
    }
    // an analysis whose results vanished from disk should stop claiming them
    for (const aid of Object.keys(S.results)) {
      if (!st[aid] && S.results[aid]) { delete S.results[aid]; changed = true; }
    }
    if (changed) refresh();
  } catch { /* status is advisory; never let it break editing */ }
}

// ---------------- views ----------------
function setView(v) {
  S.view = v;
  for (const b of document.querySelectorAll(".vtab")) {
    b.setAttribute("aria-selected", String(b.dataset.view === v));
  }
  renderLegend(null);
  if (v === "geometry") { viewer.showGeometry(); updateStat(); }
  syncExplodeControl();     // also here: switching views is not a model edit
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
    // prefer results for whatever the user is looking at, not an arbitrary run
    const cur = A.currentAnalysis?.();
    const aid = (cur && S.results[cur.id]) ? cur.id
      : Object.keys(S.results).find((k) => S.results[k]);
    if (aid) A.openResults(aid);
    else logLine("No results yet — run an analysis first.", "warnln");
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
  // While a field has focus the panel holds still — rebuilding it would
  // destroy the element being typed into. See liveInput() in ui.js.
  if (!panelIsFrozen()) renderPanel(S, A);
  viewer.setFaceStates(faceStates());
  viewer.setHighlightSolid(S.selection.kind === "solid" ? S.selection.id : null);
  updateGlyphs();
  // driven from here, not from setView: on project open setView runs before
  // the geometry is in state, so the solid count was still zero
  syncExplodeControl();
}

/**
 * Rebuild the BC symbols only when something they draw actually changed.
 *
 * This runs on every model edit, and each glyph allocates cone/cylinder/tube
 * geometries — rebuilding them on every keystroke was both the largest source
 * of garbage in the app and pure wasted work, since typing a preload value
 * does not move an arrow.
 */
function updateGlyphs() {
  const a = A.currentAnalysis();
  const setup = S.project.setup;
  const sig = JSON.stringify([
    a?.id,
    (a?.supports || []).map((s) => [s.type, s.faces, s.ux, s.uy, s.uz]),
    (a?.loads || []).map((l) => [l.type, l.faces, l.fx, l.fy, l.fz,
                                 l.mx, l.my, l.mz, l.x, l.y, l.z, l.g, l.g_mag,
                                 l.axis, l.center, l.pressure]),
    (setup.bolts || []).map((b) => [b.side_a_faces, b.side_b_faces]),
    (setup.probes || []).map((p) => [p.x, p.y, p.z]),
  ]);
  if (sig === S.glyphSig) return;
  S.glyphSig = sig;
  viewer.setGlyphs({ ...setup, ...(a || { supports: [], loads: [] }) },
                   S.project.geometry);
}

// ---------------- project bootstrap ----------------
async function openProject(pid) {
  S.project = await api.get(`/api/projects/${pid}`);
  document.getElementById("projName").textContent = S.project.name;
  document.getElementById("overlay").hidden = true;
  S.results = {}; S.runStatus = {}; S.meshData = null; S.activeResult = null;
  S.selection = { kind: "model", id: "root" };
  try { S.expanded = JSON.parse(localStorage.getItem(`lattice-tree-${pid}`) || "{}"); }
  catch { S.expanded = {}; }

  S.tess = await api.get(`/api/projects/${pid}/tessellation`);
  viewer.setGeometry(S.tess, S.project.geometry);

  try { S.meshData = await api.get(`/api/projects/${pid}/mesh`); } catch { /* not meshed */ }

  // Discover existing results. Ask which analyses have any first, rather than
  // probing each one and eating a 404 for every analysis that has never run.
  try {
    const status = await api.get(`/api/projects/${pid}/results-status`);
    for (const aid of Object.keys(status)) {
      S.results[aid] = await api.get(`/api/projects/${pid}/results/${aid}`);
      S.runStatus[aid] = "done";
    }
  } catch (e) { logLine(`results: ${e.message}`, "warnln"); }
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

// ---------------- resizable panes ----------------
const PANE = { minL: 150, maxL: 560, minR: 200, maxR: 680, minViewport: 300 };

// What the user asked for, kept separate from what currently fits. Clamping
// straight onto the live width was a one-way ratchet: shrink the window once
// and the panes stayed narrow forever, because the clamped value became the
// new intent.
const paneWant = { L: 264, R: 300 };

function paneWidths() {
  const body = document.getElementById("body");
  const cs = getComputedStyle(body);
  return {
    body,
    total: body.clientWidth || window.innerWidth,
    L: parseFloat(cs.getPropertyValue("--wL")) || paneWant.L,
    R: parseFloat(cs.getPropertyValue("--wR")) || paneWant.R,
  };
}

/** Apply the wanted widths, reduced only as far as this window requires.
 *
 *  Pane sizes persist across sessions, so a layout set up on a wide monitor
 *  would otherwise reopen on a laptop with the viewport crushed to nothing —
 *  and the splitters pushed off-screen, leaving no way to undo it. */
function clampPanes() {
  const body = document.getElementById("body");
  const total = body.clientWidth || window.innerWidth;
  let l = Math.min(Math.max(paneWant.L, PANE.minL), PANE.maxL);
  let r = Math.min(Math.max(paneWant.R, PANE.minR), PANE.maxR);
  const spare = total - PANE.minViewport - 8;   // 8px for the two splitters
  if (l + r > spare) {
    const scale = Math.max(spare, PANE.minL + PANE.minR) / (l + r);
    l = Math.max(Math.floor(l * scale), PANE.minL);
    r = Math.max(Math.floor(r * scale), PANE.minR);
  }
  body.style.setProperty("--wL", `${Math.round(l)}px`);
  body.style.setProperty("--wR", `${Math.round(r)}px`);
}

function savePanes() {
  localStorage.setItem("lattice-panes", JSON.stringify({
    wL: paneWant.L, wR: paneWant.R,
    logH: document.getElementById("logDrawer").style.height,
  }));
}

function initSplitters() {
  const body = document.getElementById("body");
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("lattice-panes") || "{}"); } catch { /* ignore */ }
  if (saved.wL) paneWant.L = parseFloat(saved.wL) || paneWant.L;
  if (saved.wR) paneWant.R = parseFloat(saved.wR) || paneWant.R;
  if (saved.logH) document.getElementById("logDrawer").style.height = saved.logH;
  clampPanes();
  window.addEventListener("resize", () => { clampPanes(); viewer?.resize(); });

  const drag = (el, onMove, vertical) => {
    el.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      el.setPointerCapture(e.pointerId);
      el.classList.add("dragging");
      document.body.classList.add(vertical ? "resizing-v" : "resizing");
      const move = (ev) => onMove(ev);
      const up = () => {
        el.classList.remove("dragging");
        document.body.classList.remove("resizing", "resizing-v");
        el.removeEventListener("pointermove", move);
        el.removeEventListener("pointerup", up);
        savePanes();
        viewer.resize();
      };
      el.addEventListener("pointermove", move);
      el.addEventListener("pointerup", up);
    });
  };

  drag(document.getElementById("splitL"), (e) => {
    const { total, R } = paneWidths();
    const max = Math.min(PANE.maxL, total - R - PANE.minViewport - 8);
    const w = Math.min(Math.max(e.clientX - body.getBoundingClientRect().left, PANE.minL),
                       Math.max(max, PANE.minL));
    paneWant.L = w;
    clampPanes();
    viewer.resize();
  });
  drag(document.getElementById("splitR"), (e) => {
    const { total, L } = paneWidths();
    const max = Math.min(PANE.maxR, total - L - PANE.minViewport - 8);
    const w = Math.min(Math.max(body.getBoundingClientRect().right - e.clientX, PANE.minR),
                       Math.max(max, PANE.minR));
    paneWant.R = w;
    clampPanes();
    viewer.resize();
  });
  // The separators are focusable, so they have to be operable from the
  // keyboard too — arrows nudge, Home resets to the default layout.
  const keys = (id, apply) => {
    document.getElementById(id).addEventListener("keydown", (e) => {
      const step = e.shiftKey ? 40 : 8;
      if (e.key === "ArrowLeft" || e.key === "ArrowUp") apply(-step);
      else if (e.key === "ArrowRight" || e.key === "ArrowDown") apply(step);
      else if (e.key === "Home") apply(null);
      else return;
      e.preventDefault();
      clampPanes();
      viewer.resize();
      savePanes();
    });
  };
  keys("splitL", (d) => { paneWant.L = d === null ? 264 : paneWant.L + d; });
  keys("splitR", (d) => { paneWant.R = d === null ? 300 : paneWant.R - d; });
  keys("splitLog", (d) => {
    const log = document.getElementById("logDrawer");
    const h = log.getBoundingClientRect().height;
    log.style.height = (d === null ? 148 : Math.max(60, h - d)) + "px";
  });

  drag(document.getElementById("splitLog"), (e) => {
    const log = document.getElementById("logDrawer");
    if (log.dataset.open === "false") log.dataset.open = "true";
    const h = Math.min(Math.max(window.innerHeight - e.clientY, 60), window.innerHeight * 0.6);
    log.style.height = h + "px";
    viewer.resize();
  }, true);
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
    ["Engines available",
     (sv.engines || []).map((e) => e.label).join(", ") || "none"],
    ["code_aster mode", sv.mode + (sv.wsl_distro ? ` · ${sv.wsl_distro}` : "")],
    ...(sv.ccx ? [["CalculiX", `${sv.ccx_cmd} · ${sv.ccx_threads} thread`
        + (sv.ccx_threads === 1 ? " (multithreaded ccx can return wrong results)" : "s")]] : []),
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
    const assembly = document.getElementById("npAssembly")?.value || "bonded";
    const { id, job } = await api.upload("/api/projects",
                                         { name, step: file, assembly });
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

// Explode. Interior faces are the ones contacts are made of, and on a stack
// of parts they are exactly the faces you cannot click on.
const btnExplode = document.getElementById("btnExplode");
const explodePos = document.getElementById("explodePos");

function applyExplode() {
  const on = btnExplode.getAttribute("aria-pressed") === "true";
  explodePos.style.display = on ? "" : "none";
  viewer.setExplode(on ? Number(explodePos.value) / 1000 : 0);
}
btnExplode.addEventListener("click", () => {
  btnExplode.setAttribute("aria-pressed",
    String(btnExplode.getAttribute("aria-pressed") !== "true"));
  applyExplode();
});
explodePos.addEventListener("input", applyExplode);

/** Only offer it where it means something: more than one solid, and a view
 *  that is showing the parts rather than a field. */
function syncExplodeControl() {
  const n = (S.project?.geometry?.solids || []).length;
  const ok = n > 1 && S.view === "geometry";
  btnExplode.hidden = !ok;
  if (!ok) {
    btnExplode.setAttribute("aria-pressed", "false");
    explodePos.style.display = "none";
    viewer.setExplode(0);
  }
}

// The UI and the Python process are versioned together. If the server was
// started before a `git pull`, it is still running the old code in memory —
// restarting it is the fix, and this makes that state visible instead of
// looking like a mysteriously dead button.
const UI_BUILD = "0.23.0";

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
  const engines = sv.engines || [];
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
  } else if (engines.length) {
    // Whatever can actually run goes in the chip. Reporting only code_aster
    // told a Mac with a working CalculiX that it had no solver at all.
    chip.querySelector(".dot").className = "dot ok";
    chipText.textContent = engines.map((e) => e.label).join(" + ")
      + (sv.mode === "wsl" && sv.wsl_distro ? ` · ${sv.wsl_distro}` : "");
  } else {
    chip.querySelector(".dot").className = "dot bad";
    chipText.textContent = "no solver";
  }
  chip.title = (engines.length
    ? engines.map((e) => `${e.label}: ${e.detail} — ${e.types.join(", ")}`).join("\n")
    : sv.detail) + (sv.notes?.length ? "\n" + sv.notes.join("\n") : "");
  const ov = document.getElementById("ovSolver");
  if (ov) {
    ov.textContent = engines.length
      ? `Solvers: ${engines.map((e) => `${e.label} (${e.types.join(", ")})`).join(" · ")}`
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
  initSplitters();
  installTreeKeys(S, A);
  // a field losing focus (or Enter) re-renders the panel it froze
  setPanelThaw(() => { if (S.project) renderPanel(S, A); });
  await showOverlay();
}

// debug / scripting handle
window.lattice = { S, A, viewer: () => viewer };

boot().catch((e) => {
  document.getElementById("ovSolver").textContent = `Failed to start: ${e.message}`;
  document.getElementById("overlay").hidden = false;
});
