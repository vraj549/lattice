"""Verification: solved answers against closed-form ones.

This is the difference between "the deck says what I meant" and "the number is
right", and until this file existed the project only had the first. Every
other test here checks generated input text, a parser, or arithmetic done in
Python. Two cases checked a solved answer — a cantilever's tip deflection and
its first natural frequency — and both were displacement or frequency.

**Stress had never been checked against theory on either engine**, which is
the quantity the tool is mostly used to read.

Each case states its closed-form answer, the tolerance, and why that tolerance
and not a tighter one. A case that cannot run — no solver for that engine on
this machine — is skipped, never silently passed. `docs/VERIFICATION.md`
publishes what was executed and where.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import ccx_writer, geometry, meshing  # noqa: E402
from lattice_fea.config import SolverConfig, find_ccx  # noqa: E402
from lattice_fea.frd_reader import FrdFile  # noqa: E402
from lattice_fea.projects import default_setup  # noqa: E402
from lattice_fea.solver import ccx_env  # noqa: E402

CCX = find_ccx()
needs_ccx = pytest.mark.skipif(not CCX, reason="no CalculiX binary on PATH")

E = 210000.0          # MPa
NU = 0.3
RHO = 7.85e-9         # tonne/mm^3


def _bar(tmp, L, b, h, size):
    """A b x h x L bar, meshed, with the -X end available as `root` and the
    +X end as `tip`."""
    brep = os.path.join(tmp, "bar.brep")
    with geometry.GMSH_LOCK:
        gmsh = geometry._gmsh()
        geometry._fresh_model(gmsh, "bar")
        gmsh.model.occ.addBox(0, 0, 0, L, b, h)
        gmsh.model.occ.synchronize()
        gmsh.write(brep)
        gmsh.clear()
    meta = geometry._analyze_brep(brep)
    root = min(meta["faces"], key=lambda f: f["com"][0])
    tip = max(meta["faces"], key=lambda f: f["com"][0])
    setup = default_setup()
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": E / 1000.0,
                           "nu": NU, "rho_kgm3": RHO * 1e12}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    setup["mesh"]["size_mm"] = size
    return brep, meta, setup, root, tip


def _solve_ccx(tmp, brep, meta, setup):
    out = meshing.mesh_project(brep, os.path.join(tmp, "mesh.unv"), meta, setup)
    stats = out["stats"]
    assert (stats.get("quality_counts") or {}).get("inverted", 0) == 0
    run = os.path.join(tmp, "run")
    os.makedirs(run, exist_ok=True)
    shutil.copyfile(os.path.join(tmp, "mesh.inp"), os.path.join(run, "mesh.inp"))
    with open(os.path.join(run, "job.inp"), "w") as f:
        f.write(ccx_writer.build_deck(setup["analyses"][0], setup, meta, stats))
    p = subprocess.run([CCX, "-i", "job"], cwd=run, capture_output=True,
                       text=True, env=ccx_env(SolverConfig()), timeout=1800)
    assert p.returncode == 0, p.stdout[-2000:]
    return FrdFile(os.path.join(run, "job.frd"))


def _von_mises(comps, arr):
    ix = {c.upper(): i for i, c in enumerate(comps)}
    g = lambda k: arr[:, ix[k]]                                    # noqa: E731
    sx, sy, sz = g("SXX"), g("SYY"), g("SZZ")
    txy, tyz, tzx = g("SXY"), g("SYZ"), g("SZX")
    return np.sqrt(0.5 * ((sx - sy) ** 2 + (sy - sz) ** 2 + (sz - sx) ** 2)
                   + 3.0 * (txy ** 2 + tyz ** 2 + tzx ** 2))


# --------------------------------------------------------------- uniaxial

@needs_ccx
def test_uniaxial_stress_is_exact(tmp_path):
    """A bar in tension: sigma = P/A and delta = PL/(AE), both exact.

    No stress concentration, no singularity, no shear — so this is the one
    case where a discretisation argument cannot excuse an error, and 1 % is a
    real bound rather than a generous one. It is also the cheapest possible
    check that the unit system survives the round trip: N and mm in, MPa out.
    """
    tmp = str(tmp_path)
    L, b, h, P = 100.0, 10.0, 10.0, 5000.0
    brep, meta, setup, root, tip = _bar(tmp, L, b, h, size=4.0)
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Pull", "config": {"engine": "ccx"},
        "supports": [{"id": "s1", "name": "root", "type": "fixed",
                      "faces": [root["tag"]]}],
        "loads": [{"id": "l1", "name": "pull", "type": "force",
                   "faces": [tip["tag"]], "fx": P, "fy": 0, "fz": 0}]}]
    frd = _solve_ccx(tmp, brep, meta, setup)

    A = b * h
    comps, u = frd.field("DISP")
    ux = u[:, [c.upper() for c in comps].index("D1")]
    assert abs(ux.max() - P * L / (A * E)) / (P * L / (A * E)) < 0.01

    comps, sig = frd.field("STRESS")
    vm = _von_mises(comps, sig)
    x = frd.nodes[:, 0]
    # Away from both ends: the clamp restrains Poisson contraction and the
    # loaded face carries the nodal load pattern, so neither is uniaxial.
    mid = (x > 0.3 * L) & (x < 0.7 * L)
    assert mid.sum() > 20
    got = float(np.median(vm[mid]))
    assert abs(got - P / A) / (P / A) < 0.01, f"{got} vs {P / A}"


# ---------------------------------------------------------------- bending

@needs_ccx
def test_bending_stress_matches_beam_theory(tmp_path):
    """Cantilever bending stress at mid-span: sigma = M c / I.

    Sampled at mid-span rather than at the root. The root of a clamped
    cantilever is a stress singularity in a solid model — the value there
    climbs with every refinement and matching it would mean matching a number
    that does not converge.

    5 % because beam theory is itself the approximation here: it ignores the
    shear-induced warping that a solid model captures, and at L/h = 10 that is
    worth a couple of per cent on its own.
    """
    tmp = str(tmp_path)
    L, b, h, P = 100.0, 10.0, 10.0, 1000.0
    brep, meta, setup, root, tip = _bar(tmp, L, b, h, size=2.5)
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Tip load", "config": {"engine": "ccx"},
        "supports": [{"id": "s1", "name": "root", "type": "fixed",
                      "faces": [root["tag"]]}],
        "loads": [{"id": "l1", "name": "tip", "type": "force",
                   "faces": [tip["tag"]], "fx": 0, "fy": 0, "fz": -P}]}]
    frd = _solve_ccx(tmp, brep, meta, setup)

    comps, sig = frd.field("STRESS")
    ix = [c.upper() for c in comps].index("SXX")
    x, z = frd.nodes[:, 0], frd.nodes[:, 2]
    I = b * h ** 3 / 12.0
    xm = 0.5 * L
    band = (np.abs(x - xm) < 2.0) & (z > h - 1e-6)      # top fibre, mid-span
    assert band.sum() >= 4, f"only {band.sum()} nodes sampled"
    got = float(np.median(sig[band, ix]))
    want = (P * (L - xm)) * (h / 2.0) / I
    assert abs(got - want) / abs(want) < 0.05, f"{got} vs {want}"


@needs_ccx
def test_tip_deflection_matches_beam_theory(tmp_path):
    """delta = PL^3/(3EI) + PL/(kAG), the shear term included because at
    L/h = 10 it is about 4 % of the answer and leaving it out would make the
    tolerance hide it."""
    tmp = str(tmp_path)
    L, b, h, P = 100.0, 10.0, 10.0, 1000.0
    brep, meta, setup, root, tip = _bar(tmp, L, b, h, size=2.5)
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Tip load", "config": {"engine": "ccx"},
        "supports": [{"id": "s1", "name": "root", "type": "fixed",
                      "faces": [root["tag"]]}],
        "loads": [{"id": "l1", "name": "tip", "type": "force",
                   "faces": [tip["tag"]], "fx": 0, "fy": 0, "fz": -P}]}]
    frd = _solve_ccx(tmp, brep, meta, setup)
    comps, u = frd.field("DISP")
    uz = float(u[:, [c.upper() for c in comps].index("D3")].min())
    I = b * h ** 3 / 12.0
    G = E / (2 * (1 + NU))
    want = -(P * L ** 3) / (3 * E * I) - (P * L) / (b * h * G / 1.2)
    assert abs(uz - want) / abs(want) < 0.06, f"{uz} vs {want}"


@needs_ccx
def test_first_mode_matches_beam_theory(tmp_path):
    """f1 = (1.875^2 / 2 pi) sqrt(EI / (m L^4)) for a cantilever."""
    tmp = str(tmp_path)
    L, b, h = 100.0, 10.0, 10.0
    brep, meta, setup, root, _tip = _bar(tmp, L, b, h, size=3.0)
    setup["analyses"] = [{
        "id": "a1", "type": "modal", "name": "Modes",
        "config": {"engine": "ccx", "n_modes": 4},
        "supports": [{"id": "s1", "name": "root", "type": "fixed",
                      "faces": [root["tag"]]}],
        "loads": []}]
    frd = _solve_ccx(tmp, brep, meta, setup)
    freqs = frd.frequencies()
    assert len(freqs) >= 2
    I = b * h ** 3 / 12.0
    m = RHO * b * h
    want = (1.875 ** 2 / (2 * math.pi)) * math.sqrt(E * I / (m * L ** 4))
    assert abs(freqs[0] - want) / want < 0.06, f"{freqs[0]} vs {want}"


# ------------------------------------------------------ mesh convergence

@needs_ccx
def test_refining_the_mesh_moves_the_answer_toward_theory(tmp_path):
    """The property that makes a discretisation trustworthy, asserted rather
    than assumed: refine, and the error must fall.

    Without this a tolerance only says the answer is close on one mesh, which
    a compensating pair of errors can also produce.
    """
    L, b, h, P = 100.0, 10.0, 10.0, 1000.0
    I = b * h ** 3 / 12.0
    G = E / (2 * (1 + NU))
    want = -(P * L ** 3) / (3 * E * I) - (P * L) / (b * h * G / 1.2)
    errs = []
    for i, size in enumerate((6.0, 3.0)):
        tmp = str(tmp_path / f"m{i}")
        os.makedirs(tmp, exist_ok=True)
        brep, meta, setup, root, tip = _bar(tmp, L, b, h, size=size)
        setup["analyses"] = [{
            "id": "a1", "type": "static", "name": "Tip", "config": {"engine": "ccx"},
            "supports": [{"id": "s1", "name": "root", "type": "fixed",
                          "faces": [root["tag"]]}],
            "loads": [{"id": "l1", "name": "tip", "type": "force",
                       "faces": [tip["tag"]], "fx": 0, "fy": 0, "fz": -P}]}]
        frd = _solve_ccx(tmp, brep, meta, setup)
        comps, u = frd.field("DISP")
        uz = float(u[:, [c.upper() for c in comps].index("D3")].min())
        errs.append(abs(uz - want) / abs(want))
    assert errs[1] < errs[0], f"refining made it worse: {errs}"


# --------------------------------------------------------------- code_aster
#
# code_aster is the engine that does everything CalculiX cannot: bolt
# pretension, harmonic, random and shock — which is to say, the reason this
# tool exists. None of it had ever been checked against a closed-form answer.
#
# These cases cannot run on macOS: there is no working code_aster build for it
# (docs/INSTALL.md). They are written to run wherever code_aster does, and
# docs/VERIFICATION.md records which rows have actually been executed and on
# what. A test that has never run is a claim, not a verification, and it is
# labelled as one until somebody runs it.

_ASTER = SolverConfig()
try:
    from lattice_fea import config as _cfg
    _ASTER = _cfg.detect(".")
except Exception:  # noqa: BLE001
    pass
needs_aster = pytest.mark.skipif(
    "aster" not in {e["id"] for e in _ASTER.engines()} or _ASTER.is_demo(),
    reason="no code_aster on this machine (see docs/INSTALL.md)")


def _aster_static(tmp, meta, setup):
    """Run one analysis through the same path the app uses, and return the
    parsed results. Deliberately not a shortcut: a benchmark that bypasses the
    writer verifies the solver rather than the tool."""
    from lattice_fea import comm_writer, results
    from lattice_fea.solver import Job, run_solver

    out = meshing.mesh_project(os.path.join(tmp, "bar.brep"),
                               os.path.join(tmp, "mesh.unv"), meta, setup)
    stats = out["stats"]
    run = os.path.join(tmp, "run")
    os.makedirs(run, exist_ok=True)
    comm, export = comm_writer.build_run(setup["analyses"][0], setup, meta,
                                         stats, _ASTER)
    for name, text in (("run.comm", comm), ("run.export", export)):
        with open(os.path.join(run, name), "w", encoding="utf-8") as f:
            f.write(text)
    shutil.copyfile(os.path.join(tmp, "mesh.unv"), os.path.join(run, "mesh.unv"))
    job = Job("solve", "verification")
    rc = run_solver(_ASTER, run, job)
    assert rc == 0, "\n".join(job.log[-40:])
    return results.build_results(run, meta["bbox"], stats.get("geo_volume")), run


@needs_aster
def test_aster_uniaxial_stress_is_exact(tmp_path):
    """The same bar and the same closed-form answer as the CalculiX case:
    sigma = P/A, delta = PL/(AE). Running one problem through both engines is
    the cheapest way to catch a unit-system error in either — a factor of 1000
    somewhere would show up here and nowhere else.
    """
    from lattice_fea.med_reader import MedFile
    tmp = str(tmp_path)
    L, b, h, P = 100.0, 10.0, 10.0, 5000.0
    _brep, meta, setup, root, tip = _bar(tmp, L, b, h, size=4.0)
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Pull", "config": {},
        "supports": [{"id": "s1", "name": "root", "type": "fixed",
                      "faces": [root["tag"]]}],
        "loads": [{"id": "l1", "name": "pull", "type": "force",
                   "faces": [tip["tag"]], "fx": P, "fy": 0, "fz": 0}]}]
    meta_out, run = _aster_static(tmp, meta, setup)

    names = [f["name"] for f in meta_out["fields"]]
    sieq = next(n for n in names if "SIEQ" in n.upper())
    med = MedFile(os.path.join(run, "result.med"))
    try:
        step = meta_out["fields"][names.index(sieq)]["steps"][0]["key"]
        vals = np.asarray(med.read_field(sieq, step))
        comps = [c.strip().upper()
                 for c in next(f for f in med.list_fields()
                               if f["name"] == sieq)["comps"]]
        vm = vals[:, comps.index("VMIS")] if vals.ndim > 1 else vals
        x = med.nodes[:, 0]
    finally:
        med.close()

    # Away from both ends, for the same reason as the CalculiX case: the clamp
    # restrains Poisson contraction and the loaded face carries the nodal load
    # pattern, so neither end is uniaxial.
    mid = (x > 0.3 * L) & (x < 0.7 * L)
    assert mid.sum() > 20, f"only {mid.sum()} nodes in the sample band"
    got = float(np.median(vm[mid]))
    want = P / (b * h)
    assert abs(got - want) / want < 0.02, f"{got} vs {want}"
