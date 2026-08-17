"""Analysis meshing: quadratic tets, physical groups, UNV export.

Physical groups are the contract with code_aster:
  V<tag>        one per solid volume            -> GROUP_MA for material AFFE
  SUP<i>/LOA<i> face sets used by BCs and loads -> GROUP_MA (+ auto node groups)
Only grouped entities are written (Mesh.SaveAll=0), so the UNV contains the
3D mesh plus exactly the boundary faces the model needs — no stray elements
for AFFE_MODELE to choke on.
"""
from __future__ import annotations

import math
import time

import numpy as np

from .geometry import GMSH_LOCK, _gmsh, _fresh_model, _b64


def mesh_project(brep_path: str, unv_path: str, meta: dict, setup: dict,
                 progress=lambda s: None) -> dict:
    diag = meta["diag"]
    mcfg = setup.get("mesh", {})
    size = mcfg.get("size_mm") or diag / 25.0
    minsize = max(size / 12.0, diag * 1e-5)
    curvdiv = int(mcfg.get("curvature") or 16)
    order = int(mcfg.get("order") or 2)

    face_sets = _collect_face_sets(setup)

    t0 = time.time()
    with GMSH_LOCK:
        gmsh = _gmsh()
        _fresh_model(gmsh, "mesh")
        gmsh.model.occ.importShapes(brep_path)
        gmsh.model.occ.synchronize()

        gmsh.option.setNumber("Mesh.MeshSizeMax", size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", minsize)
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 1)
        gmsh.option.setNumber("Mesh.MinimumElementsPerTwoPi", curvdiv)
        gmsh.option.setNumber("Mesh.Optimize", 1)

        # local refinement via Restrict(MathEval) fields
        fields = []
        for loc in mcfg.get("local", []):
            ftags = [int(t) for t in loc.get("faces", [])]
            lsize = float(loc.get("size_mm") or size)
            if not ftags or lsize <= 0:
                continue
            fm = gmsh.model.mesh.field.add("MathEval")
            gmsh.model.mesh.field.setString(fm, "F", str(lsize))
            fr = gmsh.model.mesh.field.add("Restrict")
            gmsh.model.mesh.field.setNumber(fr, "InField", fm)
            gmsh.model.mesh.field.setNumbers(fr, "SurfacesList", ftags)
            # pull volumes adjacent to those faces so refinement has depth
            vt = set()
            for t in ftags:
                up, _ = gmsh.model.getAdjacencies(2, t)
                vt.update(int(u) for u in up)
            if vt:
                gmsh.model.mesh.field.setNumbers(fr, "VolumesList", sorted(vt))
            fields.append(fr)
        if fields:
            fg = gmsh.model.mesh.field.add("MathEval")
            gmsh.model.mesh.field.setString(fg, "F", str(size))
            fmin = gmsh.model.mesh.field.add("Min")
            gmsh.model.mesh.field.setNumbers(fmin, "FieldsList", fields + [fg])
            gmsh.model.mesh.field.setAsBackgroundMesh(fmin)

        progress("meshing surfaces…")
        gmsh.model.mesh.generate(2)
        progress("meshing volume (HXT)…")
        try:
            gmsh.option.setNumber("Mesh.Algorithm3D", 10)  # HXT, multithreaded
            gmsh.model.mesh.generate(3)
        except Exception:  # noqa: BLE001 — HXT can fail on dirty geometry
            progress("HXT failed, retrying with Delaunay…")
            gmsh.option.setNumber("Mesh.Algorithm3D", 1)
            gmsh.model.mesh.generate(3)

        if order == 2:
            progress("raising to 2nd order…")
            gmsh.option.setNumber("Mesh.SecondOrderLinear", 0)
            gmsh.model.mesh.setOrder(2)

        # bolt beams AFTER setOrder so they stay SEG2 for POU_D_T
        bolt_records = _add_bolt_beams(gmsh, meta, setup, progress)
        remote_records = _add_remote_stubs(gmsh, setup, meta, progress)

        # ---- physical groups ----
        for dim, tag in gmsh.model.getEntities(3):
            gmsh.model.addPhysicalGroup(3, [tag], name=f"V{tag}")
        # One query, not one per face tag: this set was being rebuilt from a
        # fresh gmsh call inside the comprehension for every tag in every
        # group, which on an assembly with thousands of faces and a few dozen
        # boundary conditions dominated the whole meshing step.
        surfaces = {t for _, t in gmsh.model.getEntities(2)}
        written = []
        for gname, ftags in face_sets.items():
            valid = [t for t in ftags if t in surfaces]
            if valid:
                gmsh.model.addPhysicalGroup(2, valid, name=gname)
                written.append(gname)
            else:
                progress(f"warning: group {gname} references no surface in this "
                         "geometry and was not written")

        gmsh.option.setNumber("Mesh.SaveAll", 0)
        gmsh.write(unv_path)

        stats = _stats(gmsh, order)
        stats["wall_s"] = round(time.time() - t0, 1)
        stats["size_mm"] = size

        # expected volume — used later to self-validate MED connectivity parsing
        stats["geo_volume"] = float(sum(
            gmsh.model.occ.getMass(3, t) for _, t in gmsh.model.getEntities(3)))

        # probe -> nearest mesh node coordinates (exact coords survive into aster)
        probes_out = []
        probes = setup.get("probes", [])
        if probes:
            _, coords, _ = gmsh.model.mesh.getNodes()
            xyz = np.asarray(coords).reshape(-1, 3)
            for p in probes:
                q = np.array([p["x"], p["y"], p["z"]])
                d = np.linalg.norm(xyz - q, axis=1)
                i = int(np.argmin(d))
                probes_out.append({**p, "node_xyz": xyz[i].tolist(),
                                   "snap_dist": float(d[i])})
        stats["probes"] = probes_out
        stats["bolts"] = bolt_records
        stats["remotes"] = remote_records
        # Only what was actually written. Recording every REQUESTED group
        # let the stale-mesh guard pass for a group that does not exist,
        # and the solver then aborted on GROUP_MA not found.
        stats["face_groups"] = sorted(written)

        skin = _skin(gmsh)
        conn_islands = _count_islands(gmsh)
        stats["islands"] = conn_islands

        gmsh.clear()

    return {"stats": stats, "skin": skin}


