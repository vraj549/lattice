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


# ---------------------------------------------------------------- hexahedra

def _mesh_shape(tmp, build, elements="tet", size=4.0):
    """Mesh a shape built in gmsh, through the real mesh_project path."""
    import gmsh as _g
    from lattice_fea.geometry import GMSH_LOCK, _gmsh, _fresh_model
    brep = os.path.join(tmp, "s.brep")
    with GMSH_LOCK:
        g = _gmsh()
        _fresh_model(g, "s")
        build(g)
        g.model.occ.synchronize()
        g.write(brep)
        g.clear()
    meta = geometry.import_step(brep, os.path.join(tmp, "o.brep")) \
        if brep.endswith(".step") else geometry._analyze_brep(brep)
    setup = default_setup()
    setup["mesh"] = {"size_mm": size, "order": 2, "elements": elements}
    notes = []
    out = meshing.mesh_project(brep, os.path.join(tmp, "m.unv"), meta, setup,
                               progress=notes.append)
    return out, notes


def _box(g):
    g.model.occ.addBox(0, 0, 0, 100, 40, 2)


def _lshape(g):
    """An L-SECTION extruded along Y — which is a prism, and does sweep.
    Kept because that is a useful thing to know: 'not a box' is not the same
    as 'not sweepable'."""
    a = g.model.occ.addBox(0, 0, 0, 100, 40, 3)
    b = g.model.occ.addBox(0, 0, 0, 3, 40, 40)
    g.model.occ.fuse([(3, a)], [(3, b)])


def _blind_pocket(g):
    """A pocket that does not go through. The cross-section changes along Z,
    and along X and Y as well, so this is a prism about nothing."""
    b = g.model.occ.addBox(0, 0, 0, 100, 40, 10)
    c = g.model.occ.addCylinder(50, 20, 5, 0, 0, 6, 8)
    g.model.occ.cut([(3, b)], [(3, c)])


def _box_with_hole(g):
    b = g.model.occ.addBox(0, 0, 0, 100, 40, 2)
    c = g.model.occ.addCylinder(50, 20, -1, 0, 0, 4, 5)
    g.model.occ.cut([(3, b)], [(3, c)])


def test_hex_meshing_works_where_the_shape_sweeps(tmp_path):
    """A thin plate is the case hexes are for: tets there are slivers."""
    out, notes = _mesh_shape(str(tmp_path), _box, elements="hex")
    kinds = out["stats"]["element_kinds"]
    assert any(k.startswith("HEX") for k in kinds), kinds
    assert any("hexahedra:" in n for n in notes), notes


def test_a_hole_does_not_stop_the_sweep(tmp_path):
    """The claim this replaces was that a hole defeats hex meshing. It defeats
    `setTransfiniteAutomatic`, which needs a four-sided face — and that is not
    the tool for the job. A swept plate does not care what its cross-section
    looks like, because the section is quad-meshed in 2D where a hole is
    nothing special.
    """
    hexed, notes = _mesh_shape(str(tmp_path), _box_with_hole, elements="hex")
    tets, _ = _mesh_shape(str(tmp_path), _box_with_hole, elements="tet")
    assert all(k.startswith("HEX") for k in hexed["stats"]["element_kinds"])
    assert any("sweeping" in n for n in notes), notes
    # and it is the better mesh, not merely a different one
    assert hexed["stats"]["nodes"] < tets["stats"]["nodes"]
    assert hexed["stats"]["quality_min"] > tets["stats"]["quality_min"]


def test_quadratic_hexes_have_twenty_nodes_not_twenty_seven(tmp_path):
    """HEXA20 / C3D20 is what code_aster and CalculiX both read. The interior
    nodes of a complete HEX27 buy nothing here and CalculiX will not take it."""
    out, _ = _mesh_shape(str(tmp_path), _box_with_hole, elements="hex")
    assert list(out["stats"]["element_kinds"]) == ["HEX20"]


