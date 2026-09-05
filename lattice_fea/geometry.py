"""STEP import, assembly fragmenting, and display tessellation via gmsh.

The one non-obvious design rule: after import we fragment all solids
(imprinting shared interfaces so assemblies mesh conformally = bonded
contact for free), write the result to geometry.brep, then RELOAD that
brep and do everything else — tessellation now and meshing later — from
the reloaded model. OCC assigns tags deterministically for a given file,
so every face/solid tag the UI stores refers to geometry.brep and stays
valid across sessions and re-meshes.

gmsh's Python API is a process-wide singleton: every entry point here
must hold GMSH_LOCK.
"""
from __future__ import annotations

import base64
import math
import threading

import numpy as np

GMSH_LOCK = threading.Lock()
_INITIALIZED = False


def _gmsh():
    global _INITIALIZED
    import gmsh
    if not _INITIALIZED:
        # interruptible=False: skip gmsh's SIGINT handler, which cannot be
        # installed from worker threads (jobs run off the main thread)
        try:
            gmsh.initialize(interruptible=False)
        except TypeError:  # older gmsh without the kwarg
            gmsh.initialize()
        gmsh.option.setNumber("General.Terminal", 0)
        _INITIALIZED = True
    return gmsh


def _b64(arr: np.ndarray) -> dict:
    a = np.ascontiguousarray(arr)
    return {"dtype": str(a.dtype), "shape": list(a.shape),
            "b64": base64.b64encode(a.tobytes()).decode("ascii")}


def _fresh_model(gmsh, name: str):
    gmsh.clear()
    gmsh.model.add(name)


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------

def import_step(step_path: str, brep_path: str, fragment: bool = True) -> dict:
    """Import STEP, fragment solids, save canonical brep, return metadata
    (names matched by center-of-mass/volume signature after reload)."""
    with GMSH_LOCK:
        gmsh = _gmsh()
        _fresh_model(gmsh, "import")
        gmsh.option.setNumber("Geometry.OCCImportLabels", 1)
        gmsh.model.occ.importShapes(step_path)
        gmsh.model.occ.synchronize()

        vols = gmsh.model.getEntities(3)
        if not vols:
            raise ValueError("No solid bodies found in STEP file. "
                             "Lattice v0.1 needs solids (surface/wire-only files are not supported).")

        # names before fragmenting (drop OCC translator boilerplate)
        pre_names = {}
        for dim, tag in vols:
            nm = gmsh.model.getEntityName(dim, tag) or ""
            nm = nm.split("/")[-1] if nm else ""
            if "step translator" in nm.lower() or "shape_" in nm.lower():
                nm = ""
            pre_names[tag] = nm

        # fragment: imprints + dedups shared boundaries -> conformal interfaces
        if len(vols) > 1 and fragment:
            out, out_map = gmsh.model.occ.fragment(vols[:1], vols[1:])
            gmsh.model.occ.synchronize()
            # map original -> children names (children usually 1:1)
            post_names = {}
            for (dim, tag), children in zip(vols, out_map):
                for cdim, ctag in children:
                    if cdim == 3 and ctag not in post_names:
                        post_names[ctag] = pre_names.get(tag, "")
        else:
            post_names = {t: pre_names.get(t, "") for _, t in vols}

        # signatures for name matching after reload
        sigs = []
        for dim, tag in gmsh.model.getEntities(3):
            com = gmsh.model.occ.getCenterOfMass(3, tag)
            vol = gmsh.model.occ.getMass(3, tag)
            sigs.append({"com": list(com), "volume": vol,
                         "name": post_names.get(tag, "")})

        gmsh.write(brep_path)
        gmsh.clear()

    meta = _analyze_brep(brep_path, sigs)
    meta["fragmented"] = bool(fragment and len(vols) > 1)
    if not meta["fragmented"]:
        meta["contact_pairs"] = find_contact_pairs(meta)
    else:
        meta["contact_pairs"] = []
    return meta


def _match_names(solids, sigs):
    """Assign STEP names to reloaded solids by (volume, center-of-mass)."""
    used = set()
    for s in solids:
        best, best_d = None, None
        for i, g in enumerate(sigs):
            if i in used:
                continue
            dv = abs(g["volume"] - s["volume"]) / max(g["volume"], s["volume"], 1e-30)
            dc = math.dist(g["com"], s["com"])
            d = dv * 100.0 + dc
            if best_d is None or d < best_d:
                best, best_d = i, d
        if best is not None:
            used.add(best)
            s["name"] = sigs[best]["name"] or s["name"]


