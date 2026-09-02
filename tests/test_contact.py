"""Contact: bonded stays linear, sliding goes nonlinear, preload comes first."""
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import comm_writer, geometry, meshing  # noqa: E402
from lattice_fea.config import SolverConfig  # noqa: E402
from lattice_fea.projects import default_setup  # noqa: E402


def base(kind, mu=None, solve=None):
    setup = default_setup()
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210,
                           "nu": 0.3, "rho_kgm3": 7850}]
    setup["assignments"] = {"1": "st", "2": "st"}
    setup["contacts"] = [{"id": "c1", "name": "plate/plate", "kind": kind,
                          "mu": mu, "solids": [1, 2],
                          "faces_a": [3], "faces_b": [11],
                          **({"solve": solve} if solve else {})}]
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Static", "config": {},
        "supports": [{"id": "s1", "name": "fix", "type": "fixed", "faces": [5]}],
        "loads": [{"id": "l1", "name": "pull", "type": "force", "faces": [1],
                   "fx": 0, "fy": 0, "fz": 500}],
    }]
    meta = {"solids": [{"tag": 1}, {"tag": 2}],
            "faces": [{"tag": t, "area": 100.0, "com": [0, 0, 0]}
                      for t in (1, 3, 5, 11)]}
    stats = {"bolts": [], "remotes": [], "probes": [],
             "mesh_format": meshing.MESH_FORMAT,
             "face_groups": ["CTA1", "CTB1", "LOA1_1", "SUP1_1"]}
    return setup, meta, stats


def build(kind, mu=None, bolts=None, solve=None):
    setup, meta, stats = base(kind, mu, solve)
    if bolts:
        setup["bolts"] = bolts
        stats["bolts"] = [{"index": 1, "id": "b1", "name": "b",
                           "end_a": [0, 0, 8], "end_b": [0, 0, 0], "length": 8.0}]
    comm, _ = comm_writer.build_run(setup["analyses"][0], setup, meta, stats,
                                    SolverConfig())
    return comm


def test_bonded_contact_stays_linear():
    """A bonded interface has no status to solve for — a Newton loop would
    cost time and buy nothing."""
    comm = build("bonded")
    assert "MECA_STATIQUE" in comm
    assert "STAT_NON_LINE" not in comm
    assert "DEFI_CONTACT" not in comm
    assert "LIAISON_MAIL" in comm          # glued instead
    assert "GROUP_MA_ESCL=('CTB1',)" in comm


@pytest.mark.parametrize("kind", ["frictionless", "friction", "noseparation"])
def test_sliding_contact_is_nonlinear(kind):
    """Frictionless and no-separation always are. Friction is too when asked
    to be solved rather than checked — see test_linear_friction_* below."""
    comm = build(kind, mu=0.2, solve="nonlinear")
    assert "STAT_NON_LINE" in comm
    assert "MECA_STATIQUE" not in comm
    assert "DEFI_CONTACT" in comm
    assert "FORMULATION='CONTINUE'" in comm
    assert "GROUP_MA_MAIT='CTA1'" in comm and "GROUP_MA_ESCL='CTB1'" in comm
    assert "INCREMENT=_F(LIST_INST=tstep)" in comm


def test_friction_carries_its_coefficient():
    comm = build("friction", mu=0.35, solve="nonlinear")
    assert "FROTTEMENT='COULOMB'" in comm
    assert "COULOMB=0.35" in comm


def test_frictionless_has_no_friction_keyword():
    comm = build("frictionless")
    assert "FROTTEMENT" not in comm


def test_preload_is_applied_before_the_external_load():
    """The bolts must clamp the joint before anything tries to open it.

    Ramping both together lets the interface be pushed apart while the bolt
    is still slack — not the sequence the hardware sees, and a reliable way
    to lose convergence.
    """
    comm = build("friction", mu=0.2, solve="nonlinear", bolts=[{
        "id": "b1", "name": "b", "side_a_faces": [3], "side_b_faces": [11],
        "size": "M6", "d_mm": 6, "as_mm2": 20.1, "E_GPa": 210,
        "preload_N": 8000}])
    assert "PRE_EPSI" in comm
    # preload ramps 0->1 over the first unit of time and then holds
    assert "VALE=(0.0, 0.0, 1.0, 1.0, 2.0, 1.0)" in comm
    # the external load stays at zero until the joint is clamped
    assert "VALE=(0.0, 0.0, 1.0, 0.0, 2.0, 1.0)" in comm
    assert "CHARGE=preload, FONC_MULT=fpre" in comm
    assert "CHARGE=loadc, FONC_MULT=fext" in comm


def test_contact_needs_its_groups_in_the_mesh():
    setup, meta, stats = base("frictionless")
    stats["face_groups"] = ["LOA1_1", "SUP1_1"]        # contact groups missing
    with pytest.raises(ValueError, match="(?i)re-mesh"):
        comm_writer.build_run(setup["analyses"][0], setup, meta, stats,
                              SolverConfig())


def test_contact_pairs_are_found_on_coincident_faces():
    """Two parts stacked face to face: exactly one interface, not four."""
    meta = {
        "diag": 100.0,
        "solids": [{"tag": 1}, {"tag": 2}],
        "faces": [
            {"tag": 1, "area": 400.0, "com": [0, 0, 0], "solids": [1]},
            {"tag": 2, "area": 400.0, "com": [0, 0, 8], "solids": [1]},
            {"tag": 3, "area": 400.0, "com": [0, 0, 8], "solids": [2]},
            {"tag": 4, "area": 400.0, "com": [0, 0, 16], "solids": [2]},
            {"tag": 5, "area": 120.0, "com": [10, 0, 4], "solids": [1]},
        ],
    }
    pairs = geometry.find_contact_pairs(meta)
    assert len(pairs) == 1
    assert sorted(pairs[0]["solids"]) == [1, 2]
    assert pairs[0]["faces_a"] == [2] and pairs[0]["faces_b"] == [3]


