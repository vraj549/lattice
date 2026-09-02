"""The linear friction check: tractions, margins, and the premise it tests."""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import slip  # noqa: E402


def tensor(xx=0, yy=0, zz=0, xy=0, xz=0, yz=0):
    return [[xx, yy, zz, xy, xz, yz]]


Z = [0.0, 0.0, 1.0]


def test_pure_compression_is_all_pressure_no_shear():
    p, t = slip.tractions(tensor(zz=-50.0), Z)
    assert p[0] == pytest.approx(50.0)      # compression is positive
    assert t[0] == pytest.approx(0.0)


def test_pure_tension_is_negative_pressure():
    p, _ = slip.tractions(tensor(zz=30.0), Z)
    assert p[0] == pytest.approx(-30.0)


def test_in_plane_stress_crosses_no_interface():
    """SIXX on a z-normal face is carried IN the plane, not across it."""
    p, t = slip.tractions(tensor(xx=100.0, yy=-40.0, xy=25.0), Z)
    assert p[0] == pytest.approx(0.0)
    assert t[0] == pytest.approx(0.0)


def test_transverse_shear_is_all_shear():
    p, t = slip.tractions(tensor(xz=12.0, yz=5.0), Z)
    assert p[0] == pytest.approx(0.0)
    assert t[0] == pytest.approx(13.0)      # 5-12-13


def test_tractions_are_frame_independent():
    """Rotating the tensor and the normal together must change nothing —
    the check has to give the same answer whatever the interface's
    orientation, and this is the only thing that guarantees it."""
    s = np.array([[-80.0, 10.0, -25.0, 7.0, 14.0, -3.0]])
    p0, t0 = slip.tractions(s, Z)
    for axis, ang in (([1, 0, 0], 0.7), ([0, 1, 0], -1.1), ([1, 1, 1], 2.0)):
        a = np.asarray(axis, float) / np.linalg.norm(axis)
        K = np.array([[0, -a[2], a[1]], [a[2], 0, -a[0]], [-a[1], a[0], 0]])
        R = np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * (K @ K)
        xx, yy, zz, xy, xz, yz = s[0]
        S = np.array([[xx, xy, xz], [xy, yy, yz], [xz, yz, zz]])
        Sr = R @ S @ R.T
        rot = [[Sr[0, 0], Sr[1, 1], Sr[2, 2], Sr[0, 1], Sr[0, 2], Sr[1, 2]]]
        p1, t1 = slip.tractions(rot, R @ np.array(Z))
        assert p1[0] == pytest.approx(p0[0], rel=1e-9, abs=1e-9)
        assert t1[0] == pytest.approx(t0[0], rel=1e-9, abs=1e-9)


def test_normal_is_normalised_not_assumed_unit():
    p, _ = slip.tractions(tensor(zz=-50.0), [0.0, 0.0, 7.0])
    assert p[0] == pytest.approx(50.0)


def test_sign_of_the_normal_does_not_matter():
    """Either face of the interface may supply it."""
    a = slip.tractions(tensor(zz=-50.0, xz=10.0), [0, 0, 1])
    b = slip.tractions(tensor(zz=-50.0, xz=10.0), [0, 0, -1])
    assert a[0][0] == pytest.approx(b[0][0])
    assert a[1][0] == pytest.approx(b[1][0])


# ------------------------------------------------------------------ margins

def test_margin_is_the_coulomb_ratio():
    m = slip.margins([100.0], [10.0], 0.15)
    assert m[0] == pytest.approx(0.15 * 100.0 / 10.0)


def test_no_shear_is_infinitely_stuck():
    assert math.isinf(slip.margins([100.0], [0.0], 0.15)[0])


def test_tension_has_no_friction_to_give():
    assert slip.margins([-5.0], [1.0], 0.15)[0] == 0.0
    assert slip.margins([-5.0], [0.0], 0.15)[0] == 0.0


# ------------------------------------------------------------------- verdict

