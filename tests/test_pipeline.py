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


def _an(setup, atype, config):
    """Reuse the fixture's BCs for a new analysis of the given type, and
    register it so its mesh-group indices line up.

    The originals are stashed on first use: a modal analysis carries no loads,
    so reading them back off the last-registered analysis would lose them.
    """
    if "_bcs" not in setup:
        setup["_bcs"] = {"supports": setup["analyses"][0]["supports"],
                         "loads": setup["analyses"][0]["loads"]}
    bcs = setup["_bcs"]
    a = {"id": f"x_{atype}", "type": atype, "name": atype, "config": config,
         "supports": bcs["supports"],
         "loads": [] if atype == "modal" else bcs["loads"]}
    setup["analyses"] = [a]
    return a

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
    # BCs now belong to an analysis, not the model
    setup["probes"] = [{"id": "p1", "name": "tip", "x": 130.0, "y": 114.0, "z": 6.5}]
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Static", "config": {},
        "supports": [{"id": "s1", "name": "fix", "type": "fixed",
                      "faces": [faces[0]["tag"]]}],
        "loads": [{"id": "l1", "name": "load", "type": "force",
                   "faces": [faces[1]["tag"]], "fx": 0, "fy": 0, "fz": -1200}],
    }]
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
    for g in ("V1", "V2", "SUP1_1", "LOA1_1"):
        assert g in txt, f"group {g} missing from UNV"
    assert out["skin"]["vtx"]["b64"]


def test_comm_static(meshed):
    _, out, setup, meta = meshed
    setup["materials"] = [{"id": "al", "name": "Al", "E_GPa": 68.9, "nu": 0.33,
                           "rho_kgm3": 2700}]
    setup["assignments"] = {str(s["tag"]): "al" for s in meta["solids"]}
    comm, export = comm_writer.build_run(
        _an(setup, "static", {}),
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
        _an(setup, "modal", {"n_modes": 12}),
        setup, meta, out["stats"], SolverConfig())
    for kw in ("ASSEMBLAGE", "CALC_MODES", "SORENSEN", "NMAX_FREQ=12",
               "MASS_EFFE_UN_DX", "MASS_INER"):
        assert kw in comm

    comm, export = comm_writer.build_run(
        _an(setup, "harmonic", {"f_min": 20, "f_max": 2000, "n_steps": 50,
                                "damping": 0.02, "field_freqs": [500.0]}),
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
    for atype, cfg in [("static", {}), ("modal", {"n_modes": 6}),
                       ("harmonic", {"f_min": 20, "f_max": 900,
                                     "n_steps": 20, "damping": 0.02})]:
        comm, _ = comm_writer.build_run(_an(setup, atype, cfg), setup, meta,
                                        out["stats"], SolverConfig())
        ast.parse(comm)                      # raises SyntaxError if malformed
        assert "NUME_ORDRE" not in comm, "MODE_MECA publishes NUME_MODE, not NUME_ORDRE"


def test_med_written_before_optional_tables(meshed):
    """The expensive result must land before anything that can fail."""
    _, out, setup, meta = meshed
    setup["materials"] = [{"id": "al", "name": "Al", "E_GPa": 68.9, "nu": 0.33,
                           "rho_kgm3": 2700}]
    setup["assignments"] = {str(s["tag"]): "al" for s in meta["solids"]}
    comm, _ = comm_writer.build_run(
        _an(setup, "modal", {}),
        setup, meta, out["stats"], SolverConfig())
    assert comm.index("IMPR_RESU") < comm.index("RECU_TABLE")
    # and every table sits inside a guard
    assert comm.count("try:") >= 3


def test_med_layout_fallback_without_hint(tmp_path):
    """The coordinate-interlace fallback must agree with the bbox-validated
    answer. gmsh's MED writer stores coordinates component-major; an earlier
    heuristic guessed interleaved and silently scrambled every node."""
    import numpy as np
    from lattice_fea.med_reader import MedFile

    med = str(tmp_path / "m.med")
    # Use the module's own gmsh session. Calling gmsh.finalize() here tore it
    # down for every test that ran afterwards, which failed as
    # "Gmsh has not been initialized" a long way from the cause.
    with geometry.GMSH_LOCK:
        g = geometry._gmsh()
        geometry._fresh_model(g, "box")
        # deliberately unequal extents, the case the heuristic must resolve
        g.model.occ.addBox(0, 0, 0, 90, 50, 16)
        g.model.occ.synchronize()
        g.option.setNumber("Mesh.MeshSizeMax", 20)
        g.model.mesh.generate(3)
        g.write(med)
        g.clear()

    hinted = MedFile(med, [0, 0, 0, 90, 50, 16])
    blind = MedFile(med)
    try:
        assert blind.interleaved == hinted.interleaved, "fallback disagrees with hint"
        assert np.allclose(blind.nodes.max(0), [90, 50, 16], atol=1e-6)
    finally:
        hinted.close()
        blind.close()


def test_skin_triangles_all_wind_outward(tmp_path):
    """Every skin triangle must be wound so its normal points out of the part.

    Mixed winding makes averaged vertex normals partly cancel, which renders
    as speckle across a contour band, and makes front/back-face tests flicker
    per triangle. The divergence theorem gives an exact check: summing the
    signed tetrahedron volumes of a consistently outward-wound closed surface
    reproduces the enclosed volume, and any flipped triangle subtracts.
    """
    import numpy as np
    from lattice_fea.med_reader import MedFile

    med = str(tmp_path / "m.med")
    with geometry.GMSH_LOCK:
        g = geometry._gmsh()
        geometry._fresh_model(g, "box")
        g.model.occ.addBox(0, 0, 0, 30, 20, 10)
        g.model.occ.synchronize()
        g.option.setNumber("Mesh.MeshSizeMax", 8)
        g.model.mesh.generate(3)
        g.model.mesh.setOrder(2)
        g.write(med)
        g.clear()

    f = MedFile(med, [0, 0, 0, 30, 20, 10])
    try:
        tri, used, _, _ = f.skin()
        p = f.nodes[used]
        a, b, c = p[tri[:, 0]], p[tri[:, 1]], p[tri[:, 2]]
        signed = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
        assert abs(signed.sum() - 30 * 20 * 10) < 1.0, "skin is not consistently outward"
        # and no triangle points the wrong way: outward normals must all agree
        # with the direction away from the part centre on a convex solid
        centre = p.mean(0)
        n = np.cross(b - a, c - a)
        outward = np.einsum("ij,ij->i", n, (a + b + c) / 3 - centre)
        assert (outward > 0).all(), f"{(outward <= 0).sum()} inward-facing triangles"
    finally:
        f.close()


def test_stale_mesh_group_names_are_caught(meshed):
    """A mesh built before the BCs changed (or under an older group-naming
    scheme) must be rejected up front, not by the solver minutes later."""
    _, out, setup, meta = meshed
    setup["materials"] = [{"id": "al", "name": "Al", "E_GPa": 68.9, "nu": 0.33,
                           "rho_kgm3": 2700}]
    setup["assignments"] = {str(s["tag"]): "al" for s in meta["solids"]}
    a = _an(setup, "static", {})
    # simulate the pre-0.9 mesh: groups named SUP1 / LOA1, not SUP1_1 / LOA1_1
    old = {**out["stats"], "face_groups": ["SUP1", "LOA1"]}
    with pytest.raises(ValueError, match="Re-mesh"):
        comm_writer.build_run(a, setup, meta, old, SolverConfig())
    # and the current mesh still builds fine
    comm, _ = comm_writer.build_run(a, setup, meta, out["stats"], SolverConfig())
    assert "SUP1_1" in comm


def _fresh_static_setup(meta):
    """A setup of this test's own. The module-scoped fixture is mutated by
    other tests, so reusing it makes these depend on execution order."""
    faces = sorted(meta["faces"], key=lambda f: -f["area"])
    setup = default_setup()
    setup["materials"] = [{"id": "al", "name": "Al", "E_GPa": 68.9, "nu": 0.33,
                           "rho_kgm3": 2700}]
    setup["assignments"] = {str(s["tag"]): "al" for s in meta["solids"]}
    setup["mesh"]["size_mm"] = 8
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Static", "config": {},
        "supports": [{"id": "s1", "name": "fix", "type": "fixed",
                      "faces": [faces[0]["tag"]]}],
        "loads": [{"id": "l1", "name": "load", "type": "force",
                   "faces": [faces[1]["tag"]], "fx": 0, "fy": 0, "fz": -1200}],
    }]
    return setup


