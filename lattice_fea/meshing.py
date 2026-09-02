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

# Bumped whenever a mesh written by an older build is no longer trustworthy.
# 2: UNV groups could contain node entities (a leaked gmsh write option),
#    which made code_aster fail on a duplicate GROUP_NO. Meshes written before
#    this are re-generated rather than silently reused.
# 3: bolt beams spanned the picked faces' centroids, which is half the clamped
#    length whenever the hole cylinders were picked. The recorded grip is read
#    back as l_K by the sizing calculation and sets the bolt's stiffness, so a
#    mesh written before this reports the wrong bolt entirely.
MESH_FORMAT = 3


def mesh_project(brep_path: str, unv_path: str, meta: dict, setup: dict,
                 progress=lambda s: None) -> dict:
    diag = meta["diag"]
    mcfg = setup.get("mesh", {})
    size = mcfg.get("size_mm") or diag / 25.0
    minsize = max(size / 12.0, diag * 1e-5)
    curvdiv = int(mcfg.get("curvature") or 16)
    order = int(mcfg.get("order") or 2)

    face_sets = _collect_face_sets(setup)
    setup_orig = setup

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

        # Recombination is a session-wide option on a shared gmsh session, so
        # it is set from scratch every time rather than left wherever the last
        # mesh put it — the same discipline the UNV/Abaqus writes need.
        gmsh.option.setNumber("Mesh.RecombineAll", 0)
        # A complete quadratic hexahedron has 27 nodes; the serendipity one has
        # 20. HEXA20 / C3D20 is what code_aster and CalculiX both read, and the
        # interior nodes of a HEX27 buy nothing here, so ask for the incomplete
        # form. Set every time, never inherited — this session is shared.
        gmsh.option.setNumber("Mesh.SecondOrderIncomplete", 1)
        want_hex = str(mcfg.get("elements") or "tet") == "hex"
        remap = None

        if want_hex:
            progress("looking for a sweep…")
            try:
                plan = _sweep_plan(gmsh)
                remap = _rebuild_as_sweep(gmsh, plan, size, progress) if plan else None
                if plan is None:
                    progress("no sweep: at least one solid is not a prism, or "
                             "they do not share one axis. Meshing with "
                             "tetrahedra instead.")
            except Exception as e:      # noqa: BLE001
                progress(f"sweep failed ({str(e).splitlines()[0][:120]}); "
                         "meshing with tetrahedra instead")
                remap = None
            if remap is None:
                # the model may be half-rebuilt; start over from the file
                _fresh_model(gmsh, "mesh")
                gmsh.model.occ.importShapes(brep_path)
                gmsh.model.occ.synchronize()
                gmsh.option.setNumber("Mesh.MeshSizeMax", size)
                gmsh.option.setNumber("Mesh.MeshSizeMin", minsize)
                gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 1)
                gmsh.option.setNumber("Mesh.MinimumElementsPerTwoPi", curvdiv)
                gmsh.option.setNumber("Mesh.Optimize", 1)
                gmsh.option.setNumber("Mesh.RecombineAll", 0)
                want_hex = False
            else:
                # every tag the rest of this function uses came from the
                # geometry that has just been replaced
                face_sets = {k: [remap["faces"][t] for t in v
                                 if t in remap["faces"]]
                             for k, v in face_sets.items()}
                _size_fields(gmsh, mcfg, size, remap)
                gmsh.model.mesh.generate(3)
                ok, note = _hex_is_usable(gmsh)
                if not ok:
                    progress(f"the swept mesh is not usable ({note}); "
                             "meshing with tetrahedra instead")
                    _fresh_model(gmsh, "mesh")
                    gmsh.model.occ.importShapes(brep_path)
                    gmsh.model.occ.synchronize()
                    gmsh.option.setNumber("Mesh.MeshSizeMax", size)
                    gmsh.option.setNumber("Mesh.MeshSizeMin", minsize)
                    gmsh.option.setNumber("Mesh.RecombineAll", 0)
                    face_sets = _collect_face_sets(setup_orig)
                    remap, want_hex = None, False
                else:
                    progress(f"hexahedra: {note}")

        if not want_hex:
            _size_fields(gmsh, mcfg, size, None)
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
        gmsh.option.setNumber("Mesh.RecombineAll", 0)

        if order == 2:
            progress("raising to 2nd order…")
            gmsh.option.setNumber("Mesh.SecondOrderLinear", 0)
            gmsh.model.mesh.setOrder(2)

        # bolt beams AFTER setOrder so they stay SEG2 for POU_D_T
        bolt_records = _add_bolt_beams(gmsh, meta, setup, progress, remap)
        remote_records = _add_remote_stubs(gmsh, setup, meta, progress)

        # ---- physical groups ----
        # A swept model was rebuilt, so its volume tags are new. The group
        # NAME has to stay the original one — material assignment, contacts
        # and every result field are keyed to it.
        vname = {v: k for k, v in (remap or {}).get("volumes", {}).items()}
        for dim, tag in gmsh.model.getEntities(3):
            gmsh.model.addPhysicalGroup(3, [tag], name=f"V{vname.get(tag, tag)}")
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

        # Write options are set explicitly before EVERY write, never left to
        # whatever the last write wanted.
        #
        # The gmsh session is shared and long-lived, so an option set for one
        # format silently applies to the next. Turning on SaveGroupsOfNodes
        # for the Abaqus deck leaked into the following mesh's UNV, which then
        # carried node entities inside the element groups. code_aster read
        # those as GROUP_NO, and the deck's own
        # DEFI_GROUP(CREA_GROUP_NO=TOUT_GROUP_MA) then collided with a group
        # that already existed — so the first mesh after a restart solved and
        # every one after it failed.
        gmsh.option.setNumber("Mesh.SaveGroupsOfNodes", 0)
        gmsh.write(unv_path)

        # An Abaqus deck as well, for CalculiX. Same mesh, same groups —
        # written here so both solvers are always looking at the identical
        # discretisation and a result cannot depend on which one ran.
        try:
            gmsh.option.setNumber("Mesh.SaveGroupsOfNodes", 1)
            inp_path = unv_path.rsplit(".", 1)[0] + ".inp"
            gmsh.write(inp_path)
            _strip_surface_elements(inp_path)
        except Exception as e:                      # noqa: BLE001
            progress(f"warning: could not write the CalculiX mesh ({e}); "
                     "code_aster is unaffected")
        finally:
            gmsh.option.setNumber("Mesh.SaveGroupsOfNodes", 0)

        stats = _stats(gmsh, order)
        stats["wall_s"] = round(time.time() - t0, 1)
        stats["mesh_format"] = MESH_FORMAT
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
        stats["element_kinds"] = _element_kinds(gmsh)
        stats["bolts"] = bolt_records
        stats["remotes"] = remote_records
        # Only what was actually written. Recording every REQUESTED group
        # let the stale-mesh guard pass for a group that does not exist,
        # and the solver then aborted on GROUP_MA not found.
        stats["face_groups"] = sorted(written)

        # Consistent nodal loads for every face group.
        #
        # CalculiX applies a distributed face load through element-face
        # numbers, which a mesh written from physical groups does not carry.
        # Integrating the shape functions here instead gives the exact
        # equivalent nodal forces for a uniform unit traction, and works for
        # any solver that takes point loads. Corner nodes of a quadratic
        # triangle legitimately get zero — that is what the shape functions
        # integrate to, not a bug.
        # These serve CalculiX and the slip check. None is needed to SOLVE
        # with code_aster, and the mesh itself is already written by now —
        # so a failure in any of them must not throw away a completed mesh.
        # It costs the user minutes and tells them nothing about their model.
        for key, fn in (("face_nodes", _face_group_weights),
                        ("face_elems", _face_group_element_faces),
                        ("face_normals", _face_group_normals),
                        ("face_areas", _face_group_areas)):
            try:
                stats[key] = fn(gmsh, written)
            except Exception as e:              # noqa: BLE001
                stats[key] = {}
                progress(f"warning: could not build {key} ({e}). "
                         "code_aster is unaffected; CalculiX will ask you to "
                         "re-mesh if it needs this.")

        skin = _skin(gmsh)
        conn_islands = _count_islands(gmsh)
        stats["islands"] = conn_islands

        gmsh.clear()

    return {"stats": stats, "skin": skin}


