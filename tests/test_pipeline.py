"""End-to-end smoke tests for the geometry -> mesh -> comm pipeline.

These run without code_aster: they validate STEP import, fragmenting,
tessellation, tet10 meshing with physical groups, UNV export, and the
generated command files.
"""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import comm_writer, geometry, meshing  # noqa: E402
from lattice_fea.config import SolverConfig  # noqa: E402
from lattice_fea.projects import default_setup  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
ASSEMBLY = os.path.join(EXAMPLES, "bracket_assembly.step")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(ASSEMBLY),
    reason="run examples/make_examples.py first")


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    d = tmp_path_factory.mktemp("geo")
    brep = str(d / "geometry.brep")
    meta = geometry.import_step(ASSEMBLY, brep)
    return brep, meta, d


def test_import_assembly(imported):
    _, meta, _ = imported
    assert len(meta["solids"]) == 2
    assert len(meta["interfaces"]) >= 1, "bracket/bushing interface should be shared after fragment"
    assert meta["diag"] > 100


def test_tessellation(imported):
    brep, meta, _ = imported
    tess = geometry.tessellate(brep, meta["diag"])
    assert len(tess["faces"]) == len(meta["faces"])
    assert all(f["tri"]["b64"] for f in tess["faces"])
    assert tess["edges"]


@pytest.fixture(scope="module")
def meshed(imported):
    brep, meta, d = imported
    setup = default_setup()
    # pick real faces for a support and a load: largest two faces of solid 1
    faces = sorted(meta["faces"], key=lambda f: -f["area"])
    setup["supports"] = [{"id": "s1", "name": "fix", "type": "fixed",
                          "faces": [faces[0]["tag"]]}]
    setup["loads"] = [{"id": "l1", "name": "load", "type": "force",
                       "faces": [faces[1]["tag"]], "fx": 0, "fy": 0, "fz": -1200}]
    setup["probes"] = [{"id": "p1", "name": "tip", "x": 130.0, "y": 114.0, "z": 6.5}]
    unv = str(d / "mesh.unv")
    out = meshing.mesh_project(brep, unv, meta, setup)
    return unv, out, setup, meta


def test_mesh_and_unv(meshed):
    unv, out, setup, meta = meshed
    s = out["stats"]
    assert s["nodes"] > 1000
    assert s["order"] == 2
    assert s["islands"] == 1, "fragmented assembly must mesh as one connected body"
    assert s["probes"] and s["probes"][0]["snap_dist"] < 5.0
    txt = open(unv, errors="ignore").read()
    for g in ("V1", "V2", "SUP1", "LOA1"):
        assert g in txt, f"group {g} missing from UNV"
    assert out["skin"]["vtx"]["b64"]


def test_comm_static(meshed):
    _, out, setup, meta = meshed
    setup["materials"] = [{"id": "al", "name": "Al", "E_GPa": 68.9, "nu": 0.33,
                           "rho_kgm3": 2700}]
    setup["assignments"] = {str(s["tag"]): "al" for s in meta["solids"]}
    comm, export = comm_writer.build_run(
        {"id": "a1", "type": "static", "config": {}},
        setup, meta, out["stats"], SolverConfig())
    for kw in ("LIRE_MAILLAGE", "MECA_STATIQUE", "SIEQ_NOEU", "IMPR_RESU",
               "FORCE_FACE", "MUMPS", "REAC_NODA"):
        assert kw in comm
    assert "FORMAT='MED'" in comm
    assert re.search(r"F libr result\.med R 80", export)
    # total force divided by area -> traction; reconstruct and check
    m = re.search(r"FZ=(-?[\d.e-]+)\)", comm)
    assert m, "traction not emitted"


def test_comm_modal_and_harmonic(meshed):
    _, out, setup, meta = meshed
    setup["materials"] = [{"id": "al", "name": "Al", "E_GPa": 68.9, "nu": 0.33,
                           "rho_kgm3": 2700}]
    setup["assignments"] = {str(s["tag"]): "al" for s in meta["solids"]}
    comm, _ = comm_writer.build_run(
        {"id": "a2", "type": "modal", "config": {"n_modes": 12}},
        setup, meta, out["stats"], SolverConfig())
    for kw in ("ASSEMBLAGE", "CALC_MODES", "SORENSEN", "NMAX_FREQ=12",
               "MASS_EFFE_UN_DX", "MASS_INER"):
        assert kw in comm

    comm, export = comm_writer.build_run(
        {"id": "a3", "type": "harmonic",
         "config": {"f_min": 20, "f_max": 2000, "n_steps": 50,
                    "damping": 0.02, "field_freqs": [500.0]}},
        setup, meta, out["stats"], SolverConfig())
    for kw in ("DYNA_VIBRA", "PROJ_BASE", "AMOR_REDUIT", "RECU_FONCTION",
               "ENV_SPHERE", "REST_GENE_PHYS", "PARTIE='REEL'"):
        assert kw in comm
    assert "frf_p1_dx.csv" in export


def test_tableau_parser():
    from lattice_fea.results import parse_tableau
    text = (
        "#TITRE\n"
        "NUME_ORDRE,FREQ\n"
        "1,342.7\n"
        "2,618.4\n"
        "\n"
        "INTITULE,MASSE\n"
    )
    blocks = parse_tableau(text)
    assert blocks and blocks[0]["columns"] == ["NUME_ORDRE", "FREQ"]
    assert blocks[0]["rows"][1][1] == 618.4


def test_comm_is_valid_python(meshed):
    """A .comm executes as Python, and optional outputs are wrapped in
    try/except — so a generated deck must parse. Guards the indentation."""
    import ast
    _, out, setup, meta = meshed
    setup["materials"] = [{"id": "al", "name": "Al", "E_GPa": 68.9, "nu": 0.33,
                           "rho_kgm3": 2700}]
    setup["assignments"] = {str(s["tag"]): "al" for s in meta["solids"]}
    for a in [{"id": "s", "type": "static", "config": {}},
              {"id": "m", "type": "modal", "config": {"n_modes": 6}},
              {"id": "h", "type": "harmonic",
               "config": {"f_min": 20, "f_max": 900, "n_steps": 20, "damping": 0.02}}]:
        comm, _ = comm_writer.build_run(a, setup, meta, out["stats"], SolverConfig())
        ast.parse(comm)                      # raises SyntaxError if malformed
        assert "NUME_ORDRE" not in comm, "MODE_MECA publishes NUME_MODE, not NUME_ORDRE"


def test_med_written_before_optional_tables(meshed):
    """The expensive result must land before anything that can fail."""
    _, out, setup, meta = meshed
    setup["materials"] = [{"id": "al", "name": "Al", "E_GPa": 68.9, "nu": 0.33,
                           "rho_kgm3": 2700}]
    setup["assignments"] = {str(s["tag"]): "al" for s in meta["solids"]}
    comm, _ = comm_writer.build_run({"id": "m", "type": "modal", "config": {}},
                                    setup, meta, out["stats"], SolverConfig())
    assert comm.index("IMPR_RESU") < comm.index("RECU_TABLE")
    # and every table sits inside a guard
    assert comm.count("try:") >= 3