def test_an_l_section_still_sweeps(tmp_path):
    """"Not a box" is not the same as "not sweepable" — an L-section extruded
    along its length is a prism, and a bracket like that hex-meshes fine."""
    out, _ = _mesh_shape(str(tmp_path), _lshape, elements="hex")
    assert all(k.startswith("HEX") for k in out["stats"]["element_kinds"])


def test_a_shape_that_does_not_sweep_falls_back(tmp_path):
    """A blind pocket changes the cross-section along every axis, so there is
    no sweep. Tetrahedra, and the log says why rather than leaving the user to
    notice."""
    out, notes = _mesh_shape(str(tmp_path), _blind_pocket, elements="hex")
    kinds = out["stats"]["element_kinds"]
    assert all(k.startswith("TET") for k in kinds), kinds
    assert any("no sweep" in n for n in notes), notes


def test_the_default_is_still_tetrahedra(tmp_path):
    out, _ = _mesh_shape(str(tmp_path), _box)
    assert all(k.startswith("TET") for k in out["stats"]["element_kinds"])


def test_recombine_does_not_leak_into_the_next_mesh(tmp_path):
    """The gmsh session is shared. A hex attempt that left RecombineAll on
    would put quad faces into every mesh after it — which is exactly how the
    SaveGroupsOfNodes regression happened."""
    _mesh_shape(str(tmp_path), _box, elements="hex")
    out, _ = _mesh_shape(str(tmp_path), _blind_pocket, elements="tet")
    kinds = out["stats"]["element_kinds"]
    assert all(k.startswith("TET") for k in kinds), kinds


@pytest.mark.skipif(not os.path.isfile(os.path.join(
    os.path.dirname(__file__), "..", "examples", "bolted_plates.step")),
    reason="run examples/make_examples.py first")
def test_a_bolted_stack_sweeps_conformally(tmp_path):
    """The case this exists for, end to end.

    Two plates with bolt holes, sharing an interface. Both sweep along Z, and
    the chain is extruded through the shared face so the interface stays ONE
    face — a hex volume next to a tet one cannot share a conformal boundary,
    and gmsh refuses to mesh that at all.

    Everything downstream is keyed to the original tags, so the rebuild has to
    be invisible: the face groups, the bolt beam and the material volumes all
    have to come out the same as they do for tetrahedra.
    """
    tmp = str(tmp_path)
    step = os.path.join(os.path.dirname(__file__), "..", "examples",
                        "bolted_plates.step")
    brep = os.path.join(tmp, "g.brep")
    meta = geometry.import_step(step, brep)
    tess = geometry.tessellate(brep, meta["diag"])
    fits = {f["tag"]: f.get("fit") for f in tess["faces"]}
    for f in meta["faces"]:
        if fits.get(f["tag"]):
            f["fit"] = fits[f["tag"]]

    def holes(solid_tag):
        sd = next(x for x in meta["solids"] if x["tag"] == solid_tag)
        return [f for f in meta["faces"] if f["tag"] in sd["faces"]
                and (f.get("fit") or {}).get("kind") == "cylinder"]

    s1, s2 = (x["tag"] for x in meta["solids"])
    flat = sorted((f for f in meta["faces"]
                   if (f.get("fit") or {}).get("kind") == "plane"),
                  key=lambda f: -f["area"])

    def build(elements):
        setup = default_setup()
        setup["mesh"] = {"size_mm": 4.0, "order": 2, "elements": elements,
                         "local": []}
        setup["materials"] = [{"id": "lib-steel", "name": "Steel", "E_GPa": 210,
                               "nu": 0.3, "rho_kgm3": 7850, "lib": "steel"}]
        setup["assignments"] = {str(x["tag"]): "lib-steel" for x in meta["solids"]}
        setup["bolts"] = [{"id": "b1", "name": "B1", "d_mm": 6, "E_GPa": 210,
                           "preload_N": 8000,
                           "side_a_faces": [holes(s2)[0]["tag"]],
                           "side_b_faces": [holes(s1)[0]["tag"]]}]
        setup["analyses"] = [{
            "id": "a1", "type": "static", "name": "S", "config": {},
            "supports": [{"id": "s1", "name": "fix", "type": "fixed",
                          "faces": [flat[0]["tag"]]}],
            "loads": [{"id": "l1", "name": "pull", "type": "force",
                       "faces": [flat[1]["tag"]], "fx": 0, "fy": 0, "fz": 500}]}]
        notes = []
        unv = os.path.join(tmp, f"{elements}.unv")
        out = meshing.mesh_project(brep, unv, meta, setup, progress=notes.append)
        comm, _ = comm_writer.build_run(setup["analyses"][0], setup, meta,
                                        out["stats"], SolverConfig())
        return out["stats"], notes, open(unv, errors="ignore").read(), comm

    hexed, hnotes, hunv, hcomm = build("hex")
    tets, _, tunv, tcomm = build("tet")

    assert list(hexed["element_kinds"]) == ["HEX20"], hexed["element_kinds"]
    assert any("sweeping 2 solid" in n for n in hnotes), hnotes

    # the rebuild is invisible: same groups, same bolt, same material volumes
    for g in ("BOLT1", "BFA1", "BFB1", "SUP1_1", "LOA1_1"):
        assert g in hunv, f"{g} missing from the swept mesh"
        assert g in tunv
    assert [b["length"] for b in hexed["bolts"]] == pytest.approx(
        [b["length"] for b in tets["bolts"]], abs=0.01)
    assert "'V1'" in hcomm and "'V2'" in hcomm

    # and it is worth doing
    assert hexed["dof"] < 0.6 * tets["dof"]
    assert hexed["quality_min"] > tets["quality_min"]