# --------------------------------------------------------------- sweep meshing

def _size_fields(gmsh, mcfg: dict, size: float, remap=None) -> None:
    """Local refinement, as Restrict(MathEval) fields.

    Built AFTER any sweep rebuild, because a rebuild replaces the surfaces
    these point at. Fields set on dead tags are not an error — gmsh simply
    ignores them, so the refinement would have gone missing with nothing said.
    """
    m = (remap or {}).get("faces") or {}
    fields = []
    for loc in mcfg.get("local", []):
        ftags = [m.get(int(t), int(t)) for t in loc.get("faces", [])]
        ftags = [t for t in ftags if t in {int(x) for _, x in gmsh.model.getEntities(2)}]
        lsize = float(loc.get("size_mm") or size)
        if not ftags or lsize <= 0:
            continue
        fm = gmsh.model.mesh.field.add("MathEval")
        gmsh.model.mesh.field.setString(fm, "F", str(lsize))
        fr = gmsh.model.mesh.field.add("Restrict")
        gmsh.model.mesh.field.setNumber(fr, "InField", fm)
        gmsh.model.mesh.field.setNumbers(fr, "SurfacesList", ftags)
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


def _sweep_plan(gmsh, tol_rel: float = 1e-6) -> "dict|None":
    """Can this model be swept into hexahedra, and how?

    A volume sweeps when it is a prism: two parallel planar faces of equal
    area, offset along their own normal, with area x thickness equal to the
    volume. The cross-section can be **anything** — a plate with a dozen bolt
    holes sweeps perfectly well, because the face it sweeps is quad-meshed in
    2D where holes are not a problem.

    That is the point missed by the first attempt at this, which only tried
    gmsh's `setTransfiniteAutomatic`. Transfinite needs a four-sided face, so
    one hole defeated it, and the conclusion drawn was that holed parts cannot
    be hex-meshed. They can. They just need sweeping rather than mapping.

    Every volume must sweep along the same axis, because a hex volume next to
    a tet one cannot share a conformal face — gmsh refuses the mesh outright.
    So this is all-or-nothing, and returns None the moment one volume fails.
    """
    vols = [int(t) for _, t in gmsh.model.getEntities(3)]
    if not vols:
        return None
    out, axis = {}, None
    for v in vols:
        faces = [int(f) for _, f in gmsh.model.getBoundary([(3, v)], oriented=False)]
        mass = gmsh.model.occ.getMass(3, v)
        best = None
        planes = [f for f in faces if gmsh.model.getType(2, f) == "Plane"]
        for i, a in enumerate(planes):
            na = np.asarray(gmsh.model.getNormal(a, [0.5, 0.5]), dtype=float)
            ma = gmsh.model.occ.getMass(2, a)
            ca = np.asarray(gmsh.model.occ.getCenterOfMass(2, a))
            for b in planes[i + 1:]:
                mb = gmsh.model.occ.getMass(2, b)
                if abs(ma - mb) > tol_rel * max(ma, mb):
                    continue
                nb = np.asarray(gmsh.model.getNormal(b, [0.5, 0.5]), dtype=float)
                if abs(abs(float(na @ nb)) - 1.0) > 1e-9:
                    continue
                t = float((np.asarray(gmsh.model.occ.getCenterOfMass(2, b)) - ca) @ na)
                if abs(t) < 1e-9 or abs(abs(t) * ma - mass) > 1e-4 * mass:
                    continue
                if best is None or abs(t) < best["t"]:
                    best = {"base": a, "top": b, "t": abs(t),
                            "dir": (na * np.sign(t)).tolist()}
        if best is None:
            return None
        d = np.asarray(best["dir"])
        if axis is None:
            axis = d
        elif abs(abs(float(axis @ d)) - 1.0) > 1e-9:
            return None                     # volumes sweep on different axes
        out[v] = best
    return {"axis": axis.tolist(), "volumes": out}