def _analyze_brep(brep_path: str, sigs=None) -> dict:
    """Reload canonical brep and extract solids/faces/interfaces metadata."""
    with GMSH_LOCK:
        gmsh = _gmsh()
        _fresh_model(gmsh, "analyze")
        gmsh.model.occ.importShapes(brep_path)
        gmsh.model.occ.synchronize()

        solids = []
        for dim, tag in gmsh.model.getEntities(3):
            com = gmsh.model.occ.getCenterOfMass(3, tag)
            vol = gmsh.model.occ.getMass(3, tag)
            faces = [t for d, t in gmsh.model.getBoundary([(3, tag)], oriented=False, recursive=False) if d == 2]
            solids.append({"tag": tag, "name": f"Solid {tag}", "volume": vol,
                           "com": list(com), "faces": sorted(set(faces))})
        if sigs:
            _match_names(solids, sigs)

        faces = []
        interfaces = []
        for dim, tag in gmsh.model.getEntities(2):
            up, _ = gmsh.model.getAdjacencies(2, tag)
            area = gmsh.model.occ.getMass(2, tag)
            com = gmsh.model.occ.getCenterOfMass(2, tag)
            faces.append({"tag": tag, "area": area, "com": list(com),
                          "solids": sorted(int(u) for u in up)})
            if len(up) == 2:
                interfaces.append({"face": tag, "solids": sorted(int(u) for u in up)})

        snaps = _snap_points(gmsh)
        x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(-1, -1)
        gmsh.clear()

    return {"solids": solids, "faces": faces, "interfaces": interfaces,
            "snaps": snaps,
            "bbox": [x0, y0, z0, x1, y1, z1],
            "diag": math.dist((x0, y0, z0), (x1, y1, z1))}


def _snap_points(gmsh) -> dict:
    """Exact points worth snapping a probe to, taken from the BREP.

    From the geometry, not the tessellation. A probe placed "on the corner" has
    to be ON the corner — a tessellated approximation of one is a different
    point, and the whole reason to snap is that you meant an exact feature.

    * `vertex`  — model vertices. In a solid these are also where edges meet,
                  so they are the intersection snap as well; a BREP has no
                  edge crossing that is not already a vertex.
    * `centre`  — centre of every circular or elliptical curve, recovered by
                  circumcentre from three points on it, which is exact for a
                  circle and the best available for anything else.
    * `mid`     — midpoint of every curve, at the middle of its parameter
                  range.
    """
    out = {"vertex": [], "centre": [], "mid": []}
    for _, t in gmsh.model.getEntities(0):
        try:
            out["vertex"].append([round(float(c), 6)
                                  for c in gmsh.model.getValue(0, int(t), [])])
        except Exception:                       # noqa: BLE001
            pass

    for _, t in gmsh.model.getEntities(1):
        tag = int(t)
        try:
            lo, hi = gmsh.model.getParametrizationBounds(1, tag)
            u0, u1 = float(lo[0]), float(hi[0])
        except Exception:                       # noqa: BLE001
            continue
        try:
            mid = gmsh.model.getValue(1, tag, [0.5 * (u0 + u1)])
            out["mid"].append([round(float(c), 6) for c in mid])
        except Exception:                       # noqa: BLE001
            pass
        try:
            kind = gmsh.model.getType(1, tag)
        except Exception:                       # noqa: BLE001
            kind = ""
        if kind not in ("Circle", "Ellipse"):
            continue
        try:
            pts = [np.asarray(gmsh.model.getValue(1, tag, [u0 + f * (u1 - u0)]),
                              dtype=float) for f in (0.0, 1.0 / 3.0, 2.0 / 3.0)]
            c = _circumcentre(*pts)
            if c is not None:
                out["centre"].append([round(float(x), 6) for x in c])
        except Exception:                       # noqa: BLE001
            pass

    # A closed circle's three sample points can repeat between the two
    # half-edges OCC splits it into, and a box has eight vertices reported once
    # per adjacent face in some kernels. Snapping does not care how many times
    # a point was found, only where it is.
    for k in out:
        out[k] = _dedupe(out[k])
    return out