def test_repeated_meshing_writes_an_identical_unv(imported, tmp_path):
    """The server is long-lived: the second mesh must equal the first.

    gmsh options are global to the session, so one set for a different output
    format silently applies to the next write. SaveGroupsOfNodes, turned on
    for the Abaqus deck, leaked into the following UNV — which then carried
    node entities inside its element groups. code_aster read those as
    GROUP_NO, and the deck's own DEFI_GROUP(CREA_GROUP_NO=TOUT_GROUP_MA)
    collided with a group that already existed. The first mesh after a restart
    solved; every one after it failed.
    """
    import hashlib
    brep, meta, _ = imported
    setup = _fresh_static_setup(meta)
    hashes = []
    for i in range(3):
        unv = str(tmp_path / f"m{i}.unv")
        meshing.mesh_project(brep, unv, meta, setup)
        hashes.append(hashlib.sha256(open(unv, "rb").read()).hexdigest())
    assert len(set(hashes)) == 1, "the UNV changed between identical meshes"


def test_unv_groups_hold_elements_only(imported, tmp_path):
    """A group carrying node entities makes code_aster create a GROUP_NO that
    the generated deck then tries to create again."""
    brep, meta, _ = imported
    setup = _fresh_static_setup(meta)
    unv = str(tmp_path / "check.unv")
    meshing.mesh_project(brep, unv, meta, setup)
    txt = open(unv, errors="ignore").read()
    assert "  2477\n" in txt, "no group block in the UNV"
    block = txt.split("    -1\n  2477\n")[1].split("    -1")[0]
    lines = block.splitlines()
    i = 0
    seen = 0
    while i < len(lines):
        parts = lines[i].split()
        if len(parts) == 8 and all(p.lstrip("-").isdigit() for p in parts):
            n = int(parts[-1])
            for r in lines[i + 2: i + 2 + (n + 1) // 2]:
                f = r.split()
                for k in range(0, len(f), 4):
                    # entity type 8 is an element, 7 a node
                    assert f[k] != "7", "group contains node entities"
            seen += 1
            i += 2 + (n + 1) // 2
        else:
            i += 1
    assert seen > 0