def _sweep_chains(plan: dict) -> list:
    """Order the volumes into stacks.

    Each volume is an edge joining its two cap faces, so a stack of plates is
    a path through that graph: bottom - mid - top. Walking the path and
    extruding one volume at a time from the face the last one produced is what
    keeps the interface between them a single shared face. Extruding each
    volume from its own cap independently would leave two solids that touch
    but share nothing, and the mesh across the joint would not match.

    Returns [[(volume, from_face, to_face), ...], ...], or [] if the graph is
    not a set of simple paths — a branch means it is not a stack and this
    cannot order it.
    """
    vols = plan["volumes"]
    deg = {}
    for v in vols.values():
        for f in (v["base"], v["top"]):
            deg[f] = deg.get(f, 0) + 1
    if any(n > 2 for n in deg.values()):
        return []                       # three volumes meeting on one face

    edges = {k: {v["base"], v["top"]} for k, v in vols.items()}
    chains, used = [], set()
    ends = [f for f, n in deg.items() if n == 1]
    for start in ends:
        if any(start in edges[k] for k in used):
            continue
        chain, cur = [], start
        while True:
            nxt = next((k for k, fs in edges.items()
                        if k not in used and cur in fs), None)
            if nxt is None:
                break
            other = next(iter(edges[nxt] - {cur})) if len(edges[nxt]) == 2 else cur
            chain.append((nxt, cur, other))
            used.add(nxt)
            cur = other
        if chain:
            chains.append(chain)
    return chains if len(used) == len(vols) else []