def _collect_face_sets(setup: dict) -> dict:
    """Face groups for the whole model.

    The mesh is shared, but supports and loads now belong to individual
    analyses, so group names are scoped by analysis index — otherwise the
    second analysis's SUP1 would overwrite the first's.
    """
    sets = {}
    for ai, a in enumerate(setup.get("analyses", []), start=1):
        for i, s in enumerate(a.get("supports", [])):
            if s.get("faces"):
                sets[group_name("SUP", ai, i + 1)] = [int(t) for t in s["faces"]]
        for i, l in enumerate(a.get("loads", [])):
            if l.get("type") in ("force", "pressure", "remote") and l.get("faces"):
                sets[group_name("LOA", ai, i + 1)] = [int(t) for t in l["faces"]]
    for i, b in enumerate(setup.get("bolts", [])):
        if b.get("side_a_faces"):
            sets[f"BFA{i + 1}"] = [int(t) for t in b["side_a_faces"]]
        if b.get("side_b_faces"):
            sets[f"BFB{i + 1}"] = [int(t) for t in b["side_b_faces"]]
    for i, t in enumerate(setup.get("ties", [])):
        if t.get("slave_faces"):
            sets[f"TIE{i + 1}"] = [int(x) for x in t["slave_faces"]]
    return sets


def group_name(kind: str, analysis_index: int, item_index: int) -> str:
    """Mesh group name for a per-analysis boundary condition, e.g. SUP1_2."""
    return f"{kind}{analysis_index}_{item_index}"


def _face_index(meta: dict) -> dict:
    """tag -> face record. Built once per mesh: rebuilding it inside the bolt
    loop was O(bolts x faces), which patterning a joint across a flange turns
    into real time on a large assembly."""
    return {f["tag"]: f for f in meta.get("faces", [])}


def _face_set_centroid(faces: dict, ftags) -> "np.ndarray|None":
    """Area-weighted centroid of a set of geometric faces."""
    acc, area = np.zeros(3), 0.0
    for t in ftags:
        f = faces.get(int(t))
        if f:
            acc += np.asarray(f["com"]) * f["area"]
            area += f["area"]
    return acc / area if area > 0 else None


def _bolt_axis(faces: dict, bolt: dict, ca, cb) -> np.ndarray:
    """Bolt axis: prefer a detected cylinder axis, fall back to the
    centroid-to-centroid line."""
    for key in ("side_a_faces", "side_b_faces"):
        for t in bolt.get(key, []):
            fit = (faces.get(int(t)) or {}).get("fit") or {}
            if fit.get("kind") == "cylinder":
                return np.asarray(fit["axis"], dtype=float)
    d = cb - ca
    n = np.linalg.norm(d)
    return d / n if n > 1e-12 else np.array([0.0, 0.0, 1.0])