# ------------------------------------------------------------------- snapping

@pytest.mark.skipif(not os.path.isfile(os.path.join(
    os.path.dirname(__file__), "..", "examples", "bolted_plates.step")),
    reason="run examples/make_examples.py first")
def test_snap_points_are_exact_geometry(tmp_path):
    """Snap targets come from the BREP, not the tessellation.

    A probe placed "on the hole centre" has to BE on it — a tessellated
    approximation is a different point, and being exact is the entire reason
    to snap rather than click.
    """
    step = os.path.join(os.path.dirname(__file__), "..", "examples",
                        "bolted_plates.step")
    meta = geometry.import_step(step, os.path.join(str(tmp_path), "g.brep"))
    sn = meta["snaps"]

    # two M6 holes at x = 25 and 65, y = 25, through a stack from z=0 to z=16,
    # so a circle centre at each hole on each of the three plate faces
    centres = {tuple(round(c, 3) for c in p) for p in sn["centre"]}
    for x in (25.0, 65.0):
        for z in (0.0, 8.0, 16.0):
            assert (x, 25.0, z) in centres, f"no circle centre at {x}, 25, {z}"

    # corners of the stack, exactly
    verts = {tuple(round(c, 3) for c in p) for p in sn["vertex"]}
    for z in (0.0, 8.0, 16.0):
        assert (0.0, 0.0, z) in verts
        assert (90.0, 50.0, z) in verts

    assert sn["mid"], "every curve should offer a midpoint"


def test_snap_points_are_deduplicated(tmp_path):
    """OCC splits a full circle into two half-edges and reports a box corner
    once per adjacent face. Snapping does not care how many times a point was
    found, only where it is."""
    step = os.path.join(os.path.dirname(__file__), "..", "examples",
                        "bolted_plates.step")
    meta = geometry.import_step(step, os.path.join(str(tmp_path), "g.brep"))
    for key, pts in meta["snaps"].items():
        keys = [tuple(round(c, 6) for c in p) for p in pts]
        assert len(keys) == len(set(keys)), f"{key} has duplicates"