def _rebuild_as_sweep(gmsh, plan: dict, size: float, progress) -> "dict|None":
    """Rebuild the model by extruding its base faces, and map the old entity
    tags onto the new ones.

    The rebuild is invisible outside this function: physical groups are still
    written under the names the setup asked for, so every face group, contact,
    bolt and boundary condition keeps working. What comes back is
    {"faces": {old: new}, "volumes": {old: new}} — and None if the rebuilt
    model is not the same shape as the original, which is checked rather than
    trusted.
    """
    chains = _sweep_chains(plan)
    if not chains:
        return None

    # what the original looked like, so the rebuild can be checked against it
    def snap(dim):
        return {int(t): (np.asarray(gmsh.model.occ.getCenterOfMass(dim, int(t))),
                         gmsh.model.occ.getMass(dim, int(t)))
                for _, t in gmsh.model.getEntities(dim)}
    old_f, old_v = snap(2), snap(3)
    centre = {t: c for t, (c, _) in old_f.items()}
    keep = {c[0][1] for c in chains}          # the face each chain starts from

    gmsh.model.occ.remove([(3, int(v)) for v in plan["volumes"]])
    gmsh.model.occ.synchronize()
    for _, t in list(gmsh.model.getEntities(2)):
        if int(t) not in keep:
            try:
                gmsh.model.occ.remove([(2, int(t))], recursive=True)
            except Exception:               # noqa: BLE001 — shared edges
                pass
    gmsh.model.occ.synchronize()
    if {int(t) for _, t in gmsh.model.getEntities(2)} != keep:
        return None

    axis = np.asarray(plan["axis"])
    for chain in chains:
        face = chain[0][1]
        for v, frm, to in chain:
            t = plan["volumes"][v]["t"]
            # direction is from THIS cap toward the other one, not a global
            # axis: a stack extruded from its middle runs both ways
            step = float((centre[to] - centre[frm]) @ axis)
            d = axis * np.sign(step)
            n = max(1, int(round(t / max(size, 1e-9))))
            gmsh.model.mesh.setRecombine(2, int(face))
            made = gmsh.model.occ.extrude([(2, int(face))], *(d * t),
                                          numElements=[n], recombine=True)
            gmsh.model.occ.synchronize()
            face = next(tag for dim, tag in made if dim == 2)
    new_f, new_v = snap(2), snap(3)

    # Match by geometry. Identical shapes, so this is exact — and if any face
    # fails to find its twin the rebuild did not reproduce the model and the
    # whole thing is abandoned rather than meshed into something else.
    def match(old, new, scale):
        used, out = set(), {}
        for ot, (oc, om) in old.items():
            hit = None
            for nt, (nc, nm) in new.items():
                if nt in used or abs(om - nm) > 1e-6 * max(om, nm, 1e-12):
                    continue
                if float(np.linalg.norm(oc - nc)) <= 1e-6 * scale:
                    hit = nt
                    break
            if hit is None:
                return None
            used.add(hit)
            out[ot] = hit
        return out

    bb = gmsh.model.getBoundingBox(-1, -1)
    scale = max(float(np.linalg.norm(np.array(bb[3:]) - np.array(bb[:3]))), 1.0)
    fmap, vmap = match(old_f, new_f, scale), match(old_v, new_v, scale)
    if fmap is None or vmap is None:
        progress("sweep rebuild did not reproduce the original faces; "
                 "meshing with tetrahedra instead")
        return None
    progress(f"sweeping {len(vmap)} solid(s) into hexahedra along "
             f"{np.round(d, 3).tolist()}")
    return {"faces": fmap, "volumes": vmap}


