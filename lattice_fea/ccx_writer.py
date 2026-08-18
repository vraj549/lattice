"""Generate CalculiX (ccx) input decks.

Why a second solver at all: code_aster only runs through WSL or Docker, so on
macOS and on a plain Linux box there is nothing to run. CalculiX installs from
a package manager, is a single binary, and covers exactly the ground this tool
cares most about — static with contact, and modal.

Units are the same mm-t-s system the rest of Lattice uses, so E is in MPa,
density in tonne/mm^3, force in N, and frequencies come out in Hz.

Loads are applied as consistent nodal forces (*CLOAD) computed at mesh time by
integrating the element shape functions over each face group. CalculiX's own
distributed-load syntax addresses element FACES by number, which a mesh
exported from physical groups does not carry; integrating ourselves is exact
for a uniform traction and avoids inventing a face map that could be wrong.
"""
from __future__ import annotations

import math

from .materials import to_solver_units
from .meshing import group_name

G_MM = 9810.0

# What this writer covers.
#
# One table, consulted by the deck writer AND published to the UI, so the
# blockers you see before running are the same rules the writer enforces.
# Anything absent stays on code_aster rather than being approximated.
CAPABILITIES = {
    "types": ["static", "modal"],
    "supports": ["fixed", "disp", "frictionless"],
    "loads": ["force", "pressure", "gravity", "rotation"],
    "features": ["contacts"],
}
SUPPORTED = tuple(CAPABILITIES["types"])


def _f(x) -> str:
    return f"{float(x):.10g}"


class Deck:
    def __init__(self):
        self.lines = []

    def w(self, s=""):
        self.lines.append(s)

    def text(self):
        return "\n".join(self.lines) + "\n"


def unsupported_reason(analysis: dict, setup: dict, mesh_stats: dict = None) -> "str|None":
    """Why CalculiX cannot run this analysis, or None if it can.

    Checked before a run, not discovered during one — the same function backs
    the blockers shown in the analysis panel.
    """
    t = analysis.get("type")
    if t not in CAPABILITIES["types"]:
        return (f"CalculiX runs {' and '.join(CAPABILITIES['types'])} in Lattice; "
                f"'{t}' needs code_aster.")
    if setup.get("bolts"):
        return ("Bolt beams need a distributing coupling and a pre-tension "
                "section, which this writer does not emit yet — bolted models "
                "need code_aster.")
    if setup.get("ties"):
        return "Tie constraints are not emitted for CalculiX yet; use code_aster."
    for l in analysis.get("loads", []):
        if l.get("type") == "remote":
            return ("Remote force/moment needs a distributing coupling, which "
                    "this writer does not emit yet — use code_aster.")
        if l.get("type") not in CAPABILITIES["loads"]:
            return f"CalculiX: load type '{l.get('type')}' is not supported yet."
    for i, sup in enumerate(analysis.get("supports", [])):
        if sup.get("type") not in CAPABILITIES["supports"]:
            return f"CalculiX: support type '{sup.get('type')}' is not supported yet."
        if sup.get("type") == "frictionless" and mesh_stats:
            g = group_name("SUP", 1, i + 1)
            info = (mesh_stats.get("face_normals") or {}).get(g)
            if info and info.get("flatness", 1.0) < 0.999:
                return (f"Support '{sup.get('name', g)}' is frictionless on a "
                        "curved face. CalculiX takes one normal per node set, "
                        "so only a planar symmetry face can be written; use "
                        "code_aster, which constrains each node's own normal.")

    return None