def test_circumcentre_is_the_circle_centre():
    import numpy as _np
    c = geometry._circumcentre(_np.array([1.0, 0.0, 5.0]),
                               _np.array([0.0, 1.0, 5.0]),
                               _np.array([-1.0, 0.0, 5.0]))
    assert c == pytest.approx([0.0, 0.0, 5.0], abs=1e-9)
    # collinear points define no circle
    assert geometry._circumcentre(_np.zeros(3), _np.array([1.0, 0, 0]),
                                  _np.array([2.0, 0, 0])) is None


def test_face_groups_are_confirmed_against_the_written_mesh(tmp_path):
    """The stale-mesh guard reads stats["face_groups"], which recorded that
    gmsh had been ASKED to create a physical group. That is not the same as
    the group reaching the file the solver opens, and when the two differed
    the guard passed and code_aster aborted minutes into the run with "le
    GROUP_MA SUP1_1 ne fait pas partie du maillage".

    The names are now read back out of the written UNV, so the guard checks
    the artifact rather than the intent.
    """
    tmp = str(tmp_path)
    brep = os.path.join(tmp, "b.brep")
    with geometry.GMSH_LOCK:
        g = geometry._gmsh()
        geometry._fresh_model(g, "b")
        g.model.occ.addBox(0, 0, 0, 100, 50, 16)
        g.model.occ.synchronize()
        g.write(brep)
        g.clear()
    meta = geometry._analyze_brep(brep)
    faces = sorted(meta["faces"], key=lambda f: f["area"])
    setup = default_setup()
    setup["materials"] = [{"id": "st", "name": "S", "E_GPa": 210, "nu": 0.3,
                           "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    setup["mesh"]["size_mm"] = 8.0
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "S", "config": {},
        "supports": [{"id": "s1", "name": "Sup", "type": "fixed",
                      "faces": [f["tag"] for f in faces[:4]]}],
        "loads": [{"id": "l1", "name": "L", "type": "force",
                   "faces": [faces[4]["tag"]], "fx": 0, "fy": 0, "fz": 100}]}]
    unv = os.path.join(tmp, "mesh.unv")
    stats = meshing.mesh_project(brep, unv, meta, setup)["stats"]

    assert stats["requested_groups"] == ["LOA1_1", "SUP1_1"]
    assert stats["face_groups"] == ["LOA1_1", "SUP1_1"]
    # and every confirmed name really is in the file the solver reads
    names = meshing.unv_group_names(unv)
    assert set(stats["face_groups"]) <= names
    assert "V1" in names, "the volume group should be there too"

    # the reader is honest about a file it cannot make sense of
    empty = os.path.join(tmp, "nothing.unv")
    open(empty, "w").write("not a unv\n")
    assert meshing.unv_group_names(empty) == set()
    assert meshing.unv_group_names(os.path.join(tmp, "missing.unv")) == set()


def test_element_validity_is_checked_separately_from_element_shape():
    """A real run reported four TETRA10 with "le jacobien n'a pas le meme
    signe sur tous les points de Gauss" — tangled elements — while the mesh
    passed the quality gate.

    Shape and validity are different questions. minSICN answers the first: a
    quadratic tetrahedron whose mid-side nodes were pulled onto a curved face
    can score a perfectly reasonable shape and still be turned inside out
    between its Gauss points. minDetJac answers the second, and nothing was
    looking at it.
    """
    from lattice_fea.meshing import quality_counts

    # well shaped and tangled: the case that got through
    got = quality_counts([0.6, 0.7, 0.9], [5.0, -0.2, 4.0])
    assert got["inverted"] == 1, got
    assert got["sliver"] == 0, "it is not a sliver; its shape is fine"

    # a sliver that is still valid is a warning, not a refusal
    got = quality_counts([0.02, 0.7, 0.9], [5.0, 6.0, 4.0])
    assert got["inverted"] == 0 and got["sliver"] == 1, got

    # a clean mesh is clean
    assert quality_counts([0.8, 0.7, 0.9], [5.0, 6.0, 4.0])["inverted"] == 0

    # and where gmsh cannot supply the validity metric, shape alone still
    # catches an inverted element rather than the check disappearing
    assert quality_counts([0.6, -0.1, 0.9])["inverted"] == 1