def _element_kinds(gmsh) -> dict:
    """{element name: count} over the solid mesh, for reporting what was made."""
    out = {}
    for _, tag in gmsh.model.getEntities(3):
        for et in gmsh.model.mesh.getElementTypes(3, int(tag)):
            tags, _ = gmsh.model.mesh.getElementsByType(et, int(tag))
            name = gmsh.model.mesh.getElementProperties(et)[0]
            out[name] = out.get(name, 0) + len(tags)
    return out


# gmsh element types that are solid hexahedra
_HEX_TYPES = (5, 12, 17, 92, 93)


def _hex_is_usable(gmsh) -> "tuple[bool, str]":
    """Did asking for hexes actually produce a mesh worth having?

    Measured, not assumed. gmsh will happily accept the request and then
    return a mesh with no hexes in it at all, or one with inverted elements,
    depending entirely on the topology it was given:

        plain thin plate   275 hexes,  worst Jacobian 0.835   (7x fewer nodes)
        plate with a hole    0 hexes,  4295 tets + 643 pyramids, 0.077
        L-bracket            0 hexes,  5136 tets + 886 pyramids, 0.100
        two-part assembly  gmsh refuses: non-manifold quad boundaries

    In the last three the tet mesh was better than what asking for hexes
    produced. So the answer is checked and rejected rather than shipped: no
    hexes, or a negative Jacobian anywhere, means fall back.
    """
    import numpy as _np
    nhex = ntot = 0
    worst = 1.0
    for _, tag in gmsh.model.getEntities(3):
        for et in gmsh.model.mesh.getElementTypes(3, int(tag)):
            tags, _ = gmsh.model.mesh.getElementsByType(et, int(tag))
            if not len(tags):
                continue
            ntot += len(tags)
            if int(et) in _HEX_TYPES:
                nhex += len(tags)
            q = _np.asarray(gmsh.model.mesh.getElementQualities(tags, "minSICN"))
            worst = min(worst, float(q.min()))
    if not ntot:
        return False, "no elements"
    if not nhex:
        return False, "this shape does not sweep, so gmsh produced no hexahedra"
    if worst <= 0.0:
        return False, f"inverted elements (worst Jacobian {worst:.3f})"
    return True, (f"{nhex} of {ntot} elements are hexahedra, "
                  f"worst Jacobian {worst:.3f}")


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
    # contact interfaces: one group per side, so the solver can pair them
    for i, c in enumerate(setup.get("contacts", [])):
        if c.get("suppressed"):
            continue
        if c.get("faces_a"):
            sets[f"CTA{i + 1}"] = [int(x) for x in c["faces_a"]]
        if c.get("faces_b"):
            sets[f"CTB{i + 1}"] = [int(x) for x in c["faces_b"]]
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