def frictionless_nodes(analysis: dict, mesh_stats: dict, index: int) -> list:
    """Nodes to transform for one frictionless support.

    A node can carry only one coordinate transform, and a symmetry face
    usually shares an edge with something else. Where they meet, the more
    restrictive support wins: the shared nodes are dropped from the symmetry
    set and stay fixed in global coordinates. Rotating them instead would
    apply the neighbouring support's "fixed" in the symmetry frame, which
    means something entirely different.
    """
    face_nodes = mesh_stats.get("face_nodes") or {}
    mine = {int(n) for n in (face_nodes.get(group_name("SUP", 1, index + 1)) or {})}
    for j, other in enumerate(analysis.get("supports", [])):
        if j == index or not other.get("faces"):
            continue
        if other.get("type") == "frictionless":
            continue
        mine -= {int(n) for n in (face_nodes.get(group_name("SUP", 1, j + 1)) or {})}
    # Loaded nodes come out too. A transform rotates everything at that node,
    # applied forces included — so a symmetry face sharing an edge with a
    # loaded face would silently turn part of the load in a new direction.
    # Losing the symmetry constraint on a line of edge nodes is a far smaller
    # error than applying the load somewhere other than where it was asked for.
    for j, l in enumerate(analysis.get("loads", [])):
        if l.get("faces"):
            mine -= {int(n) for n in (face_nodes.get(group_name("LOA", 1, j + 1)) or {})}
    return sorted(mine)


def _materials(d: Deck, setup: dict, meta: dict) -> None:
    mats = {m["id"]: m for m in setup.get("materials", [])}
    assign = setup.get("assignments", {})
    if not assign:
        raise ValueError("No material assignments — assign a material to every solid.")
    seen = {}
    for k, mid in sorted(assign.items()):
        if mid not in mats:
            raise ValueError(f"Solid {k} references unknown material '{mid}'")
        if mid in seen:
            continue
        u = to_solver_units(mats[mid])
        name = f"MAT{len(seen) + 1}"
        seen[mid] = name
        d.w(f"*MATERIAL, NAME={name}")
        d.w("*ELASTIC")
        d.w(f"{_f(u['E'])}, {_f(u['NU'])}")
        d.w("*DENSITY")
        d.w(f"{_f(u['RHO'])}")
    for k, mid in sorted(assign.items()):
        d.w(f"*SOLID SECTION, ELSET=V{int(k)}, MATERIAL={seen[mid]}")
    d.w()


def _boundaries(d: Deck, analysis: dict, ai: int, mesh_stats: dict) -> None:
    """*BOUNDARY per support. DOF numbering is 1..3 for UX..UZ."""
    normals = mesh_stats.get("face_normals") or {}
    any_written = False
    for i, s in enumerate(analysis.get("supports", [])):
        if not s.get("faces"):
            continue
        g = group_name("SUP", ai, i + 1)
        stype = s.get("type", "fixed")

        if stype == "frictionless":
            # Rotate the node set into a frame whose local 1-axis is the face
            # normal, then hold only that direction: motion normal to the face
            # is blocked, sliding within it is free. This is what a symmetry
            # plane is, and it only works because the face is planar — which
            # unsupported_reason() has already established.
            info = normals.get(g)
            if not info:
                raise ValueError(
                    f"Support '{s.get('name', g)}': the mesh carries no normal "
                    f"for {g}. Re-mesh, then run again.")
            nodes = frictionless_nodes(analysis, mesh_stats, i)
            if not nodes:
                raise ValueError(
                    f"Support '{s.get('name', g)}': every node of this "
                    "frictionless face is already held by another support.")
            n = info["normal"]
            t1 = _perp(n)
            setname = f"{g}_SYM"
            d.w(f"** frictionless: local 1-axis along the face normal {n}")
            d.w(f"*NSET, NSET={setname}")
            for k in range(0, len(nodes), 8):
                d.w(", ".join(str(x) for x in nodes[k:k + 8]) + ",")
            d.w(f"*TRANSFORM, NSET={setname}, TYPE=R")
            d.w(f"{_f(n[0])}, {_f(n[1])}, {_f(n[2])}, "
                f"{_f(t1[0])}, {_f(t1[1])}, {_f(t1[2])}")
            d.w("*BOUNDARY")
            d.w(f"{setname}, 1, 1, 0.")
            any_written = True
            continue

        d.w("*BOUNDARY")
        if stype == "fixed":
            d.w(f"{g}, 1, 3, 0.")
            any_written = True
        else:
            for dof, key in ((1, "ux"), (2, "uy"), (3, "uz")):
                v = s.get(key)
                if v is not None:
                    d.w(f"{g}, {dof}, {dof}, {_f(v)}")
                    any_written = True
    if not any_written:
        raise ValueError(
            f"'{analysis.get('name', 'Analysis')}' has no support with faces.")
    d.w()