def _add_bolt_beams(gmsh, meta: dict, setup: dict, progress) -> list:
    """Create one SEG2 beam per bolt as a discrete curve (added after
    setOrder, so beams stay linear — POU_D_T wants SEG2). End nodes land at
    the projections of each face-set centroid onto the bolt axis.

    Returns per-bolt records for the comm writer.
    """
    out = []
    faces = _face_index(meta)
    bolts = setup.get("bolts", [])
    for i, b in enumerate(bolts):
        # A half-picked bolt is skipped, not fatal: the UI already flags it and
        # blocks the run, and failing the whole mesh over one unfinished item
        # would throw away minutes of work on a large model.
        if not (b.get("side_a_faces") and b.get("side_b_faces")):
            progress(f"bolt {i + 1} ({b.get('name', '')}): faces not picked — skipped")
            continue
        ca = _face_set_centroid(faces, b.get("side_a_faces", []))
        cb = _face_set_centroid(faces, b.get("side_b_faces", []))
        if ca is None or cb is None:
            progress(f"bolt {i + 1}: face tags not in this geometry — skipped")
            continue
        axis = _bolt_axis(faces, b, ca, cb)
        mid = (ca + cb) / 2.0
        pa = mid + axis * float((ca - mid) @ axis)
        pb = mid + axis * float((cb - mid) @ axis)
        if np.linalg.norm(pb - pa) < 1e-9:
            progress(f"bolt {i + 1} ({b.get('name', '')}): zero grip length — "
                     "both face sets are on the same feature; skipped")
            continue

        dtag = gmsh.model.addDiscreteEntity(1)
        n0 = gmsh.model.mesh.getMaxNodeTag() + 1
        e0 = gmsh.model.mesh.getMaxElementTag() + 1
        gmsh.model.mesh.addNodes(1, dtag, [n0, n0 + 1],
                                 [*pa.tolist(), *pb.tolist()])
        gmsh.model.mesh.addElementsByType(dtag, 1, [e0], [n0, n0 + 1])  # SEG2
        gmsh.model.addPhysicalGroup(1, [dtag], name=f"BOLT{i + 1}")

        out.append({"index": i + 1, "id": b.get("id"), "name": b.get("name", f"Bolt {i + 1}"),
                    "end_a": [round(x, 6) for x in pa],
                    "end_b": [round(x, 6) for x in pb],
                    "length": round(float(np.linalg.norm(pb - pa)), 4)})
        progress(f"bolt {i + 1}: grip {out[-1]['length']} mm, "
                 f"A({', '.join(f'{x:.1f}' for x in pa)}) → B({', '.join(f'{x:.1f}' for x in pb)})")
    return out


def _add_remote_stubs(gmsh, setup: dict, meta: dict, progress) -> list:
    """Remote force/moment loads need a node with 6 DOFs off the body.
    Recipe: a short SEG2 beam stub whose end A sits at the remote point;
    A becomes the RBE3 master over the load's faces and carries FORCE_NODALE.
    The dangling end B is held through the beam, so nothing is singular."""
    out = []
    diag = meta.get("diag", 100.0)
    stub = max(diag * 1e-3, 0.05)
    entries = []
    for ai, a in enumerate(setup.get("analyses", []), start=1):
        for i, l in enumerate(a.get("loads", [])):
            if l.get("type") == "remote":
                entries.append((ai, i + 1, l))
    for ai, i, l in entries:
        if not l.get("faces"):
            raise ValueError(f"Remote load '{l.get('name', i)}': pick faces first")
        pt = np.asarray([float(l.get("x", 0)), float(l.get("y", 0)), float(l.get("z", 0))])
        pb = pt + np.array([1.0, 1.0, 1.0]) / math.sqrt(3.0) * stub
        dtag = gmsh.model.addDiscreteEntity(1)
        n0 = gmsh.model.mesh.getMaxNodeTag() + 1
        e0 = gmsh.model.mesh.getMaxElementTag() + 1
        gmsh.model.mesh.addNodes(1, dtag, [n0, n0 + 1], [*pt.tolist(), *pb.tolist()])
        gmsh.model.mesh.addElementsByType(dtag, 1, [e0], [n0, n0 + 1])
        tag_name = group_name("RPT", ai, i)
        gmsh.model.addPhysicalGroup(1, [dtag], name=tag_name)
        out.append({"analysis_index": ai, "load_index": i, "id": l.get("id"),
                    "group": tag_name,
                    "node": [round(x, 6) for x in pt]})
        progress(f"remote point {tag_name} at ({pt[0]:.1f}, {pt[1]:.1f}, {pt[2]:.1f})")
    return out


