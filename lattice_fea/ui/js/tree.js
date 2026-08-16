// The simulation tree.
//
// One continuous hierarchy, the way a production pre-processor presents a
// model: the project at the root, shared model data beneath it, and each
// analysis as a branch owning its settings, its boundary conditions and a
// Solution node that holds the individual results.
//
// The previous version was five flat groups with section headers, and results
// were a header inside the analysis rather than a node under it — so there was
// no way to collapse a finished study out of the way, and "Solution" was a
// label rather than a thing you could open.

import { el, needsLoads, analysisStatus, solutionItems, meshIssues,
         loadMeta, excitationMeta, boltSizeOf } from "./ui.js";
import { icon, RESULT_ICONS } from "./icons.js";
import { fmtVal } from "./colormap.js";

const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;

// ---------------------------------------------------------------- model

/** Build the node tree. Pure data — rendering is separate so the shape of the
 *  model is readable in one place. */
function buildModel(S, A) {
  const setup = S.project.setup;
  const geo = S.project.geometry;

  const solids = geo.solids.map((sd) => {
    const mat = setup.materials.find((m) => m.id === setup.assignments[String(sd.tag)]);
    return {
      key: `so:${sd.tag}`, kind: "solid", id: sd.tag, icon: "solid",
      label: sd.name || `Solid ${sd.tag}`,
      meta: mat ? mat.name : "no material", warn: !mat,
    };
  });
  if (geo.interfaces.length) {
    solids.push({
      key: "ifc", kind: "connections", id: "all", icon: "interface",
      label: "Bonded interfaces", meta: String(geo.interfaces.length),
    });
  }

  const conns = [
    ...setup.bolts.map((bl, i) => ({
      key: `bo:${bl.id}`, kind: "bolt", id: bl.id, icon: "bolt", iconClass: "c-bolt",
      label: bl.name || `Bolt ${i + 1}`,
      meta: bl.side_a_faces?.length && bl.side_b_faces?.length
        ? `${boltSizeOf(bl)?.id ?? "?"} · ${fmtVal(bl.preload_N || 0)} N` : "pick faces",
      warn: !(bl.side_a_faces?.length && bl.side_b_faces?.length),
    })),
    ...setup.ties.map((t, i) => ({
      key: `ti:${t.id}`, kind: "tie", id: t.id, icon: "tie", iconClass: "c-ok",
      label: t.name || `Tie ${i + 1}`,
      meta: t.slave_faces?.length && t.master_solid ? `→ solid ${t.master_solid}` : "incomplete",
      warn: !(t.slave_faces?.length && t.master_solid),
    })),
  ];

  const probes = setup.probes.map((pr, i) => ({
    key: `pr:${pr.id}`, kind: "probe", id: pr.id, icon: "probe", iconClass: "c-probe",
    label: pr.name || `Probe ${i + 1}`,
    meta: `${fmtVal(pr.x)}, ${fmtVal(pr.y)}, ${fmtVal(pr.z)}`,
  }));

  const ms = S.meshData?.stats;
  const issues = ms ? meshIssues(S) : [];
  const mesh = {
    key: "mesh", kind: "mesh", id: "mesh", icon: "mesh",
    label: "Mesh",
    meta: ms ? `${ms.nodes.toLocaleString()} n` : "not meshed",
    warn: !ms,
    badge: issues.length ? { text: "!", cls: "stale",
      title: "This mesh no longer matches the model:\n"
           + issues.map((i) => "· " + i.text).join("\n") } : null,
    title: ms ? `Tet${ms.order === 2 ? "10" : "4"} · ${fmtVal(ms.size_mm)} mm target`
              : "Not meshed yet",
  };

  const children = [
    { key: "geo", icon: "geometry", label: "Geometry",
      meta: plural(geo.faces.length, "face"), children: solids },
    { key: "con", icon: "connection", label: "Connections",
      meta: conns.length ? String(conns.length) : "none", children: conns,
      empty: "No bolts or ties — bonded interfaces are found on import.",
      insert: [
        { label: "Bolt (beam + spider)", onclick: () => A.addBolt() },
        { label: "Tie (bonded, non-conformal)", onclick: () => A.addTie(),
          disabled: geo.solids.length < 2,
          hint: geo.solids.length < 2 ? "needs two or more solids" : null },
      ] },
    { key: "prb", icon: "probe", label: "Probes",
      meta: probes.length ? String(probes.length) : "none", children: probes,
      empty: "Frequency response is extracted at probes.",
      insert: [{ label: "Probe point", onclick: () => A.addProbe() }] },
    mesh,
    ...setup.analyses.map((a) => analysisNode(S, A, a)),
  ];

  return {
    key: "model", kind: "model", id: "root", icon: "model",
    label: S.project.name, meta: plural(geo.solids.length, "solid"),
    children,
    insert: [{ label: "Analysis…", onclick: () => A.addAnalysis() }],
  };
}