def clamped(mu=0.15, p=100.0, tau=5.0, n=10):
    """n nodes of uniform pressure and shear on a z-normal interface."""
    return slip.check([[0, 0, -p, 0, tau, 0]] * n, Z, mu)


def test_a_stuck_joint_validates_the_linear_solve():
    c = clamped(tau=5.0)                       # needs mu 0.05, has 0.15
    ok, why = slip.verdict(c)
    assert ok and c["stuck"]
    assert c["min_margin"] == pytest.approx(3.0)
    assert c["mu_required"] == pytest.approx(0.05)
    assert c["area_slipping"] == 0.0 and c["area_open"] == 0.0
    assert "linear result is the nonlinear one" in why


def test_a_slipping_joint_invalidates_it():
    c = clamped(tau=40.0)                      # needs mu 0.40, has 0.15
    ok, why = slip.verdict(c)
    assert not ok and not c["stuck"]
    assert c["area_slipping"] == pytest.approx(1.0)
    assert c["mu_required"] == pytest.approx(0.40)
    assert "slips" in why and "nonlinear" in why


def test_an_opening_joint_is_reported_separately():
    """Tension is a different failure from slip and needs a different fix."""
    c = slip.check([[0, 0, 20.0, 0, 1.0, 0]] * 4, Z, 0.15)
    ok, why = slip.verdict(c)
    assert not ok
    assert c["area_open"] == pytest.approx(1.0)
    assert c["area_slipping"] == 0.0
    assert "tension" in why and "opening" in why


def test_area_weighting_beats_node_counting():
    """One slipping node on a tiny element is not 50% of the interface.

    Mesh refinement clusters nodes exactly where stress concentrates, so an
    unweighted fraction reads high precisely where it matters most.
    """
    sig = [[0, 0, -100.0, 0, 5.0, 0], [0, 0, -100.0, 0, 90.0, 0]]
    big = slip.check(sig, Z, 0.15, areas=[99.0, 1.0])
    even = slip.check(sig, Z, 0.15)
    assert big["area_slipping"] == pytest.approx(0.01)
    assert even["area_slipping"] == pytest.approx(0.5)


def test_bad_areas_fall_back_to_node_counts():
    sig = [[0, 0, -100.0, 0, 90.0, 0]] * 2
    for bad in ([0.0, 0.0], [1.0], None):
        assert slip.check(sig, Z, 0.15, areas=bad)["area_slipping"] == 1.0


def test_worst_node_governs_not_the_average():
    """A joint that slips anywhere slips."""
    sig = [[0, 0, -100.0, 0, 1.0, 0]] * 99 + [[0, 0, -100.0, 0, 90.0, 0]]
    c = slip.check(sig, Z, 0.15)
    ok, _ = slip.verdict(c)
    assert not ok
    assert c["min_margin"] < 1.0
    assert c["mu_required"] == pytest.approx(0.9)


def test_empty_interface_is_not_a_failure():
    ok, why = slip.verdict(slip.check([], Z, 0.15))
    assert ok and "No interface stress" in why


def test_zero_normal_is_refused():
    with pytest.raises(ValueError, match="normal is zero"):
        slip.tractions(tensor(zz=-1.0), [0.0, 0.0, 0.0])


# ------------------------------------------------- matching nodes to areas

def test_area_lookup_bridges_the_two_naming_schemes():
    """code_aster hands back "N412"; the mesh's area map is keyed by the gmsh
    tag "412". A literal lookup misses every node, and the check then weighs
    every node equally without saying so."""
    w = {"412": 2.0, "77": 0.5}
    assert slip.areas_for(w, ["N412", "N77"]) == [2.0, 0.5]
    assert slip.areas_for(w, ["412", "77"]) == [2.0, 0.5]


def test_area_lookup_is_all_or_nothing():
    """A partial map would weigh some of the interface and count the rest."""
    assert slip.areas_for({"1": 1.0}, ["N1", "N2"]) is None
    assert slip.areas_for({}, ["N1"]) is None


def test_area_lookup_survives_an_empty_interface():
    assert slip.areas_for({"1": 1.0}, []) == []