def _face_set_stations(gmsh, ftags, axis, origin) -> "tuple|None":
    """(min, max) of the picked faces' mesh nodes projected on the bolt axis.

    Measured from the mesh rather than the geometry so it is exact whatever
    the axis orientation, and so a picked bearing plane (all nodes at one
    station) and a picked hole cylinder (nodes spread over the plate
    thickness) go through the same code.
    """
    lo, hi = None, None
    for t in ftags:
        try:
            tags, coords, _ = gmsh.model.mesh.getNodes(2, int(t), True)
        except Exception:                       # noqa: BLE001
            continue
        if not len(tags):
            continue
        p = np.asarray(coords, dtype=float).reshape(-1, 3) - origin
        # elementwise, not matmul: numpy hands a matmul of this size to the
        # platform BLAS, which on macOS/Accelerate raises a spurious
        # divide-by-zero warning on finite input. Small faces took the naive
        # path and stayed quiet, so it only appeared on the big ones.
        sv = (p * axis).sum(axis=1)
        lo = sv.min() if lo is None else min(lo, float(sv.min()))
        hi = sv.max() if hi is None else max(hi, float(sv.max()))
    return None if lo is None else (float(lo), float(hi))


def _add_bolt_beams(gmsh, meta: dict, setup: dict, progress, remap=None) -> list:
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

        # The beam has to span the CLAMPED LENGTH — head bearing face to nut
        # bearing face — because that length is the bolt's elastic length. It
        # sets the bolt's stiffness, so it sets how much of an external load
        # the bolt takes, and the sizing calculation reads it back as l_K.
        #
        # Projecting the picked faces' centroids gave half of it whenever the
        # user picked the hole cylinders, which is the documented way to pick
        # a bolt: the centroid of a hole through an 8 mm plate sits at 4 mm,
        # so a bolt through two 8 mm plates came out 8 mm long instead of 16.
        # Taking the OUTER extreme of the picked faces instead gives the true
        # grip, and gives the same answer whether the user picked the hole
        # cylinders or the bearing faces under head and nut.
        # `meta` is import-time data keyed by the ORIGINAL tags, so the
        # centroids above use them as-is; gmsh has been rebuilt if this is a
        # swept mesh, so only the queries against gmsh are translated.
        fm = (remap or {}).get("faces") or {}
        tr = lambda ts: [fm.get(int(t), int(t)) for t in ts]
        sa = _face_set_stations(gmsh, tr(b.get("side_a_faces", [])), axis, mid)
        sb = _face_set_stations(gmsh, tr(b.get("side_b_faces", [])), axis, mid)
        ta = float((ca - mid) @ axis)
        tb = float((cb - mid) @ axis)
        if sa and sb:
            if ta >= tb:                       # A is the +axis side
                ta, tb = sa[1], sb[0]
            else:
                ta, tb = sa[0], sb[1]
        else:
            progress(f"bolt {i + 1}: could not measure the picked faces; "
                     "grip falls back to the face centroids")
        pa = mid + axis * ta
        pb = mid + axis * tb
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


def _face_group_areas(gmsh, group_names) -> dict:
    """{group: {node_tag: lumped area}} — every node on the face gets a share.

    Deliberately NOT `_face_group_weights`. Those are consistent-load weights,
    where the corner nodes of a quadratic triangle carry exactly zero. That is
    right for turning a pressure into forces and wrong for asking "what
    fraction of this interface is slipping": half the nodes, including the
    corners where stress concentrates, would weigh nothing.

    Here the element's area is split evenly over all of its nodes. It is a
    lumped area, not an exact integration weight, which is all a fraction-of-
    area report needs.
    """
    out = {}
    want = set(group_names)
    for dim, tag in gmsh.model.getPhysicalGroups(2):
        name = gmsh.model.getPhysicalName(dim, tag)
        if name not in want:
            continue
        areas = {}
        for ent in gmsh.model.getEntitiesForPhysicalGroup(dim, tag):
            etypes, _, enodes = gmsh.model.mesh.getElements(2, int(ent))
            for et, en in zip(etypes, enodes):
                npery = {2: 3, 9: 6}.get(int(et))
                if not npery:
                    continue
                conn = np.asarray(en, dtype=np.int64).reshape(-1, npery)
                pts = {int(t): np.asarray(gmsh.model.mesh.getNode(int(t))[0],
                                          dtype=float)
                       for t in np.unique(conn)}
                for row in conn:
                    p0, p1, p2 = (pts[int(row[k])] for k in range(3))
                    area = 0.5 * float(np.linalg.norm(np.cross(p1 - p0, p2 - p0)))
                    if area <= 0:
                        continue
                    share = area / npery
                    for t in row:
                        areas[int(t)] = areas.get(int(t), 0.0) + share
        if areas:
            out[name] = areas
    return out