function analysisNode(S, A, a) {
  a.supports ||= []; a.loads ||= [];
  const st = analysisStatus(S, a);
  const kids = [{
    key: `set:${a.id}`, kind: "settings", id: a.id, icon: "settings",
    label: "Analysis Settings", meta: settingsMeta(a),
  }];

  for (const sup of a.supports) {
    kids.push({
      key: `su:${sup.id}`, kind: "support", id: sup.id,
      icon: "support", iconClass: "c-support",
      label: sup.name || "Support",
      meta: sup.faces?.length ? plural(sup.faces.length, "face") : "no faces",
      warn: !sup.faces?.length,
    });
  }

  if (needsLoads(a)) {
    for (const l of a.loads) {
      kids.push({
        key: `lo:${l.id}`, kind: "load", id: l.id,
        icon: "load", iconClass: "c-load",
        label: l.name || "Load", meta: loadMeta(l),
        warn: !["gravity", "rotation"].includes(l.type) && !l.faces?.length,
      });
    }
  } else if (a.type !== "modal") {
    // Base-driven studies take no applied loads. Show what IS driving them,
    // pointing at the settings where its direction and level live.
    kids.push({
      key: `bx:${a.id}`, kind: "settings", id: a.id,
      icon: "load", iconClass: "c-load",
      label: "Base excitation", meta: excitationMeta(a),
    });
  }

  const items = solutionItems(S, a);
  if (items.length) {
    kids.push({
      key: `sol:${a.id}`, kind: "solution", id: a.id, icon: "solution",
      label: "Solution", badge: st.badge, title: st.title,
      children: items.map((it) => ({
        key: `re:${a.id}:${it.what}`, kind: "result", id: `${a.id}|${it.what}`,
        icon: RESULT_ICONS[it.what] || "contours",
        label: it.label, meta: it.meta, cls: "result",
      })),
      insert: [{ label: "Export all results (CSV)",
                 onclick: () => A.exportResults(a.id, "all") }],
    });
  }

  const insert = [{ label: "Support", onclick: () => A.addSupport(a.id) }];
  if (needsLoads(a)) {
    insert.push({ label: "Load", onclick: () => A.addLoad(a.id) });
  } else {
    insert.push({ label: "Load", disabled: true,
      hint: a.type === "modal" ? "modal takes no loads"
                               : "this study is driven through its supports" });
  }

  return {
    key: `an:${a.id}`, kind: "analysis", id: a.id,
    // Each study type gets its own glyph, so the tree is scannable without
    // spending a column on the word "harmonic" next to a name the user
    // already chose. Ansys distinguishes analysis systems the same way.
    icon: ANALYSIS_ICONS[a.type] || "analysis",
    label: a.name || a.type, badge: st.badge,
    title: `${TYPE_NAMES[a.type] || a.type} — ${st.title}`,
    children: kids, insert, cls: "analysis",
  };
}

const ANALYSIS_ICONS = { static: "analysis", modal: "modes",
                         harmonic: "frf", random: "random" };
const TYPE_NAMES = { static: "Static structural", modal: "Modal",
                     harmonic: "Harmonic response", random: "Random vibration" };

function settingsMeta(a) {
  const c = a.config || {};
  if (a.type === "modal") return `${c.n_modes || 10} modes`;
  if (a.type === "harmonic") return `${fmtVal(c.f_min || 0)}–${fmtVal(c.f_max || 0)} Hz`;
  if (a.type === "random") return `${(c.spec || []).length} breakpoints`;
  return "";
}

// ---------------------------------------------------------------- render

export function renderTree(S, A) {
  const host = document.getElementById("tree");
  const scroll = host.scrollTop;
  host.innerHTML = "";
  if (!S.project?.geometry) return;

  const setup = S.project.setup;
  setup.bolts ||= []; setup.ties ||= []; setup.probes ||= []; setup.analyses ||= [];

  const frag = document.createDocumentFragment();
  emit(frag, buildModel(S, A), 0, S, A);
  host.append(frag);
  host.scrollTop = scroll;   // a re-render must not throw away your position
}