def _stats(gmsh, order: int) -> dict:
    node_tags, _, _ = gmsh.model.mesh.getNodes()
    n_nodes = len(node_tags)
    etypes, etags, _ = gmsh.model.mesh.getElements(3)
    n_elems = sum(len(t) for t in etags)
    qmin = qavg = None
    try:
        all_tags = np.concatenate([np.asarray(t) for t in etags]) if etags else np.array([])
        if all_tags.size:
            q = gmsh.model.mesh.getElementQualities(all_tags.tolist(), "minSICN")
            q = np.asarray(q)
            qmin, qavg = float(q.min()), float(q.mean())
    except Exception:  # noqa: BLE001
        pass
    dof = 3 * n_nodes
    return {"nodes": n_nodes, "elements": n_elems, "order": order, "dof": dof,
            "quality_min": qmin, "quality_avg": qavg,
            "mem_gb_est": round(dof * 8e3 / 1e9, 2)}  # ~8 KB/DOF MUMPS factor, rough


def _skin(gmsh) -> dict:
    """Surface triangles of the volume mesh (from the 2D entities of the
    meshed model) for preview rendering."""
    node_tags, coords, _ = gmsh.model.mesh.getNodes()
    order = np.argsort(node_tags)
    sorted_tags = np.asarray(node_tags)[order]
    xyz = np.asarray(coords, dtype=np.float32).reshape(-1, 3)[order]

    tris = []
    for dim, tag in gmsh.model.getEntities(2):
        etypes, etags, enodes = gmsh.model.mesh.getElements(2, tag)
        for et, en in zip(etypes, enodes):
            if et == 2:      # tri3
                tris.append(np.asarray(en, dtype=np.int64).reshape(-1, 3))
            elif et == 9:    # tri6 -> 4 subtriangles
                t6 = np.asarray(en, dtype=np.int64).reshape(-1, 6)
                c1, c2, c3, m1, m2, m3 = (t6[:, k] for k in range(6))
                tris.append(np.stack([c1, m1, m3], 1))
                tris.append(np.stack([m1, c2, m2], 1))
                tris.append(np.stack([m3, m2, c3], 1))
                tris.append(np.stack([m1, m2, m3], 1))
    if not tris:
        return {}
    tri = np.concatenate(tris)
    idx = np.searchsorted(sorted_tags, tri.ravel())
    idx = np.clip(idx, 0, len(sorted_tags) - 1).reshape(-1, 3)

    used = np.unique(idx.ravel())
    remap = np.zeros(len(sorted_tags), dtype=np.uint32)
    remap[used] = np.arange(len(used), dtype=np.uint32)
    return {"vtx": _b64(xyz[used].ravel()),
            "tri": _b64(remap[idx].astype(np.uint32).ravel())}


def _count_islands(gmsh) -> int:
    """Connected components of the volume mesh — >1 means parts of the
    assembly are not joined and will fly away in modal/static.

    Vectorised label propagation. The previous version was a pure-Python
    union-find over every tet's corner nodes: on a million-element mesh that
    is several million dict lookups and pointer walks, and it ran on every
    single mesh. This does the same work in numpy, converging in O(log n)
    fully-vectorised passes.
    """
    etypes, etags, enodes = gmsh.model.mesh.getElements(3)
    pairs = []
    for et, en in zip(etypes, enodes):
        npery = {4: 4, 11: 10}.get(et)
        if not npery:
            continue
        # corner nodes only — enough for connectivity, and 2.5x less data
        conn = np.asarray(en, dtype=np.int64).reshape(-1, npery)[:, :4]
        for k in (1, 2, 3):
            pairs.append(np.stack([conn[:, 0], conn[:, k]], 1))
    if not pairs:
        return 1

    edges = np.concatenate(pairs)
    nodes, idx = np.unique(edges, return_inverse=True)
    idx = idx.reshape(-1, 2)
    n = len(nodes)
    if n == 0:
        return 1

    # Scatter-minimum without ufunc.at (which is unbuffered and very slow):
    # sort the edge endpoints once, then each pass is a reduceat.
    key = np.concatenate([idx[:, 0], idx[:, 1]])
    src = np.concatenate([idx[:, 1], idx[:, 0]])
    order = np.argsort(key, kind="stable")
    key_s, src_s = key[order], src[order]
    starts = np.flatnonzero(np.r_[True, key_s[1:] != key_s[:-1]])
    targets = key_s[starts]

    label = np.arange(n, dtype=np.int64)
    for _ in range(64):                      # far more than log2(n) in practice
        mins = np.minimum.reduceat(label[src_s], starts)
        nxt = label.copy()
        # fancy indexing yields a copy, so this must be an assignment —
        # np.minimum(..., out=nxt[targets]) would write into a temporary
        nxt[targets] = np.minimum(nxt[targets], mins)
        nxt = nxt[nxt]                       # pointer jumping
        if np.array_equal(nxt, label):
            break
        label = nxt
    return max(1, int(np.unique(label).size))