BOLT = [{"id": "b1", "name": "Bolt 1", "d_mm": 6, "E_GPa": 210,
         "preload_N": 8000, "side_a_faces": [3], "side_b_faces": [11]}]


def build_kw(kind, bolts=None, solve="nonlinear", **kw):
    setup, meta, stats = base(kind, 0.15, solve)
    if bolts:
        setup["bolts"] = bolts
        stats["bolts"] = [{"index": 1, "id": "b1", "name": "b",
                           "end_a": [0, 0, 8], "end_b": [0, 0, 0], "length": 8.0}]
    comm, exp = comm_writer.build_run(setup["analyses"][0], setup, meta, stats,
                                      SolverConfig(), **kw)
    return comm, exp


def test_calibration_run_is_a_single_ramp():
    """With no external load there is nothing to sequence after tightening.
    The second ramp would apply nothing and cost half the increments."""
    full, _ = build_kw("friction", bolts=BOLT)
    cal, _ = build_kw("friction", bolts=BOLT, calibration=True)
    assert "# ramp stops: 0.0, 1.0, 2.0" in full
    assert "# ramp stops: 0.0, 1.0" in cal
    assert "fpre" in cal and "fext" not in cal
    assert "FONC_MULT=fpre" in cal


def test_calibration_keeps_the_contact_that_sets_the_joint_stiffness():
    """Calibrating against a bonded stand-in would measure the wrong spring."""
    cal, _ = build_kw("friction", bolts=BOLT, calibration=True)
    assert "STAT_NON_LINE" in cal
    assert "DEFI_CONTACT" in cal
    assert "FROTTEMENT='COULOMB'" in cal


def test_load_only_run_is_unchanged_by_the_ramp_rework():
    """The no-preload path still ramps the external load over [0,1]."""
    comm, _ = build_kw("friction")
    assert "# ramp stops: 0.0, 1.0" in comm
    assert "fext" in comm and "fpre" not in comm
    assert "FONC_MULT=fext" in comm


# ------------------------------------------------- friction without a Newton loop

def test_friction_solves_linearly_by_default():
    """A designed friction joint is meant to be STUCK, and a stuck frictional
    interface is the same constraint as a bonded one. So it is glued, solved
    linearly, and the premise is checked afterwards from the tractions."""
    comm = build("friction", mu=0.15)
    assert "MECA_STATIQUE" in comm
    assert "STAT_NON_LINE" not in comm
    assert "DEFI_CONTACT" not in comm
    assert "LIAISON_MAIL" in comm                 # glued for the solve
    assert "GROUP_MA_ESCL=('CTB1',)" in comm
    # and the interface stress that makes the check possible
    assert "NOM_CHAM='SIGM_NOEU'" in comm
    assert "INTITULE='CONTACT1'" in comm
    assert "GROUP_NO='CTA1'" in comm


def test_the_linear_run_asks_for_the_check_data():
    _, exp = build_kw("friction", solve="linear")
    assert any("contact_check.csv" in ln for ln in exp.splitlines())


def test_frictionless_is_never_solved_linearly():
    """There is no no-slip premise to validate — it slides by definition, so
    bonded is a different interface, not a testable stand-in for it."""
    comm = build("frictionless")
    assert "STAT_NON_LINE" in comm
    assert "DEFI_CONTACT" in comm
    assert comm_writer.solves_linearly({"kind": "frictionless"}) is False
    assert comm_writer.solves_linearly({"kind": "noseparation"}) is False


def test_solve_mode_is_a_real_switch():
    lin = build("friction", mu=0.15, solve="linear")
    non = build("friction", mu=0.15, solve="nonlinear")
    assert "MECA_STATIQUE" in lin and "STAT_NON_LINE" not in lin
    assert "STAT_NON_LINE" in non and "MECA_STATIQUE" not in non
    assert "contact_check" not in non.lower()


def test_bonded_owes_no_slip_check():
    """Bonded is an assertion the user made, not a premise Lattice adopted on
    their behalf, so there is nothing to report back."""
    comm = build("bonded")
    assert "INTITULE='CONTACT1'" not in comm
    assert comm_writer.slip_checked(
        [{"kind": "bonded", "index": 1, "ga": "CTA1"}]) == []


def test_a_mixed_model_keeps_each_interface_on_its_own_terms():
    """One frictionless interface drags the solve nonlinear. The checked
    frictional one stays GLUED even so — the premise is the same either way,
    and gluing it keeps its status out of the Newton loop."""
    setup, meta, stats = base("friction", 0.15, "linear")
    setup["contacts"].append({"id": "c2", "name": "b", "kind": "frictionless",
                              "mu": None, "solids": [1, 2],
                              "faces_a": [1], "faces_b": [5]})
    stats["face_groups"] += ["CTA2", "CTB2"]
    cts = comm_writer.active_contacts(setup, stats)
    assert comm_writer.needs_nonlinear(cts) is True
    comm, _ = comm_writer.build_run(setup["analyses"][0], setup, meta, stats,
                                    SolverConfig())
    assert "STAT_NON_LINE" in comm
    assert "GROUP_MA_ESCL=('CTB1',)" in comm            # 1 glued
    assert re.findall(r"GROUP_MA_MAIT='(CTA\d)'", comm) == ["CTA2"]   # 2 solved
    assert re.findall(r"INTITULE='(CONTACT\d)'", comm) == ["CONTACT1"]
