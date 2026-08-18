"""CalculiX backend: deck generation, and a solve checked against theory."""
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import ccx_writer, geometry, meshing, results  # noqa: E402
from lattice_fea.config import find_ccx  # noqa: E402
from lattice_fea.frd_reader import FrdFile  # noqa: E402
from lattice_fea.projects import default_setup  # noqa: E402

CCX = find_ccx()
needs_ccx = pytest.mark.skipif(not CCX, reason="no CalculiX binary on PATH")


def _cantilever(tmp, L=100.0, b=10.0, h=10.0, size=3.0):
    """A b x h x L bar, meshed, with the -X end fixed and +X end loaded."""
    brep = os.path.join(tmp, "bar.brep")
    # use the module's own gmsh session; finalizing it here would pull the
    # interpreter out from under meshing.mesh_project below
    with geometry.GMSH_LOCK:
        gmsh = geometry._gmsh()
        geometry._fresh_model(gmsh, "bar")
        gmsh.model.occ.addBox(0, 0, 0, L, b, h)
        gmsh.model.occ.synchronize()
        gmsh.write(brep)
        gmsh.clear()

    meta = geometry._analyze_brep(brep)
    root = min(meta["faces"], key=lambda f: f["com"][0])     # x = 0
    tip = max(meta["faces"], key=lambda f: f["com"][0])      # x = L

    setup = default_setup()
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210.0,
                           "nu": 0.0, "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    setup["mesh"]["size_mm"] = size
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Tip load", "config": {},
        "supports": [{"id": "s1", "name": "root", "type": "fixed",
                      "faces": [root["tag"]]}],
        "loads": [{"id": "l1", "name": "tip", "type": "force",
                   "faces": [tip["tag"]], "fx": 0, "fy": 0, "fz": -1000.0}],
    }]
    out = meshing.mesh_project(brep, os.path.join(tmp, "mesh.unv"), meta, setup)
    return meta, setup, out["stats"]


def test_deck_refuses_what_it_cannot_do():
    """Silence about an unsupported feature is how wrong answers happen."""
    setup = default_setup()
    setup["bolts"] = [{"id": "b", "side_a_faces": [1], "side_b_faces": [2]}]
    a = {"id": "a1", "type": "static"}
    assert "code_aster" in (ccx_writer.unsupported_reason(a, setup) or "")

    setup2 = default_setup()
    assert ccx_writer.unsupported_reason({"id": "a", "type": "harmonic"}, setup2)
    assert ccx_writer.unsupported_reason({"id": "a", "type": "random"}, setup2)
    assert ccx_writer.unsupported_reason({"id": "a", "type": "static"}, setup2) is None


def test_nodal_load_weights_integrate_to_the_face_area(tmp_path):
    """The consistent load vector is the whole basis for applied force in
    CalculiX here, so it must integrate to the true area, not approximately."""
    meta, setup, stats = _cantilever(str(tmp_path))
    w = stats["face_nodes"]["LOA1_1"]
    tip = max(meta["faces"], key=lambda f: f["com"][0])
    assert abs(sum(w.values()) - tip["area"]) / tip["area"] < 1e-9


@needs_ccx
def test_cantilever_matches_beam_theory(tmp_path):
    """End-to-end: mesh -> deck -> ccx -> .frd, against PL^3/(3EI).

    Poisson's ratio is zero so the 3D solution has no anticlastic curvature to
    argue with, and the bar is 10:1 slender, which is where Euler-Bernoulli is
    a fair reference. Shear deflection is added because at 10:1 it is a real
    few percent, not noise.
    """
    tmp = str(tmp_path)
    L, b, h, P, E = 100.0, 10.0, 10.0, 1000.0, 210000.0
    meta, setup, stats = _cantilever(tmp, L, b, h, size=2.5)

    run = os.path.join(tmp, "run")
    os.makedirs(run, exist_ok=True)
    shutil.copyfile(os.path.join(tmp, "mesh.inp"), os.path.join(run, "mesh.inp"))
    deck = ccx_writer.build_deck(setup["analyses"][0], setup, meta, stats)
    with open(os.path.join(run, "job.inp"), "w") as f:
        f.write(deck)

    env = dict(os.environ, OMP_NUM_THREADS="4")
    p = subprocess.run([CCX, "-i", "job"], cwd=run, capture_output=True,
                       text=True, env=env, timeout=900)
    assert p.returncode == 0, p.stdout[-2000:]

    frd = FrdFile(os.path.join(run, "job.frd"))
    comps, u = frd.field("DISP")
    assert comps == ["D1", "D2", "D3"]

    # tip deflection = the mean UZ over the loaded end face
    tipx = frd.nodes[:, 0] > L - 1e-6
    uz = float(np.nanmean(u[tipx, 2]))

    I = b * h ** 3 / 12.0
    A = b * h
    G = E / 2.0                      # nu = 0
    theory = -(P * L ** 3) / (3 * E * I) - (P * L) / (A * G / 1.2)
    assert abs(uz - theory) / abs(theory) < 0.06, f"{uz} vs {theory}"

    meta_out = results.build_results_ccx(run, "job")
    assert any(f["name"] == "DISP" for f in meta_out["fields"])
    assert any(f["name"] == "STRESS" for f in meta_out["fields"])


