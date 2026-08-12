"""Bolted-joint pipeline: cylinder detection, beam creation, spiders, preload."""
import math
import os
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
    setup["supports"] = [{"id": "s1", "name": "fix", "type": "fixed",
                          "faces": [faces[0]["tag"]]}]
    setup["loads"] = [{"id": "l1", "name": "pull", "type": "force",
                       "faces": [faces[1]["tag"]], "fx": 0, "fy": 0, "fz": 500}]
    setup["bolts"] = [
        {"id": "b1", "name": "Bolt 1",
         "side_a_faces": [top_cyls[0]["tag"]], "side_b_faces": [bot_cyls[0]["tag"]],
         "d_mm": 6, "E_GPa": 210, "preload_N": 8000},
        {"id": "b2", "name": "Bolt 2",
         "side_a_faces": [top_cyls[1]["tag"]], "side_b_faces": [bot_cyls[1]["tag"]],
         "d_mm": 6, "E_GPa": 210, "preload_N": 8000},
    ]
    unv = str(d / "mesh.unv")
    out = meshing.mesh_project(brep, unv, meta, setup)
    return unv, out, setup, meta


def test_bolt_beams_in_mesh(meshed):
    unv, out, setup, meta = meshed
    stats = out["stats"]
    assert len(stats["bolts"]) == 2
    for br in stats["bolts"]:
        # grip spans the two hole midpoints: plates are 8 mm each -> ~8 mm
        assert 2.0 < br["length"] < 15.0
    txt = open(unv, errors="ignore").read()
    for g in ("BOLT1", "BOLT2", "BFA1", "BFB1", "BFA2", "BFB2"):
        assert g in txt, f"group {g} missing from UNV"


def test_bolted_static_comm(meshed):
    _, out, setup, meta = meshed
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210, "nu": 0.3,
                           "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    comm, export = comm_writer.build_run(
        {"id": "a1", "type": "static", "config": {}},
        setup, meta, out["stats"], SolverConfig())
    for kw in ("MODELISATION='POU_D_T'", "LIAISON_RBE3", "SECTION='CERCLE'",
               "PRE_EPSI", "EFGE_ELNO", "CARA_ELEM=cara", "BN1A", "BN2B",
               "DDL_MAIT=('DX', 'DY', 'DZ', 'DRX', 'DRY', 'DRZ')"):
        assert kw in comm, f"missing {kw}"
    assert "bolt_forces.csv" in export
    # preload strain: EPX = -F/(E*A) for M6: A = pi*9, E = 210000
    eps = -8000.0 / (210000.0 * math.pi * 9.0)
    assert f"EPX={eps!r}"[:14] in comm


def test_bolted_modal_comm(meshed):
    _, out, setup, meta = meshed
    comm, _ = comm_writer.build_run(
        {"id": "a2", "type": "modal", "config": {"n_modes": 8}},
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
    with pytest.raises(ValueError, match="re-mesh"):
        comm_writer.build_run({"id": "a3", "type": "static", "config": {}},
                              setup2, meta, out["stats"], SolverConfig())


def test_tie_comm(meshed):
    _, out, setup, meta = meshed
    s1 = meta["solids"][0]["tag"]
    faces = sorted(meta["faces"], key=lambda f: -f["area"])
    setup2 = {**setup, "ties": [{"id": "t1", "slave_faces": [faces[2]["tag"]],
                                 "master_solid": s1}]}
    # note: TIE group only lands in UNV after re-mesh; here we just check emission
    stats = {**out["stats"], "face_groups": out["stats"]["face_groups"] + ["TIE1"]}
    comm, _ = comm_writer.build_run({"id": "a4", "type": "static", "config": {}},
                                    setup2, meta, stats, SolverConfig())
    assert "LIAISON_MAIL" in comm
    assert f"GROUP_MA_MAIT=('V{s1}',)" in comm
    assert "TYPE_RACCORD='MASSIF'" in comm