"""Bolted-joint pipeline: cylinder detection, beam creation, spiders, preload."""
import ast
import json
import math
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import comm_writer, geometry, meshing  # noqa: E402
from lattice_fea.config import SolverConfig  # noqa: E402
from lattice_fea.projects import default_setup  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
PLATES = os.path.join(EXAMPLES, "bolted_plates.step")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(PLATES),
    reason="run examples/make_examples.py first")


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    d = tmp_path_factory.mktemp("bolted")
    brep = str(d / "geometry.brep")
    meta = geometry.import_step(PLATES, brep)
    # merge surface fits, as gmsh_worker does
    tess = geometry.tessellate(brep, meta["diag"])
    fits = {f["tag"]: f.get("fit") for f in tess["faces"]}
    for f in meta["faces"]:
        if fits.get(f["tag"]):
            f["fit"] = fits[f["tag"]]
    return brep, meta, d


def hole_cylinders(meta, solid_tag):
    solid = next(s for s in meta["solids"] if s["tag"] == solid_tag)
    return [f for f in meta["faces"]
            if f["tag"] in solid["faces"]
            and (f.get("fit") or {}).get("kind") == "cylinder"]


def test_cylinder_detection(model):
    _, meta, _ = model
    assert len(meta["solids"]) == 2
    assert meta["interfaces"], "stacked plates must share a bonded interface"
    cyls = [f for f in meta["faces"] if (f.get("fit") or {}).get("kind") == "cylinder"]
    assert len(cyls) >= 4, "each plate has two hole cylinders"
    r = cyls[0]["fit"]["radius"]
    assert abs(r - 3.3) < 0.05, f"hole radius should be ~3.3, got {r}"


@pytest.fixture(scope="module")
def meshed(model):
    brep, meta, d = model
    s1, s2 = (s["tag"] for s in meta["solids"])
    top_cyls = hole_cylinders(meta, s2)
    bot_cyls = hole_cylinders(meta, s1)
    setup = default_setup()
    faces = sorted(meta["faces"], key=lambda f: -f["area"])
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Static", "config": {},
        "supports": [{"id": "s1", "name": "fix", "type": "fixed",
                      "faces": [faces[0]["tag"]]}],
        "loads": [{"id": "l1", "name": "pull", "type": "force",
                   "faces": [faces[1]["tag"]], "fx": 0, "fy": 0, "fz": 500}],
    }]
    setup["bolts"] = [
        {"id": "b1", "name": "Bolt 1",
         "side_a_faces": [top_cyls[0]["tag"]], "side_b_faces": [bot_cyls[0]["tag"]],
         "d_mm": 6, "E_GPa": 210, "preload_N": 8000},
        {"id": "b2", "name": "Bolt 2",
         "side_a_faces": [top_cyls[1]["tag"]], "side_b_faces": [bot_cyls[1]["tag"]],
         "d_mm": 6, "E_GPa": 210, "preload_N": 8000},
    ]
    # a probe as well, so the vibration decks (which extract response there)
    # are exercised by the deck tests rather than skipped
    com = faces[1]["com"]
    setup["probes"] = [{"id": "p1", "name": "tip",
                        "x": com[0], "y": com[1], "z": com[2]}]
    unv = str(d / "mesh.unv")
    out = meshing.mesh_project(brep, unv, meta, setup)
    return unv, out, setup, meta


