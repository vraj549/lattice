"""Contact: bonded stays linear, sliding goes nonlinear, preload comes first."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import comm_writer, geometry  # noqa: E402
from lattice_fea.config import SolverConfig  # noqa: E402
from lattice_fea.projects import default_setup  # noqa: E402


def base(kind, mu=None):
    setup = default_setup()
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210,
                           "nu": 0.3, "rho_kgm3": 7850}]
    setup["assignments"] = {"1": "st", "2": "st"}
    setup["contacts"] = [{"id": "c1", "name": "plate/plate", "kind": kind,
                          "mu": mu, "solids": [1, 2],
                          "faces_a": [3], "faces_b": [11]}]
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
             "face_groups": ["CTA1", "CTB1", "LOA1_1", "SUP1_1"]}
    return setup, meta, stats


def build(kind, mu=None, bolts=None):
    setup, meta, stats = base(kind, mu)
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
    comm = build(kind, mu=0.2)
    assert "STAT_NON_LINE" in comm
    assert "MECA_STATIQUE" not in comm
    assert "DEFI_CONTACT" in comm
    assert "FORMULATION='CONTINUE'" in comm
    assert "GROUP_MA_MAIT='CTA1'" in comm and "GROUP_MA_ESCL='CTB1'" in comm
    assert "INCREMENT=_F(LIST_INST=tstep)" in comm


def test_friction_carries_its_coefficient():
    comm = build("friction", mu=0.35)
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
    comm = build("friction", mu=0.2, bolts=[{
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