def _circumcentre(a, b, c):
    """Centre of the circle through three points, or None if collinear."""
    ab, ac = b - a, c - a
    n = np.cross(ab, ac)
    n2 = float(n @ n)
    if n2 < 1e-20:
        return None
    return a + (np.cross(n, ab) * float(ac @ ac)
                + np.cross(ac, n) * float(ab @ ab)) / (2.0 * n2)


def _dedupe(points, tol: float = 1e-6) -> list:
    seen, out = set(), []
    for p in points:
        key = tuple(round(v / tol) for v in p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _fit_surface(gmsh, tag: int, vtx: np.ndarray, nrm: np.ndarray) -> dict:
    """Classify a face and, for cylinders, recover axis/center/radius.

    OCC knows the surface type; the numeric parameters are recovered from the
    tessellation: a cylinder's normals are all perpendicular to its axis, so
    the axis is the least-variance direction of the normal cloud, and the
    radius is the mean distance of the vertices from the axis line.
    """
    try:
        kind = gmsh.model.getType(2, tag)
    except Exception:  # noqa: BLE001
        kind = ""
    if kind == "Plane":
        return {"kind": "plane"}
    if kind != "Cylinder":
        return {"kind": "other", "occ": kind}
    if len(vtx) < 8:
        return {"kind": "other", "occ": kind}
    n = nrm.astype(np.float64)
    ln = np.linalg.norm(n, axis=1)
    ok = (np.isfinite(ln) & (ln > 0.5) & (ln < 2.0)
          & np.isfinite(vtx.astype(np.float64)).all(axis=1))
    n, vtx = n[ok], vtx[ok]
    if len(vtx) < 8:
        return {"kind": "other", "occ": kind}
    with np.errstate(all="ignore"):
        cov = (n - n.mean(0)).T @ (n - n.mean(0))
        w, vecs = np.linalg.eigh(cov)
        axis = vecs[:, 0]                   # smallest normal variance
        p = vtx.astype(np.float64)
        c0 = p.mean(0)
        d = p - c0
        radial = d - np.outer(d @ axis, axis)
        r = float(np.linalg.norm(radial, axis=1).mean())
        # center: pull the mean point onto the axis through the true center
        center = c0 - radial.mean(0)
        span = d @ axis
        height = float(span.max() - span.min())
    vals = [r, height, *axis, *center]
    if not all(np.isfinite(v) for v in vals):
        return {"kind": "other", "occ": kind}
    return {"kind": "cylinder", "radius": round(r, 4),
            "axis": [round(float(x), 6) for x in axis],
            "center": [round(float(x), 4) for x in center],
            "height": round(height, 4)}


# --------------------------------------------------------------------------
# Display tessellation
# --------------------------------------------------------------------------

def tessellate(brep_path: str, diag: float) -> dict:
    """Coarse curvature-adaptive triangulation for the 3D viewer,
    grouped per geometric face so faces are individually pickable."""
    with GMSH_LOCK:
        gmsh = _gmsh()
        _fresh_model(gmsh, "tess")
        gmsh.model.occ.importShapes(brep_path)
        gmsh.model.occ.synchronize()

        gmsh.option.setNumber("Mesh.MeshSizeMax", max(diag / 50.0, 1e-4))
        gmsh.option.setNumber("Mesh.MeshSizeMin", max(diag / 400.0, 1e-5))
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 1)
        gmsh.option.setNumber("Mesh.MinimumElementsPerTwoPi", 24)
        gmsh.option.setNumber("Mesh.Algorithm", 6)  # frontal-delaunay
        gmsh.model.mesh.generate(2)

        out_faces = []
        for dim, tag in gmsh.model.getEntities(2):
            node_tags, coords, _ = gmsh.model.mesh.getNodes(2, tag, includeBoundary=True,
                                                            returnParametricCoord=False)
            if len(node_tags) == 0:
                continue
            order = np.argsort(node_tags)
            sorted_tags = np.asarray(node_tags)[order]
            xyz = np.asarray(coords, dtype=np.float64).reshape(-1, 3)[order]

            etypes, etags, enodes = gmsh.model.mesh.getElements(2, tag)
            tris = None
            for et, en in zip(etypes, enodes):
                if et == 2:  # 3-node triangle
                    tris = np.asarray(en, dtype=np.int64).reshape(-1, 3)
            if tris is None or len(tris) == 0:
                continue
            idx = np.searchsorted(sorted_tags, tris.ravel())
            # guard: every element node must be present in the node list
            idx = np.clip(idx, 0, len(sorted_tags) - 1)
            tri = idx.reshape(-1, 3).astype(np.uint32)

            # smooth per-vertex normals within the face patch
            v = xyz.astype(np.float32)
            n = np.zeros_like(v)
            p0, p1, p2 = v[tri[:, 0]], v[tri[:, 1]], v[tri[:, 2]]
            fn = np.cross(p1 - p0, p2 - p0)
            for k in range(3):
                np.add.at(n, tri[:, k], fn)
            ln = np.linalg.norm(n, axis=1, keepdims=True)
            n = n / np.maximum(ln, 1e-20)

            out_faces.append({"tag": tag,
                              "fit": _fit_surface(gmsh, tag, v, n),
                              "vtx": _b64(v.ravel()),
                              "nrm": _b64(n.astype(np.float32).ravel()),
                              "tri": _b64(tri.ravel())})

        # feature edges: 1D mesh of every model curve
        evtx, eseg, base = [], [], 0
        for dim, tag in gmsh.model.getEntities(1):
            node_tags, coords, _ = gmsh.model.mesh.getNodes(1, tag, includeBoundary=True,
                                                            returnParametricCoord=False)
            if len(node_tags) == 0:
                continue
            order = np.argsort(node_tags)
            sorted_tags = np.asarray(node_tags)[order]
            xyz = np.asarray(coords, dtype=np.float32).reshape(-1, 3)[order]
            etypes, etags, enodes = gmsh.model.mesh.getElements(1, tag)
            segs = None
            for et, en in zip(etypes, enodes):
                if et == 1:  # 2-node line
                    segs = np.asarray(en, dtype=np.int64).reshape(-1, 2)
            if segs is None:
                continue
            idx = np.searchsorted(sorted_tags, segs.ravel())
            idx = np.clip(idx, 0, len(sorted_tags) - 1).reshape(-1, 2)
            evtx.append(xyz)
            eseg.append(idx.astype(np.uint32) + base)
            base += len(xyz)

        edges = {}
        if evtx:
            edges = {"vtx": _b64(np.concatenate(evtx).ravel()),
                     "seg": _b64(np.concatenate(eseg).ravel())}

        gmsh.clear()

    return {"faces": out_faces, "edges": edges}