def test_bolt_beams_in_mesh(meshed):
    unv, out, setup, meta = meshed
    stats = out["stats"]
    assert len(stats["bolts"]) == 2
    # The grip is the CLAMPED LENGTH — head bearing face to nut bearing face.
    # Two 8 mm plates stacked from z=0 to z=16, so it is 16 mm. It used to be
    # measured between the two hole centroids, which gave 8: half the bolt.
    # That length is the bolt's elastic length, so it sets how much of an
    # external load the bolt takes, and the sizing reads it back as l_K.
    zs = [f["com"][2] for f in meta["faces"]]
    stack = max(zs) - min(zs)
    for br in stats["bolts"]:
        assert br["length"] == pytest.approx(stack, abs=0.05), br
        # and it spans the outer surfaces, not something inside the material
        assert min(br["end_a"][2], br["end_b"][2]) == pytest.approx(min(zs), abs=0.05)
        assert max(br["end_a"][2], br["end_b"][2]) == pytest.approx(max(zs), abs=0.05)
    txt = open(unv, errors="ignore").read()
    for g in ("BOLT1", "BOLT2", "BFA1", "BFB1", "BFA2", "BFB2"):
        assert g in txt, f"group {g} missing from UNV"


def test_bolted_static_comm(meshed):
    _, out, setup, meta = meshed
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210, "nu": 0.3,
                           "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    comm, export = comm_writer.build_run(
        setup["analyses"][0], setup, meta, out["stats"], SolverConfig())
    for kw in ("MODELISATION='POU_D_T'", "LIAISON_RBE3", "SECTION='CERCLE'",
               "PRE_EPSI", "EFGE_ELNO", "CARA_ELEM=cara", "BN1A", "BN2B",
               "DDL_MAIT=('DX', 'DY', 'DZ', 'DRX', 'DRY', 'DRZ')"):
        assert kw in comm, f"missing {kw}"
    assert "bolt_forces.csv" in export
    # preload strain uses the same area as the beam section — see below
    eps = -8000.0 / (210000.0 * comm_writer.bolt_area(setup["bolts"][0]))
    assert f"EPX={eps!r}"[:14] in comm


def test_bolt_section_uses_tensile_stress_area(meshed):
    """The beam section and the preload strain must be sized on the SAME area.

    Preload is applied as eps = -F/(E*A) so the beam force comes out at exactly
    -F. If the section were sized on the major diameter while the strain used
    the stress area (or the reverse), every bolt force in the model would be
    off by the ratio — silently, and worst on the small screws where that
    ratio is largest.
    """
    _, out, shared, meta = meshed
    # `meshed` is module-scoped and handed out by reference: edit a copy, or
    # this M1.6 becomes bolt 1 for every test that runs after this one.
    setup = json.loads(json.dumps(shared))
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210, "nu": 0.3,
                           "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    # an M1.6: nominal circle is 2.01 mm^2, the real stress area is 1.27
    setup["bolts"][0].update({"size": "M1.6", "d_mm": 1.6, "as_mm2": 1.27,
                              "preload_N": 400.0})
    A = comm_writer.bolt_area(setup["bolts"][0])
    assert A == 1.27
    assert A < math.pi * 0.8 ** 2      # and it is not the major-diameter circle

    comm, _ = comm_writer.build_run(
        setup["analyses"][0], setup, meta, out["stats"], SolverConfig())

    r = math.sqrt(A / math.pi)
    assert f"CARA='R', VALE={r!r}" in comm, "beam radius is not from the stress area"
    eps = -400.0 / (210000.0 * A)
    assert f"EPX={eps!r}" in comm

    # the round trip: E * A_section * eps must return the preload exactly
    assert abs(210000.0 * (math.pi * r ** 2) * eps + 400.0) < 1e-9


def test_bolt_area_falls_back_to_diameter(meshed):
    """Projects saved before sizes carried a stress area still solve."""
    assert comm_writer.bolt_area({"d_mm": 6.0}) == math.pi * 9.0
    assert comm_writer.bolt_area({}) == math.pi * 16.0     # default M8


def test_bolted_modal_comm(meshed):
    _, out, setup, meta = meshed
    comm, _ = comm_writer.build_run(
        {**setup["analyses"][0], "type": "modal", "config": {"n_modes": 8}, "loads": []},
        setup, meta, out["stats"], SolverConfig())
    # spiders must be in the assembled charges; preload must NOT appear
    assert "CHARGE=(fix, spider,)" in comm
    assert "PRE_EPSI" not in comm
    assert "CARA_ELEM=cara" in comm