def test_an_empty_group_record_is_not_the_same_as_no_record(tmp_path):
    """check_mesh_current read `face_groups or []`, which collapsed "this mesh
    predates group recording" into "this mesh recorded nothing". A mesh in
    which no boundary-condition group could be written therefore skipped the
    guard as though it were an old mesh, and the run reached code_aster to
    abort on the first GROUP_MA — the exact case the guard exists for.
    """
    from lattice_fea import comm_writer as CW

    a = {"id": "a1", "type": "modal", "loads": [],
         "supports": [{"id": "s", "name": "Support 1", "faces": [1, 2, 3, 4]}]}

    # no record at all: an old mesh, nothing can be said
    CW.check_mesh_current(a, 1, {"mesh_format": meshing.MESH_FORMAT})
    # recorded and correct
    CW.check_mesh_current(a, 1, {"mesh_format": meshing.MESH_FORMAT,
                                 "face_groups": ["SUP1_1"]})
    # recorded and empty: refused, and says why rather than "re-mesh"
    with pytest.raises(ValueError) as e:
        CW.check_mesh_current(a, 1, {"mesh_format": meshing.MESH_FORMAT,
                                     "face_groups": []})
    assert "no boundary-condition face groups" in str(e.value)
    assert "SUP1_1" in str(e.value)
    # recorded and stale
    with pytest.raises(ValueError) as e:
        CW.check_mesh_current(a, 1, {"mesh_format": meshing.MESH_FORMAT,
                                     "face_groups": ["SUP2_1"]})
    assert "older than" in str(e.value)


def test_curving_to_second_order_untangles_what_it_tangles(tmp_path):
    """Mid-side nodes are projected onto the real geometry, which is what
    makes a quadratic element follow a bore instead of cutting the corner.
    Where the element is large against the curvature that projection can pull
    a mid-node through a face and tangle the element, and gmsh does not undo
    it — so the only lever the user had was to refine the entire model until
    the tangled ones went away, letting a bolt-hole radius dictate the size of
    the whole mesh.
    """
    tmp = str(tmp_path)
    brep = os.path.join(tmp, "p.brep")
    with geometry.GMSH_LOCK:
        g = geometry._gmsh()
        geometry._fresh_model(g, "p")
        box = g.model.occ.addBox(0, 0, 0, 120, 60, 8)
        cuts = [(3, g.model.occ.addCylinder(x, y, -1, 0, 0, 10, 3.3))
                for x in (20, 45, 70, 95) for y in (18, 42)]
        g.model.occ.cut([(3, box)], cuts)
        g.model.occ.synchronize()
        g.write(brep)
        g.clear()
    meta = geometry._analyze_brep(brep)
    setup = default_setup()
    setup["materials"] = [{"id": "st", "name": "S", "E_GPa": 210, "nu": 0.3,
                           "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    setup["analyses"] = [{
        "id": "a1", "type": "modal", "name": "M", "config": {"n_modes": 4},
        "supports": [{"id": "s", "name": "S", "type": "fixed",
                      "faces": [meta["faces"][0]["tag"]]}], "loads": []}]

    counts = {}
    for curv in (16, meshing.DEFAULT_CURVATURE):
        setup["mesh"].update({"size_mm": 6.0, "curvature": curv})
        st = meshing.mesh_project(brep, os.path.join(tmp, f"m{curv}.unv"),
                                  meta, setup)["stats"]
        assert st["quality_counts"]["inverted"] == 0, curv
        counts[curv] = st["nodes"]

    # The default is a real saving on hole-rich geometry, not a rounding of
    # the old one. Measured against the Howland Kt for a hole in tension,
    # peak stress does not improve past about 8 elements around a circle, so
    # the extra mesh 16 builds buys element count and nothing else.
    assert meshing.DEFAULT_CURVATURE == 10
    saved = 1 - counts[10] / counts[16]
    assert saved > 0.4, f"only {saved:.0%} smaller: {counts}"