def _face_group_weights(gmsh, group_names) -> dict:
    """{group: {node_tag: area_weight}} for a uniform unit traction.

    T3: each corner gets A/3.
    T6: corners get 0 and mid-side nodes A/3 — the standard consistent load
    vector for a quadratic triangle under uniform pressure.
    """
    out = {}
    want = set(group_names)
    for dim, tag in gmsh.model.getPhysicalGroups(2):
        name = gmsh.model.getPhysicalName(dim, tag)
        if name not in want:
            continue
        weights = {}
        for ent in gmsh.model.getEntitiesForPhysicalGroup(dim, tag):
            etypes, etags, enodes = gmsh.model.mesh.getElements(2, int(ent))
            for et, en in zip(etypes, enodes):
                npery = {2: 3, 9: 6}.get(int(et))
                if not npery:
                    continue
                conn = np.asarray(en, dtype=np.int64).reshape(-1, npery)
                pts = {}
                for t in np.unique(conn):
                    c = gmsh.model.mesh.getNode(int(t))[0]
                    pts[int(t)] = np.asarray(c, dtype=float)
                for row in conn:
                    p0, p1, p2 = (pts[int(row[k])] for k in range(3))
                    area = 0.5 * float(np.linalg.norm(np.cross(p1 - p0, p2 - p0)))
                    if area <= 0:
                        continue
                    carry = row[3:] if npery == 6 else row[:3]
                    share = area / 3.0
                    for t in carry:
                        weights[int(t)] = weights.get(int(t), 0.0) + share
                    # Corner nodes of a quadratic face carry no load, but they
                    # ARE on the face. Recording them with zero weight keeps
                    # the load exact while giving callers the complete node
                    # set — the reaction sum was missing them, and came out
                    # 0.6 % short of the applied load for that reason alone.
                    if npery == 6:
                        for t in row[:3]:
                            weights.setdefault(int(t), 0.0)
        if weights:
            out[name] = weights
    return out


# Abaqus element types gmsh emits for 2D physical groups. CalculiX reads a
# bare CPS/CPE element as a genuine plane-stress or plane-strain element and
# then refuses the model because it does not lie in z=0. The surfaces are only
# there to carry node sets for boundary conditions, so the elements go and the
# node sets stay.
_SURFACE_TYPES = ("CPS3", "CPS4", "CPS6", "CPS8", "CPE3", "CPE4", "CPE6",
                  "CPE8", "S3", "S4", "S6", "S8", "STRI65", "T3D2", "T3D3")


def _strip_surface_elements(path: str) -> None:
    """Rewrite an Abaqus deck keeping solid elements and every node set."""
    out, skip = [], False
    dropped = set()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            head = line.strip().upper()
            if head.startswith("*"):
                if head.startswith("*ELEMENT"):
                    etype = ""
                    for part in line.split(","):
                        if "TYPE" in part.upper():
                            etype = part.split("=")[-1].strip().upper()
                    skip = etype in _SURFACE_TYPES
                    if skip:
                        for part in line.split(","):
                            if "ELSET" in part.upper():
                                dropped.add(part.split("=")[-1].strip())
                elif head.startswith("*ELSET"):
                    name = line.split("=")[-1].strip()
                    skip = name in dropped
                else:
                    skip = False
            if not skip:
                out.append(line)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(out)


# Abaqus/CalculiX face numbering for a tetrahedron, by corner nodes. Contact
# masters must be element FACES, not loose surface elements, so each boundary
# triangle has to be traced back to the tet it belongs to and told which of
# that tet's four faces it is.
_TET_FACES = ((0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0))


