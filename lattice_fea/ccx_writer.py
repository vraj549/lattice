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

# What this writer covers. Anything else stays on code_aster rather than being
# silently approximated.
SUPPORTED = ("static", "modal")


def _f(x) -> str:
    return f"{float(x):.10g}"


class Deck:
    def __init__(self):
        self.lines = []

    def w(self, s=""):
        self.lines.append(s)

    def text(self):
        return "\n".join(self.lines) + "\n"


def unsupported_reason(analysis: dict, setup: dict) -> "str|None":
    """Why CalculiX cannot run this analysis, or None if it can."""
    t = analysis.get("type")
    if t not in SUPPORTED:
        return (f"CalculiX support in Lattice covers {' and '.join(SUPPORTED)}; "
                f"'{t}' runs on code_aster.")
    if setup.get("bolts"):
        return ("Bolt beams use a distributing (RBE3) coupling and an axial "
                "pre-strain, which this writer does not emit yet — bolted "
                "models run on code_aster.")
    if any(l.get("type") == "remote" for a in setup.get("analyses", [])
           for l in a.get("loads", [])):
        return "Remote force/moment needs a distributing coupling; use code_aster."
    if setup.get("ties"):
        return "Tie constraints are not emitted for CalculiX yet."
    return None


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


def _boundaries(d: Deck, analysis: dict, ai: int) -> None:
    """*BOUNDARY per support. DOF numbering is 1..3 for UX..UZ."""
    any_written = False
    for i, s in enumerate(analysis.get("supports", [])):
        if not s.get("faces"):
            continue
        g = group_name("SUP", ai, i + 1)
        stype = s.get("type", "fixed")
        d.w("*BOUNDARY")
        if stype == "fixed":
            d.w(f"{g}, 1, 3, 0.")
            any_written = True
        elif stype == "frictionless":
            # A normal-only constraint needs the face normal, which varies over
            # a curved face; refusing is better than picking an axis and being
            # quietly wrong about which way the surface can slide.
            raise ValueError(
                "Frictionless supports are not available on CalculiX in Lattice "
                "yet — they need a per-node normal constraint. Use a fixed or "
                "prescribed-displacement support, or run this on code_aster.")
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


def applied_total(analysis: dict, mesh_stats: dict) -> list:
    """Resultant of every face load, in N. Used to check the solve balances."""
    tot = [0.0, 0.0, 0.0]
    for l in analysis.get("loads", []):
        if l.get("type") == "force" and l.get("faces"):
            for k, key in enumerate(("fx", "fy", "fz")):
                tot[k] += float(l.get(key, 0) or 0)
    return tot


def support_nodes(analysis: dict, mesh_stats: dict) -> list:
    """Every node held by a support — where the reactions live."""
    face_nodes = mesh_stats.get("face_nodes") or {}
    ai = 1
    out = []
    for i, s in enumerate(analysis.get("supports", [])):
        if not s.get("faces"):
            continue
        w = face_nodes.get(group_name("SUP", ai, i + 1)) or {}
        out.extend(int(n) for n in w)
    return out


def _cloads(d: Deck, analysis: dict, ai: int, meta: dict, mesh_stats: dict) -> bool:
    """Consistent nodal forces for every face load. Returns True if any."""
    face_nodes = mesh_stats.get("face_nodes") or {}
    areas = {f["tag"]: f["area"] for f in meta.get("faces", [])}
    wrote = False
    grav = None
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
            raise ValueError("Rotational body loads are not emitted for CalculiX yet.")
        if not l.get("faces"):
            continue
        w = face_nodes.get(g)
        if not w:
            raise ValueError(
                f"Load '{l.get('name', g)}': the mesh carries no nodal weights "
                f"for {g}. Re-mesh, then run again.")
        total_area = sum(w.values())
        if total_area <= 0:
            raise ValueError(f"Load '{l.get('name', g)}': face area is zero")

        if t == "pressure":
            # Pressure acts along the face normal, which these scalar weights
            # do not carry, so it is refused rather than guessed at.
            raise ValueError(
                "Pressure loads are not available on CalculiX in Lattice yet — "
                "they need the face normal. Use a force load, or code_aster.")
        # force: traction = F/A, so each node takes F * (its weight / total)
        fx, fy, fz = (float(l.get(k, 0) or 0) for k in ("fx", "fy", "fz"))
        d.w(f"** load {l.get('name', g)} on {g}")
        d.w("*CLOAD")
        for node, weight in sorted(w.items()):
            frac = weight / total_area
            for dof, comp in ((1, fx), (2, fy), (3, fz)):
                if comp:
                    d.w(f"{node}, {dof}, {_f(comp * frac)}")
        wrote = True

    if grav:
        mag, v = grav
        d.w("*DLOAD")
        for k, _ in sorted(((t["tag"], 0) for t in meta.get("solids", []))):
            d.w(f"V{k}, GRAV, {_f(mag)}, {_f(v[0])}, {_f(v[1])}, {_f(v[2])}")
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
    reason = unsupported_reason(analysis, setup)
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
        _boundaries(d, analysis, ai)
        d.w("*STEP")
        d.w("*FREQUENCY, STORAGE=YES")
        d.w(f"{n}")
        d.w("*NODE FILE")
        d.w("U")
        d.w("*END STEP")
    else:
        _boundaries(d, analysis, ai)
        d.w("*STEP")
        d.w("*STATIC")
        _cloads(d, analysis, ai, meta, mesh_stats)
        d.w("*NODE FILE")
        d.w("U, RF")
        d.w("*EL FILE")
        d.w("S, E")
        d.w("*END STEP")
    return d.text()