def test_stale_mesh_guard(meshed):
    _, out, setup, meta = meshed
    setup2 = {**setup, "bolts": setup["bolts"] + [
        {"id": "b3", "name": "late bolt", "side_a_faces": [1], "side_b_faces": [2],
         "d_mm": 6, "E_GPa": 210, "preload_N": 0}]}
    with pytest.raises(ValueError, match="(?i)re-mesh"):
        comm_writer.build_run(setup2["analyses"][0], setup2, meta,
                              out["stats"], SolverConfig())


def test_tie_comm(meshed):
    _, out, setup, meta = meshed
    s1 = meta["solids"][0]["tag"]
    faces = sorted(meta["faces"], key=lambda f: -f["area"])
    setup2 = {**setup, "ties": [{"id": "t1", "slave_faces": [faces[2]["tag"]],
                                 "master_solid": s1}]}
    # note: TIE group only lands in UNV after re-mesh; here we just check emission
    stats = {**out["stats"], "face_groups": out["stats"]["face_groups"] + ["TIE1"]}
    comm, _ = comm_writer.build_run(setup2["analyses"][0], setup2, meta,
                                    stats, SolverConfig())
    assert "LIAISON_MAIL" in comm
    assert f"GROUP_MA_MAIT=('V{s1}',)" in comm
    assert "TYPE_RACCORD='MASSIF'" in comm

# ---------------------------------------------------- preload calibration

def test_preloaded_bolts_lists_targets(meshed):
    _, out, setup, meta = meshed
    got = comm_writer.preloaded_bolts(setup, out["stats"])
    assert [b["F"] for b in got] == [8000.0, 8000.0]
    assert {b["index"] for b in got} == {1, 2}


def test_unpreloaded_bolts_are_not_listed(meshed):
    """Nothing to calibrate on a sizing run, so it must not cost a solve."""
    _, out, setup, meta = meshed
    s2 = json.loads(json.dumps(setup))
    for b in s2["bolts"]:
        b["preload_N"] = 0
    assert comm_writer.preloaded_bolts(s2, out["stats"]) == []


def test_preload_scale_multiplies_the_imposed_strain(meshed):
    _, out, setup, meta = meshed
    a = setup["analyses"][0]
    base, _ = comm_writer.build_run(a, setup, meta, out["stats"], SolverConfig())
    scaled, _ = comm_writer.build_run(a, setup, meta, out["stats"], SolverConfig(),
                                      preload_scale={1: 1.25, 2: 1.0})

    def epx(text):
        return {int(m.group(1)): float(m.group(2)) for m in re.finditer(
            r"GROUP_MA=\('BOLT(\d+)',\), EPX=([-\d.eE+]+)", text)}

    b, s = epx(base), epx(scaled)
    assert set(b) == {1, 2}
    assert s[1] == pytest.approx(b[1] * 1.25, rel=1e-9)
    assert s[2] == pytest.approx(b[2], rel=1e-9)


def test_calibration_deck_drops_the_external_load_and_field_output(meshed):
    """It exists to measure bolt force under preload alone. Anything else in
    it is either wrong (the external load) or wasted (the MED write)."""
    _, out, setup, meta = meshed
    a = setup["analyses"][0]
    full, _ = comm_writer.build_run(a, setup, meta, out["stats"], SolverConfig())
    cal, _ = comm_writer.build_run(a, setup, meta, out["stats"], SolverConfig(),
                                   calibration=True)
    assert "PRE_EPSI" in cal
    assert "FORCE_FACE" in full and "FORCE_FACE" not in cal
    assert "IMPR_RESU" in full and "IMPR_RESU" not in cal
    assert "SIEQ_NOEU" in full and "SIEQ_NOEU" not in cal
    assert "EFGE_ELNO" in cal
    # same model: the constraints that set the joint stiffness must survive
    for keep in ("LIAISON_RBE3", "AFFE_CARA_ELEM", "DDL_IMPO"):
        assert keep in cal, keep


