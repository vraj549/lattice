"""Preload calibration: the imposed strain is corrected until the bolts
actually carry the force that was asked for."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import bolt_sizing, comm_writer, preload  # noqa: E402
from lattice_fea.projects import default_setup  # noqa: E402


# --------------------------------------------------------------- the physics

def joint_ratio(d, P, grip, E_P):
    """delta_S / (delta_S + delta_P) — the fraction of a requested preload an
    imposed strain actually delivers."""
    g = bolt_sizing.thread_geometry(d, P)
    dS = bolt_sizing.bolt_compliance(g, grip, 210000.0)["delta_S"]
    dP = bolt_sizing.member_compliance(g, grip, 1.1 * d, 1.5 * d, 3.0 * d,
                                       E_P)["delta_P"]
    return dS / (dS + dP)


def test_imposed_strain_undershoots_by_the_joint_share():
    """The defect this module exists to remove, stated as a number.

    A joint stiffer than the bolt takes back most of the imposed shortening.
    Steel on steel loses of order 15%, aluminium of order 35% — far too much
    to leave in the one number a sizing tool is asked for.
    """
    steel = joint_ratio(6, 1.0, 12.0, 210000.0)
    alu = joint_ratio(6, 1.0, 12.0, 70000.0)
    assert 0.80 < steel < 0.90
    assert 0.55 < alu < 0.70
    assert alu < steel          # softer members take back more


def test_ratio_is_one_minus_phi():
    """It is exactly the complement of the VDI load factor, not a new model."""
    g = bolt_sizing.thread_geometry(8, 1.25)
    dS = bolt_sizing.bolt_compliance(g, 16.0, 210000.0)["delta_S"]
    dP = bolt_sizing.member_compliance(g, 16.0, 8.8, 12.0, 24.0,
                                       210000.0)["delta_P"]
    phi = dP / (dS + dP)
    assert joint_ratio(8, 1.25, 16.0, 210000.0) == pytest.approx(1.0 - phi)


# ------------------------------------------------------- the calibration loop

class Joint:
    """n bolts clamping the same pair of parts.

    Each bolt has its own local member stiffness `kP`, and neighbouring
    clamped regions are tied to each other by `kC` — the plate between the
    bolts. Tightening one bolt therefore relieves its neighbours a little,
    which is the coupling that makes a componentwise correction a Jacobi step
    rather than an exact inverse.

        bolt i:   F_i = kS (x_i - u_i)              x_i = imposed shortening
        node i:   kS (x_i - u_i) = kP u_i + sum_j kC (u_i - u_j)
    """

    def __init__(self, k_bolt, k_joint, targets, k_couple=0.0):
        self.kS, self.kP, self.kC = k_bolt, k_joint, k_couple
        self.targets = targets
        self.idx = sorted(targets)
        self.calls = 0
        n = len(self.idx)
        A = np.full((n, n), -self.kC) + np.eye(n) * (
            self.kS + self.kP + (n - 1) * self.kC + self.kC)
        self.A = A

    def __call__(self, scale):
        self.calls += 1
        x = np.array([scale[i] * self.targets[i] / self.kS for i in self.idx])
        u = np.linalg.solve(self.A, self.kS * x)
        return {i: self.kS * (x[k] - u[k]) for k, i in enumerate(self.idx)}


def test_single_bolt_correction_is_exact_in_one_pass():
    """Linear and uncoupled: requested/achieved is the exact inverse."""
    j = Joint(k_bolt=1.0e5, k_joint=6.0e5, targets={1: 9000.0})
    out = preload.calibrate({1: 9000.0}, j)
    assert out["achieved"][1] == pytest.approx(9000.0, rel=1e-9)
    assert out["passes"] == 2          # one to measure, one to confirm
    assert out["scale"][1] > 1.0


def test_uncalibrated_first_pass_shows_the_shortfall():
    j = Joint(k_bolt=1.0e5, k_joint=6.0e5, targets={1: 9000.0})
    raw = j({1: 1.0})[1]
    assert raw == pytest.approx(9000.0 * 6.0 / 7.0, rel=1e-9)   # kP/(kS+kP)


def test_coupled_bolts_converge():
    """Four bolts in one joint. The componentwise step is a Jacobi iteration,
    so it is not exact on the first correction — it has to actually converge,
    and the returned `achieved` must be a measurement of the returned scale.
    """
    targets = {1: 8000.0, 2: 5000.0, 3: 12000.0, 4: 3000.0}
    j = Joint(k_bolt=1.0e5, k_joint=6.0e5, targets=targets, k_couple=6.0e4)
    out = preload.calibrate(targets, j, tol=1e-4, max_passes=8)
    for i, F in targets.items():
        assert out["achieved"][i] == pytest.approx(F, rel=2e-4)
    assert out["max_error"] <= 1e-4
    # and `achieved` is genuinely the response to `scale`, not extrapolated
    assert j(out["scale"]) == pytest.approx(out["achieved"], rel=1e-12)


def test_convergence_is_monotone_not_lucky():
    targets = {1: 8000.0, 2: 5000.0, 3: 12000.0, 4: 3000.0, 5: 6000.0}
    errs = []

    def probe(scale):
        r = j(scale)
        errs.append(max(abs(r[i] / targets[i] - 1) for i in targets))
        return r

    j = Joint(k_bolt=1.0e5, k_joint=6.0e5, targets=targets, k_couple=6.0e4)
    preload.calibrate(targets, probe, tol=1e-12, max_passes=6)
    assert len(errs) >= 4
    for a, b in zip(errs, errs[1:]):
        assert b < a


def test_unreacted_bolt_is_refused_not_scaled():
    """A bolt the model does not react would otherwise be handed a scale of
    thousands. Refusing is the useful answer."""
    def dead(scale):
        return {1: 1.0}

    with pytest.raises(preload.CalibrationFailed, match="does not react"):
        preload.calibrate({1: 9000.0}, dead)


def test_missing_bolt_is_refused():
    with pytest.raises(preload.CalibrationFailed, match="no force"):
        preload.calibrate({1: 9000.0, 2: 9000.0}, lambda s: {1: 9000.0})


def test_scale_is_capped():
    """Even a plausible-looking but very soft bolt cannot ask for an absurd
    multiplier."""
    j = Joint(k_bolt=1.0e5, k_joint=1.0e9, targets={1: 9000.0})
    out = preload.calibrate({1: 9000.0}, j, max_passes=8)
    assert out["scale"][1] <= preload.MAX_SCALE


def test_no_bolts_is_a_no_op():
    out = preload.calibrate({}, lambda s: pytest.fail("must not solve"))
    assert out == {"scale": {}, "achieved": {}, "passes": 0, "max_error": 0.0}


def test_already_correct_joint_stops_after_one_pass():
    """A bolt in a rigid joint needs no correction and must not pay for a
    second solve to find that out."""
    j = Joint(k_bolt=1.0e5, k_joint=1.0e12, targets={1: 9000.0})
    out = preload.calibrate({1: 9000.0}, j, tol=1e-3)
    assert out["passes"] == 1
    assert j.calls == 1
    assert out["scale"][1] == 1.0


def test_defaults_land_within_one_percent_on_a_coupled_pattern():
    """The shipped defaults, on a six-bolt joint with strong plate coupling.

    This is the claim the docstring makes and the reason max_passes is 3 and
    not 2: one correction leaves several percent on a joint like this.
    """
    targets = {1: 8000.0, 2: 5000.0, 3: 12000.0,
               4: 3000.0, 5: 6000.0, 6: 9000.0}
    for frac in (0.05, 0.2, 0.8, 1.5):
        j = Joint(1.0e5, 6.0e5, targets, k_couple=frac * 6.0e5)
        out = preload.calibrate(targets, j)
        assert out["max_error"] < 0.01, f"coupling {frac}: {out['max_error']}"


def test_secant_beats_proportional_on_the_third_pass():
    """Why the extra eight lines exist. Both are exact for a single bolt; the
    difference is entirely in how they handle bolts pulling on each other."""
    targets = {1: 8000.0, 2: 5000.0, 3: 12000.0,
               4: 3000.0, 5: 6000.0, 6: 9000.0}

    def proportional(t, j, passes):
        s = {i: 1.0 for i in t}
        for p in range(passes):
            F = j(s)
            if p == passes - 1:
                return max(abs(F[i] / t[i] - 1) for i in t)
            s = {i: s[i] * t[i] / F[i] for i in t}

    for frac in (0.2, 0.8, 1.5):
        kw = dict(k_couple=frac * 6.0e5)
        sec = preload.calibrate(targets, Joint(1.0e5, 6.0e5, targets, **kw),
                                tol=0, max_passes=3)["max_error"]
        prop = proportional(targets, Joint(1.0e5, 6.0e5, targets, **kw), 3)
        assert sec < prop