def find_contact_pairs(meta: dict, rel_tol: float = 0.02) -> list:
    """Faces of different solids that lie on top of each other.

    Only meaningful on an UNFRAGMENTED import. Fragmenting merges coincident
    boundaries into one shared face, which is what makes an assembly bonded
    and conformal — and also why a fragmented model has nothing to slide:
    there are no longer two surfaces there.

    Detection is on area and centroid, which is decisive for the flat mating
    faces of a bolted joint: two faces of different parts with the same area
    whose centroids sit on top of each other are the two halves of one
    interface. Tolerance is relative to the face size, so it holds from a
    washer face to a whole flange.
    """
    faces = meta.get("faces", [])
    by_solid = {}
    for f in faces:
        for sd in f.get("solids", []):
            by_solid.setdefault(sd, []).append(f)

    diag = meta.get("diag", 100.0) or 100.0
    pairs = []
    seen = set()
    solids = sorted(by_solid)
    for i, sa in enumerate(solids):
        for sb in solids[i + 1:]:
            for fa in by_solid[sa]:
                for fb in by_solid[sb]:
                    key = tuple(sorted((fa["tag"], fb["tag"])))
                    if key in seen:
                        continue
                    amax = max(fa["area"], fb["area"])
                    if amax <= 0:
                        continue
                    if abs(fa["area"] - fb["area"]) / amax > rel_tol:
                        continue
                    # centroids within a fraction of the face's own size
                    tol = max(math.sqrt(amax) * rel_tol, diag * 1e-4)
                    if math.dist(fa["com"], fb["com"]) > tol:
                        continue
                    seen.add(key)
                    pairs.append({
                        "solids": [sa, sb],
                        "faces_a": [fa["tag"]], "faces_b": [fb["tag"]],
                        "area": round(min(fa["area"], fb["area"]), 4),
                    })
    return pairs