def test_calibration_export_asks_only_for_the_bolt_forces(meshed):
    _, out, setup, meta = meshed
    a = setup["analyses"][0]
    _, exp = comm_writer.build_run(a, setup, meta, out["stats"], SolverConfig(),
                                   calibration=True)
    units = [ln for ln in exp.splitlines() if ln.startswith("F libr")]
    assert any("bolt_forces.csv" in u for u in units)
    assert not any("result.med" in u for u in units)


def test_grip_is_the_same_whichever_faces_are_picked(model):
    """The UI accepts either the hole cylinders or the bearing faces under
    head and nut. Both describe the same bolt, so both must give the same
    clamped length — that equivalence is what taking the outer extreme of the
    picked faces buys, and it is why the fix is not just "add the thickness".
    """
    brep, meta, d = model
    s1, s2 = (s["tag"] for s in meta["solids"])
    top_cyls, bot_cyls = hole_cylinders(meta, s2), hole_cylinders(meta, s1)
    zs = [f["com"][2] for f in meta["faces"]]
    zmin, zmax = min(zs), max(zs)
    planes = [f for f in meta["faces"] if (f.get("fit") or {}).get("kind") == "plane"]
    top_face = max((f for f in planes if abs(f["com"][2] - zmax) < 1e-6),
                   key=lambda f: f["area"])
    bot_face = max((f for f in planes if abs(f["com"][2] - zmin) < 1e-6),
                   key=lambda f: f["area"])

    def grip(a_faces, b_faces, name):
        setup = default_setup()
        setup["bolts"] = [{"id": "b1", "name": "B1", "d_mm": 6, "E_GPa": 210,
                           "side_a_faces": a_faces, "side_b_faces": b_faces}]
        out = meshing.mesh_project(brep, str(d / f"{name}.unv"), meta, setup)
        return out["stats"]["bolts"][0]["length"]

    by_hole = grip([top_cyls[0]["tag"]], [bot_cyls[0]["tag"]], "hole")
    by_face = grip([top_face["tag"]], [bot_face["tag"]], "face")
    assert by_hole == pytest.approx(zmax - zmin, abs=0.05)
    assert by_face == pytest.approx(by_hole, abs=0.05)


ALL_TYPES = {
    "static": {},
    "modal": {"n_modes": 6},
    "harmonic": {"f_min": 20, "f_max": 2000, "n_steps": 50, "damping": 0.02,
                 "excitation": "base", "base_dir": [0, 0, 1], "base_g": 1.0},
    "random": {"spec": [[20, 0.01], [2000, 0.007]], "damping": 0.02,
               "n_steps": 60},
    "shock": {"input": "pulse", "pulse": "half_sine", "pulse_g": 20,
              "pulse_ms": 11, "axis": 2, "rule": "srss", "damping": 0.05,
              "n_modes": 6},
}


@pytest.mark.parametrize("atype", sorted(ALL_TYPES))
def test_every_deck_is_valid_python(meshed, atype):
    """A .comm is executed as Python by code_aster, so a syntax error in one
    is a guaranteed failure — and the nearest solver that would catch it is on
    another machine. Parsing every deck here catches the whole class for free.

    It is not a check that code_aster ACCEPTS the deck; only that Python can
    read it. Bad indentation inside an `_optional` block is exactly the kind
    of thing this stops.
    """
    _, out, shared, meta = meshed
    setup = json.loads(json.dumps(shared))
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210,
                           "nu": 0.3, "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    a = setup["analyses"][0]
    a["type"] = atype
    a["config"] = dict(ALL_TYPES[atype])
    if atype in ("harmonic", "random", "shock"):
        assert out["stats"].get("probes"), "fixture must provide a probe"
    comm, _ = comm_writer.build_run(a, setup, meta, out["stats"], SolverConfig())
    ast.parse(comm.replace("DEBUT(LANG='EN')", "pass").replace("FIN()", "pass"))