def _perp(n):
    """Any unit vector perpendicular to n — the local 2-axis of the transform.
    Which one does not matter: both in-plane directions are left free."""
    a = [0.0, 0.0, 1.0] if abs(n[2]) < 0.9 else [1.0, 0.0, 0.0]
    v = [n[1] * a[2] - n[2] * a[1], n[2] * a[0] - n[0] * a[2], n[0] * a[1] - n[1] * a[0]]
    m = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / m for x in v]


def applied_total(analysis: dict, mesh_stats: dict) -> list:
    """Resultant of every face load, in N — what the reactions must balance.

    Pressure counts too: p x area along the inward face normal. Leaving it out
    made the equilibrium check silently inapplicable to any pressure-loaded
    model, which is exactly the sort of gap that turns a safety check into
    decoration.
    """
    normals = mesh_stats.get("face_normals") or {}
    tot = [0.0, 0.0, 0.0]
    for i, l in enumerate(analysis.get("loads", [])):
        t = l.get("type")
        if not l.get("faces"):
            continue
        if t == "force":
            for k, key in enumerate(("fx", "fy", "fz")):
                tot[k] += float(l.get(key, 0) or 0)
        elif t == "pressure":
            info = normals.get(group_name("LOA", 1, i + 1))
            if not info:
                return [0.0, 0.0, 0.0]      # cannot state it exactly; do not guess
            p = float(l.get("pressure", 0) or 0)
            for k in range(3):
                tot[k] -= p * info["area"] * info["normal"][k]
    return tot


def support_frames(analysis: dict, mesh_stats: dict) -> list:
    """Per support: its nodes, and the local frame its reactions are reported in.

    A frictionless support is written with a *TRANSFORM, and CalculiX then
    reports that node's forces in the LOCAL frame. Summing those as if they
    were global is simply wrong — it is what made the equilibrium check
    disagree by 3 % on a model that was perfectly in balance.
    """
    face_nodes = mesh_stats.get("face_nodes") or {}
    normals = mesh_stats.get("face_normals") or {}
    out = []
    for i, s in enumerate(analysis.get("supports", [])):
        if not s.get("faces"):
            continue
        g = group_name("SUP", 1, i + 1)
        nodes = [int(n) for n in (face_nodes.get(g) or {})]
        if not nodes:
            continue
        frame = None
        if s.get("type") == "frictionless":
            nodes = frictionless_nodes(analysis, mesh_stats, i)
            info = normals.get(g)
            if info:
                n = info["normal"]
                t1 = _perp(n)
                t2 = [n[1] * t1[2] - n[2] * t1[1],
                      n[2] * t1[0] - n[0] * t1[2],
                      n[0] * t1[1] - n[1] * t1[0]]
                frame = [n, t1, t2]          # rows: local axes in global coords
        out.append({"group": g, "nodes": nodes, "frame": frame,
                    "type": s.get("type", "fixed")})
    return out


def support_nodes(analysis: dict, mesh_stats: dict) -> list:
    """Flat list of every supported node."""
    return [n for f in support_frames(analysis, mesh_stats) for n in f["nodes"]]