def _face_group_element_faces(gmsh, group_names) -> dict:
    """{group: [[element_tag, face_number], ...]} for solid element faces."""
    want = set(group_names)
    if not want:
        return {}

    # every tet face, keyed by its sorted corner nodes.
    #
    # _TET_FACES is a tetrahedron's face numbering and nothing else's. A mesh
    # with hexahedra in it would come back with a map covering only part of
    # the interface, and a CalculiX contact built on a partial master surface
    # is wrong in a way nothing downstream can see. Refuse the whole map
    # instead; the caller already treats an empty one as "re-mesh for ccx".
    for _, tag in gmsh.model.getEntities(3):
        for et in gmsh.model.mesh.getElementTypes(3, int(tag)):
            if int(et) not in (4, 11):
                raise ValueError(
                    "element faces are mapped for tetrahedra only; this mesh "
                    "has other solid elements")

    lookup = {}
    for dim, tag in gmsh.model.getEntities(3):
        etypes, etags, enodes = gmsh.model.mesh.getElements(3, int(tag))
        for et, ets, en in zip(etypes, etags, enodes):
            npery = {4: 4, 11: 10}.get(int(et))
            if not npery:
                continue
            conn = np.asarray(en, dtype=np.int64).reshape(-1, npery)
            tags = np.asarray(ets, dtype=np.int64)
            for row, etag in zip(conn, tags):
                for fi, idx in enumerate(_TET_FACES):
                    key = tuple(sorted(int(row[k]) for k in idx))
                    lookup.setdefault(key, (int(etag), fi + 1))

    out = {}
    for dim, tag in gmsh.model.getPhysicalGroups(2):
        name = gmsh.model.getPhysicalName(dim, tag)
        if name not in want:
            continue
        faces = []
        for ent in gmsh.model.getEntitiesForPhysicalGroup(dim, tag):
            etypes, _, enodes = gmsh.model.mesh.getElements(2, int(ent))
            for et, en in zip(etypes, enodes):
                npery = {2: 3, 9: 6}.get(int(et))
                if not npery:
                    continue
                conn = np.asarray(en, dtype=np.int64).reshape(-1, npery)
                for row in conn:
                    hit = lookup.get(tuple(sorted(int(row[k]) for k in range(3))))
                    if hit:
                        faces.append([hit[0], hit[1]])
        if faces:
            out[name] = faces
    return out


def _face_group_normals(gmsh, group_names) -> dict:
    """{group: {normal, flatness}} — the area-weighted mean unit normal, and
    how close the group is to being one flat plane.

    A frictionless (symmetry) support constrains motion along the surface
    normal. On a plane that is one direction for the whole face; on a curved
    face it differs node to node, and a solver that takes one vector per node
    set cannot express it. `flatness` is the minimum of n_i . n_mean over the
    group, so 1.0 is exactly planar — enough to decide honestly whether the
    constraint can be written at all.
    """
    out = {}
    want = set(group_names)
    for dim, tag in gmsh.model.getPhysicalGroups(2):
        name = gmsh.model.getPhysicalName(dim, tag)
        if name not in want:
            continue
        acc = np.zeros(3)
        normals, areas = [], []
        for ent in gmsh.model.getEntitiesForPhysicalGroup(dim, tag):
            etypes, _, enodes = gmsh.model.mesh.getElements(2, int(ent))
            for et, en in zip(etypes, enodes):
                npery = {2: 3, 9: 6}.get(int(et))
                if not npery:
                    continue
                conn = np.asarray(en, dtype=np.int64).reshape(-1, npery)
                pts = {int(t): np.asarray(gmsh.model.mesh.getNode(int(t))[0], dtype=float)
                       for t in np.unique(conn[:, :3])}
                for row in conn:
                    p0, p1, p2 = (pts[int(row[k])] for k in range(3))
                    n = np.cross(p1 - p0, p2 - p0)
                    a = 0.5 * float(np.linalg.norm(n))
                    if a <= 0:
                        continue
                    normals.append(n / (2 * a))
                    areas.append(a)
                    acc += n / 2.0
        if not normals:
            continue
        mean = acc / max(np.linalg.norm(acc), 1e-30)
        dots = [float(np.dot(nn, mean)) for nn in normals]
        out[name] = {"normal": [round(float(x), 8) for x in mean],
                     "flatness": round(min(dots), 6),
                     "area": round(float(sum(areas)), 6)}
    return out