function isOpen(S, node) {
  const saved = S.expanded?.[node.key];
  return saved === undefined ? node.defaultOpen !== false : saved;
}

function emit(frag, node, depth, S, A) {
  const kids = node.children || [];
  const expandable = kids.length > 0 || !!node.empty;
  const open = expandable && isOpen(S, node);
  const selected = node.kind && S.selection.kind === node.kind
    && String(S.selection.id) === String(node.id);

  const row = el("div", {
    class: `tnode${node.cls ? " " + node.cls : ""}`,
    role: "treeitem", "data-key": node.key,
    "aria-selected": selected ? "true" : "false",
    "aria-expanded": expandable ? String(open) : false,
    "aria-level": String(depth + 1),
    tabindex: selected ? "0" : "-1",
    title: node.title || null,
    onclick: (e) => {
      if (e.target.closest(".tw, .tact")) return;
      if (node.kind) A.select(node.kind, node.id);
      else if (expandable) A.toggleNode(node.key, !open);
    },
    ondblclick: (e) => {
      if (e.target.closest(".tw, .tact")) return;
      if (expandable) A.toggleNode(node.key, !open);
    },
  });

  for (let i = 0; i < depth; i++) row.append(el("span", { class: "ind" }));
  row.append(expandable
    ? el("button", { class: "tw", tabindex: "-1",
        "aria-label": open ? "Collapse" : "Expand",
        onclick: (e) => { e.stopPropagation(); A.toggleNode(node.key, !open); } },
        open ? "▾" : "▸")
    : el("span", { class: "tw" }));

  row.append(icon(node.icon, node.iconClass));
  row.append(el("span", { class: "nm" }, node.label));
  if (node.badge) {
    row.append(el("span", { class: `badge ${node.badge.cls}`, title: node.badge.title },
                  node.badge.text));
  }
  if (node.meta) row.append(el("span", { class: `mt${node.warn ? " warn" : ""}` }, node.meta));
  if (node.insert?.length) {
    row.append(el("span", { class: "tact" },
      el("button", { class: "tadd", tabindex: "-1", title: "Insert",
        onclick: (e) => { e.stopPropagation(); A.openInsertMenu(e.currentTarget, node.insert); } },
        "+")));
  }
  frag.append(row);

  if (!open) return;
  if (!kids.length && node.empty) {
    const hint = el("div", { class: "tempty" }, node.empty);
    hint.style.paddingLeft = `${12 + depth * 10 + 17}px`;
    frag.append(hint);
    return;
  }
  for (const c of kids) emit(frag, c, depth + 1, S, A);
}

// ---------------------------------------------------------------- keyboard

/** Arrow-key navigation, installed once. A tree you can only click is not a
 *  tool you can work in quickly. */
export function installTreeKeys(S, A) {
  const host = document.getElementById("tree");
  host.setAttribute("role", "tree");
  host.addEventListener("keydown", (e) => {
    const rows = [...host.querySelectorAll(".tnode")];
    const cur = document.activeElement.closest?.(".tnode") || rows.find(
      (r) => r.getAttribute("aria-selected") === "true");
    const i = rows.indexOf(cur);
    const go = (j) => {
      const t = rows[Math.max(0, Math.min(j, rows.length - 1))];
      if (t) { t.tabIndex = 0; t.focus(); }
    };
    const key = cur?.dataset.key;
    const expandable = cur?.getAttribute("aria-expanded") !== null
                    && cur?.getAttribute("aria-expanded") !== "";
    const open = cur?.getAttribute("aria-expanded") === "true";

    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(rows.length - 1);
    else if (e.key === "ArrowRight") {
      if (expandable && !open) A.toggleNode(key, true);
      else go(i + 1);
    } else if (e.key === "ArrowLeft") {
      if (expandable && open) A.toggleNode(key, false);
      else {
        // step out to the nearest shallower row
        const lvl = Number(cur?.getAttribute("aria-level") || 1);
        for (let j = i - 1; j >= 0; j--) {
          if (Number(rows[j].getAttribute("aria-level")) < lvl) { go(j); break; }
        }
      }
    } else if (e.key === "Enter" || e.key === " ") {
      cur?.click();
    } else return;
    e.preventDefault();
  });
}