def _loads(d: Deck, analysis: dict, ai: int, meta: dict, mesh_stats: dict) -> bool:
    """Every load of this analysis. Returns True if anything was written."""
    face_nodes = mesh_stats.get("face_nodes") or {}
    face_elems = mesh_stats.get("face_elems") or {}
    wrote = False
    grav = None
    rot = None
    cload, dload = [], []

    for i, l in enumerate(analysis.get("loads", [])):
        t = l.get("type")
        g = group_name("LOA", ai, i + 1)

        if t == "gravity":
            mag = float(l.get("g_mag", 1.0)) * G_MM
            v = l.get("g") or [0, 0, -1]
            n = math.sqrt(sum(float(x) ** 2 for x in v)) or 1.0
            grav = (mag, [float(x) / n for x in v])
            continue
        if t == "rotation":
            # CalculiX CENTRIF takes omega^2, plus a point on the axis and its
            # direction. rpm -> rad/s first.
            omega = float(l.get("rpm", 0) or 0) * 2.0 * math.pi / 60.0
            axis = l.get("axis") or [0, 0, 1]
            n = math.sqrt(sum(float(x) ** 2 for x in axis)) or 1.0
            rot = (omega * omega, l.get("center") or [0, 0, 0],
                   [float(x) / n for x in axis])
            continue
        if not l.get("faces"):
            continue

        if t == "pressure":
            # A real distributed load on the element faces. Positive presses
            # into the surface, the same convention as code_aster's PRES_REP,
            # so a model means the same thing whichever solver runs it.
            faces = face_elems.get(g)
            if not faces:
                raise ValueError(
                    f"Load '{l.get('name', g)}': the mesh carries no element "
                    f"faces for {g}. Re-mesh, then run again.")
            pres = float(l.get("pressure", 0) or 0)
            if pres:
                dload.append((f"** pressure {l.get('name', g)} on {g}",
                              [f"{e}, P{fn}, {_f(pres)}" for e, fn in faces]))
            continue

        # force: consistent nodal loads, exact for a uniform traction
        w = face_nodes.get(g)
        if not w:
            raise ValueError(
                f"Load '{l.get('name', g)}': the mesh carries no nodal weights "
                f"for {g}. Re-mesh, then run again.")
        total_area = sum(w.values())
        if total_area <= 0:
            raise ValueError(f"Load '{l.get('name', g)}': face area is zero")
        fx, fy, fz = (float(l.get(k, 0) or 0) for k in ("fx", "fy", "fz"))
        rows = []
        for node, weight in sorted(w.items(), key=lambda kv: int(kv[0])):
            frac = weight / total_area
            for dof, comp in ((1, fx), (2, fy), (3, fz)):
                if comp:
                    rows.append(f"{int(node)}, {dof}, {_f(comp * frac)}")
        if rows:
            cload.append((f"** force {l.get('name', g)} on {g}", rows))

    for comment, rows in cload:
        d.w(comment)
        d.w("*CLOAD")
        for r in rows:
            d.w(r)
        wrote = True
    for comment, rows in dload:
        d.w(comment)
        d.w("*DLOAD")
        for r in rows:
            d.w(r)
        wrote = True

    body = []
    vols = [f"V{s['tag']}" for s in meta.get("solids", [])]
    if grav:
        mag, v = grav
        body += [f"{g}, GRAV, {_f(mag)}, {_f(v[0])}, {_f(v[1])}, {_f(v[2])}"
                 for g in vols]
    if rot:
        w2, c, a = rot
        body += [f"{g}, CENTRIF, {_f(w2)}, {_f(c[0])}, {_f(c[1])}, {_f(c[2])}, "
                 f"{_f(a[0])}, {_f(a[1])}, {_f(a[2])}" for g in vols]
    if body:
        d.w("*DLOAD")
        for r in body:
            d.w(r)
        wrote = True
    d.w()
    return wrote