@needs_ccx
def test_modal_frequencies_match_theory(tmp_path):
    """First bending mode of the same cantilever: f1 = 1.875^2/(2 pi) sqrt(EI/(m L^4))."""
    tmp = str(tmp_path)
    L, b, h, E, rho = 100.0, 10.0, 10.0, 210000.0, 7.85e-9
    meta, setup, stats = _cantilever(tmp, L, b, h, size=3.0)
    setup["analyses"][0].update({"type": "modal", "config": {"n_modes": 4},
                                 "loads": []})

    run = os.path.join(tmp, "run")
    os.makedirs(run, exist_ok=True)
    shutil.copyfile(os.path.join(tmp, "mesh.inp"), os.path.join(run, "mesh.inp"))
    with open(os.path.join(run, "job.inp"), "w") as f:
        f.write(ccx_writer.build_deck(setup["analyses"][0], setup, meta, stats))

    env = dict(os.environ, OMP_NUM_THREADS="4")
    p = subprocess.run([CCX, "-i", "job"], cwd=run, capture_output=True,
                       text=True, env=env, timeout=900)
    assert p.returncode == 0, p.stdout[-2000:]

    freqs = FrdFile(os.path.join(run, "job.frd")).frequencies()
    assert len(freqs) >= 2
    I = b * h ** 3 / 12.0
    m = rho * b * h                       # mass per unit length
    f1 = (1.875 ** 2 / (2 * np.pi)) * np.sqrt(E * I / (m * L ** 4))
    assert abs(freqs[0] - f1) / f1 < 0.06, f"{freqs[0]} vs {f1}"


@needs_ccx
def test_frictional_contact_solves(tmp_path):
    """Two separate plates with a frictional interface, end to end.

    This is the case the whole contact feature exists for: with a bonded
    interface the parts cannot slip or separate, so a bolt preload has nothing
    to clamp and slip cannot be assessed at all.
    """
    tmp = str(tmp_path)
    step = os.path.join(os.path.dirname(__file__), "..", "examples",
                        "bolted_plates.step")
    if not os.path.isfile(step):
        pytest.skip("run examples/make_examples.py first")

    brep = os.path.join(tmp, "g.brep")
    meta = geometry.import_step(step, brep, fragment=False)
    assert meta["fragmented"] is False
    pairs = meta["contact_pairs"]
    assert len(pairs) == 1, "the two plates share exactly one interface"

    setup = default_setup()
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210,
                           "nu": 0.3, "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    setup["mesh"]["size_mm"] = 7
    setup["contacts"] = [{"id": "c1", "name": "plate/plate", "kind": "friction",
                          "mu": 0.2, **pairs[0]}]
    bot = min(meta["faces"], key=lambda f: f["com"][2])
    top = max(meta["faces"], key=lambda f: f["com"][2])
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Clamp", "config": {"engine": "ccx"},
        "supports": [{"id": "s1", "name": "fix", "type": "fixed", "faces": [bot["tag"]]}],
        "loads": [{"id": "l1", "name": "press", "type": "force",
                   "faces": [top["tag"]], "fx": 0, "fy": 0, "fz": -5000}],
    }]
    out = meshing.mesh_project(brep, os.path.join(tmp, "mesh.unv"), meta, setup)
    stats = out["stats"]
    # separate parts really are separate until contact joins them
    assert stats["islands"] == 2
    assert stats["face_elems"]["CTA1"], "master side needs element faces"

    run = os.path.join(tmp, "run")
    os.makedirs(run, exist_ok=True)
    shutil.copyfile(os.path.join(tmp, "mesh.inp"), os.path.join(run, "mesh.inp"))
    deck = ccx_writer.build_deck(setup["analyses"][0], setup, meta, stats)
    assert "*FRICTION" in deck and "TYPE=NODE TO SURFACE" in deck
    with open(os.path.join(run, "job.inp"), "w") as f:
        f.write(deck)

    env = dict(os.environ, OMP_NUM_THREADS="4")
    p = subprocess.run([CCX, "-i", "job"], cwd=run, capture_output=True,
                       text=True, env=env, timeout=1800)
    assert p.returncode == 0, p.stdout[-2000:]
    comps, u = FrdFile(os.path.join(run, "job.frd")).field("DISP")
    assert np.isfinite(u).any()
