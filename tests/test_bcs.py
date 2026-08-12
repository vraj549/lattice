"""Industry-standard BC set: frictionless support, remote force/moment,
rotational velocity."""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import comm_writer, geometry, meshing  # noqa: E402
from lattice_fea.config import SolverConfig  # noqa: E402
from lattice_fea.projects import default_setup  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
BRACKET = os.path.join(EXAMPLES, "bracket.step")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(BRACKET),
    reason="run examples/make_examples.py first")


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    d = tmp_path_factory.mktemp("bcs")
    brep = str(d / "geometry.brep")
    meta = geometry.import_step(BRACKET, brep)
    return brep, meta, d


def _base_setup(meta):
    setup = default_setup()
    faces = sorted(meta["faces"], key=lambda f: -f["area"])
    setup["materials"] = [{"id": "al", "name": "Al", "E_GPa": 68.9, "nu": 0.33,
                           "rho_kgm3": 2700}]
    setup["assignments"] = {str(s["tag"]): "al" for s in meta["solids"]}
    setup["supports"] = [
        {"id": "s1", "name": "fix", "type": "fixed", "faces": [faces[0]["tag"]]},
        {"id": "s2", "name": "sym", "type": "frictionless", "faces": [faces[2]["tag"]]},
    ]
    return setup, faces


@pytest.fixture(scope="module")
def meshed(model):
    brep, meta, d = model
    setup, faces = _base_setup(meta)
    setup["loads"] = [
        {"id": "l1", "name": "remote", "type": "remote", "faces": [faces[1]["tag"]],
         "x": 200.0, "y": 50.0, "z": 6.5,
         "fx": 0, "fy": 0, "fz": -500, "mx": 0, "my": 20000, "mz": 0},
        {"id": "l2", "name": "spin", "type": "rotation",
         "rpm": 3000, "axis": [0, 0, 1], "center": [0, 0, 0]},
    ]
    unv = str(d / "mesh.unv")
    out = meshing.mesh_project(brep, unv, meta, setup)
    return unv, out, setup, meta


def test_remote_stub_in_mesh(meshed):
    unv, out, setup, meta = meshed
    assert len(out["stats"]["remotes"]) == 1
    assert out["stats"]["remotes"][0]["node"] == [200.0, 50.0, 6.5]
    txt = open(unv, errors="ignore").read()
    assert "RPT1" in txt
    assert "LOA1" in txt


def test_static_comm_new_bcs(meshed):
    _, out, setup, meta = meshed
    comm, _ = comm_writer.build_run(
        {"id": "a1", "type": "static", "config": {}},
        setup, meta, out["stats"], SolverConfig())
    # frictionless support
    assert "FACE_IMPO=(" in comm
    assert "DNOR=0.0" in comm
    # remote force + moment at the RBE3 master
    assert "GROUP_NO_MAIT='RPN1'" in comm
    assert "FORCE_NODALE=(" in comm
    assert "FZ=-500.0" in comm and "MY=20000.0" in comm
    # rotation: 3000 rpm -> rad/s
    omega = 3000 * 2 * math.pi / 60
    assert f"VITESSE={omega!r}"[:16] in comm
    assert "AXE=(0.0, 1.0)" not in comm  # axis normalized as 3 components
    assert "MODELISATION='POU_D_T'" in comm  # stub beam modeled
    assert "RPT1" in comm


def test_harmonic_excludes_rotation(meshed):
    _, out, setup, meta = meshed
    setup2 = {**setup, "probes": []}
    mesh_stats = {**out["stats"], "probes": [
        {"id": "p", "name": "p", "x": 0, "y": 0, "z": 0,
         "node_xyz": [0, 0, 0], "snap_dist": 0.001}]}
    comm, _ = comm_writer.build_run(
        {"id": "a2", "type": "harmonic",
         "config": {"f_min": 20, "f_max": 500, "n_steps": 20, "damping": 0.02}},
        setup2, meta, mesh_stats, SolverConfig())
    assert "ROTATION" not in comm, "rotation must not excite a harmonic sweep"
    assert "FORCE_NODALE" in comm, "remote load should excite the sweep"


def test_remote_stale_mesh_guard(meshed):
    _, out, setup, meta = meshed
    setup2 = {**setup, "loads": setup["loads"] + [
        {"id": "l3", "name": "late", "type": "remote", "faces": [1],
         "x": 0, "y": 0, "z": 0, "fx": 1}]}
    with pytest.raises(ValueError, match="re-mesh"):
        comm_writer.build_run({"id": "a3", "type": "static", "config": {}},
                              setup2, meta, out["stats"], SolverConfig())


def test_frictionless_only_support_ok(model):
    """A model constrained only by frictionless planes + one fixed face must
    still emit; pure-frictionless without any fixed is allowed (user's call)."""
    brep, meta, d = model
    setup, faces = _base_setup(meta)
    setup["supports"] = [{"id": "s1", "name": "sym", "type": "frictionless",
                          "faces": [faces[0]["tag"]]}]
    setup["loads"] = [{"id": "l1", "name": "p", "type": "pressure",
                       "faces": [faces[1]["tag"]], "pressure": 1.0}]
    unv = str(d / "mesh2.unv")
    out = meshing.mesh_project(brep, unv, meta, setup)
    comm, _ = comm_writer.build_run({"id": "a4", "type": "static", "config": {}},
                                    setup, meta, out["stats"], SolverConfig())
    assert "DNOR=0.0" in comm
    assert "DDL_IMPO" not in comm