def _contacts(d: Deck, setup: dict, mesh_stats: dict) -> None:
    """*CONTACT PAIR per interface.

    CalculiX pairs a SLAVE node surface with a MASTER element-face surface —
    a node set cannot be the master, which is why the mesh records which face
    of which tet every boundary triangle is. Bonded pairs use TIE, which stays
    linear; the rest are a real contact search and make the step nonlinear.
    """
    have = set(mesh_stats.get("face_groups") or [])
    elems = mesh_stats.get("face_elems") or {}
    pairs = []
    for i, c in enumerate(setup.get("contacts", []) or []):
        if c.get("suppressed") or not (c.get("faces_a") and c.get("faces_b")):
            continue
        ga, gb = f"CTA{i + 1}", f"CTB{i + 1}"
        if have and not (ga in have and gb in have):
            raise ValueError(
                f"The mesh does not contain the faces of contact "
                f"'{c.get('name', ga)}' — re-mesh before solving.")
        if ga not in elems:
            raise ValueError(
                f"Contact '{c.get('name', ga)}': the mesh carries no element "
                "faces for the master side. Re-mesh, then run again.")
        pairs.append((i + 1, c, ga, gb))
    if not pairs:
        return

    for idx, c, ga, gb in pairs:
        # master: element faces, grouped by face number into one ELSET each
        by_face = {}
        for etag, fno in elems[ga]:
            by_face.setdefault(fno, []).append(etag)
        d.w(f"*SURFACE, NAME=M{idx}, TYPE=ELEMENT")
        for fno, tags in sorted(by_face.items()):
            name = f"EM{idx}F{fno}"
            d.w(f"** face {fno} of {len(tags)} elements")
            d.w(f"*ELSET, ELSET={name}")
            for k in range(0, len(tags), 8):
                d.w(", ".join(str(t) for t in tags[k:k + 8]) + ",")
            d.w(f"*SURFACE, NAME=M{idx}, TYPE=ELEMENT")
            d.w(f"{name}, S{fno}")
        # slave: the node set of the other side
        d.w(f"*SURFACE, NAME=SL{idx}, TYPE=NODE")
        d.w(f"{gb},")

    for idx, c, ga, gb in pairs:
        kind = c.get("kind", "bonded")
        d.w(f"*SURFACE INTERACTION, NAME=I{idx}")
        d.w("*SURFACE BEHAVIOR, PRESSURE-OVERCLOSURE=LINEAR")
        d.w("1e5, 3.0")
        if kind == "friction":
            d.w("*FRICTION")
            d.w(f"{_f(c.get('mu') or 0.2)}, 1e4")
        typ = "TIE" if kind == "bonded" else "NODE TO SURFACE"
        d.w(f"*CONTACT PAIR, INTERACTION=I{idx}, TYPE={typ}")
        d.w(f"SL{idx}, M{idx}")
    d.w()


def build_deck(analysis: dict, setup: dict, meta: dict, mesh_stats: dict,
               mesh_name: str = "mesh.inp") -> str:
    reason = unsupported_reason(analysis, setup, mesh_stats)
    if reason:
        raise ValueError(reason)

    ai = 1 + [a["id"] for a in setup.get("analyses", [])].index(analysis["id"])
    d = Deck()
    d.w("** Generated by Lattice — CalculiX deck")
    d.w("** units: mm, tonne, s  =>  MPa, N, Hz")
    d.w(f"*INCLUDE, INPUT={mesh_name}")
    d.w()
    _materials(d, setup, meta)
    _contacts(d, setup, mesh_stats)

    atype = analysis.get("type")
    if atype == "modal":
        n = int((analysis.get("config") or {}).get("n_modes") or 10)
        _boundaries(d, analysis, ai, mesh_stats)
        d.w("*STEP")
        d.w("*FREQUENCY, STORAGE=YES")
        d.w(f"{n}")
        d.w("*NODE FILE")
        d.w("U")
        d.w("*END STEP")
    else:
        _boundaries(d, analysis, ai, mesh_stats)
        d.w("*STEP")
        d.w("*STATIC")
        _loads(d, analysis, ai, meta, mesh_stats)
        d.w("*NODE FILE")
        d.w("U, RF")
        d.w("*EL FILE")
        d.w("S, E")
        d.w("*END STEP")
    return d.text()
