"""Shock response spectrum: the SRS algorithm, and the modal combination.

Validated on things that must be true of any correct SRS rather than on
remembered table values: an independent integration of the same oscillator,
and the two asymptotes every shock spectrum has.
"""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import shock  # noqa: E402

G = 9.81


def reference_srs(a_g, dt, freqs, zeta):
    """RK4 on the relative-motion oscillator — a different algorithm, not a
    different arrangement of the same one.

        z'' + 2*zeta*w*z' + w^2*z = -a_base
        absolute acceleration     = -2*zeta*w*z' - w^2*z
    """
    n = len(a_g)

    def base(t):
        x = t / dt
        i = int(math.floor(x))
        if i < 0 or i >= n - 1:
            return 0.0
        fr = x - i
        return a_g[i] * (1 - fr) + a_g[i + 1] * fr

    out = []
    for fn in freqs:
        w = 2 * math.pi * fn
        u = v = t = 0.0
        h = dt / 8.0
        peak = 0.0

        def f(t, u, v):
            return (v, -2 * zeta * w * v - w * w * u - base(t))

        for _ in range((n - 1) * 8):
            k1 = f(t, u, v)
            k2 = f(t + h / 2, u + h / 2 * k1[0], v + h / 2 * k1[1])
            k3 = f(t + h / 2, u + h / 2 * k2[0], v + h / 2 * k2[1])
            k4 = f(t + h, u + h * k3[0], v + h * k3[1])
            u += h / 6 * (k1[0] + 2 * k2[0] + 2 * k3[0] + k4[0])
            v += h / 6 * (k1[1] + 2 * k2[1] + 2 * k3[1] + k4[1])
            t += h
            peak = max(peak, abs(-2 * zeta * w * v - w * w * u))
        out.append(peak)
    return out


# ------------------------------------------------------------- the algorithm

def test_smallwood_matches_direct_integration():
    """The recursive filter and a straight RK4 of the same oscillator."""
    A, tau, zeta = 20.0, 0.011, 0.05
    dt = tau / 400.0
    _, a = shock.pulse_history("half_sine", A, tau, dt, 2.5 * tau)
    freqs = [50.0, 100.0, 300.0, 900.0]
    got = shock.srs_of_history(a, dt, freqs, zeta)
    ref = reference_srs(a, dt, freqs, zeta)
    for f, g_, r in zip(freqs, got, ref):
        assert g_ == pytest.approx(r, rel=2e-3), f"{f} Hz: {g_} vs {r}"


@pytest.mark.parametrize("kind", sorted(shock.PULSES))
def test_high_frequency_limit_is_the_pulse_peak(kind):
    """A mode far stiffer than the pulse just rides the base: SRS -> ZPA.

    True of every pulse here because every one starts and ends at zero. A
    pulse with a step in it does not do this — see the next test, which is why
    an initial-peak sawtooth is not offered.
    """
    srs = shock.pulse_spectrum(kind, 20.0, 11.0, [500000.0], 0.05)[0]
    assert srs == pytest.approx(20.0, rel=2e-3)


def test_a_step_does_not_settle_to_its_own_amplitude():
    """The reason an initial-peak sawtooth is not in PULSES. An oscillator hit
    with a step overshoots to 1 + exp(-zeta*pi/sqrt(1-zeta^2)) no matter how
    stiff it is, so 'ZPA = pulse amplitude' would be wrong by 85% — and the
    missing-mass correction is applied at the ZPA.
    """
    zeta, dt = 0.05, 1e-7
    step = np.full(400000, 20.0)
    peak = shock.srs_of_history(step, dt, [20000.0], zeta)[0]
    expected = 20.0 * (1 + math.exp(-zeta * math.pi / math.sqrt(1 - zeta ** 2)))
    assert peak == pytest.approx(expected, rel=5e-3)


@pytest.mark.parametrize("kind", sorted(shock.PULSES))
def test_zpa_is_measured_not_assumed(kind):
    """It is read off the pulse's own spectrum. For a trapezoid that is a
    fraction of a percent above the amplitude where a real basis ends."""
    out = shock.spectrum_for({"input": "pulse", "pulse": kind,
                              "pulse_g": 20.0, "pulse_ms": 11.0},
                             [100.0, 2000.0])
    assert out["zpa"] == pytest.approx(20.0, rel=0.02)
    assert out["zpa"] >= 20.0 * 0.999


@pytest.mark.parametrize("kind", sorted(shock.PULSES))
def test_low_frequency_limit_is_the_velocity_change(kind):
    """A mode far softer than the pulse sees an impulse: SRS -> 2*pi*f*dV.

    This is the sharpest check that the pulse SHAPE is right — dV is the area
    under it, and every shape here has a different one.
    """
    A, tau_ms = 20.0, 11.0
    dv_frac = shock.PULSES[kind][1]
    dV = dv_frac * A * G * (tau_ms / 1000.0)          # m/s
    for f in (0.5, 1.0, 2.0):
        srs = shock.pulse_spectrum(kind, A, tau_ms, [f], 0.0)[0]
        assert srs == pytest.approx(2 * math.pi * f * dV / G, rel=0.01)


def test_half_sine_peak_amplification():
    """The classical result: an undamped half-sine SRS peaks at 1.766 times
    the pulse, near f*tau = 0.8. Nothing else in this module produces that
    number, so it is a real check on the whole chain."""
    A, tau = 20.0, 0.011
    fs = [x / tau for x in np.linspace(0.5, 1.3, 40)]
    srs = shock.pulse_spectrum("half_sine", A, tau * 1000.0, fs, 0.0)
    peak = max(srs)
    at = fs[srs.index(peak)] * tau
    assert peak / A == pytest.approx(1.766, rel=0.01)
    assert 0.7 < at < 1.0


def test_damping_only_ever_lowers_the_residual():
    """Below the pulse the peak is the first ring-down swing, so damping eats
    into it — monotonically, and by less than a full half cycle of decay.

    Stated as a bracket rather than as exp(-zeta*pi/2): that expression is a
    quarter-cycle estimate, and the peak moves earlier as damping rises, so it
    is a good heuristic and not an identity. Asserting it as one held to 2% at
    Q=25 and failed at Q=5.
    """
    un = shock.pulse_spectrum("half_sine", 20.0, 11.0, [1.0], 0.0)[0]
    prev = un
    for zeta in (0.02, 0.05, 0.1, 0.2):
        lo = shock.pulse_spectrum("half_sine", 20.0, 11.0, [1.0], zeta)[0]
        assert lo < prev
        assert math.exp(-zeta * math.pi) < lo / un < 1.0
        prev = lo


def test_ringdown_padding_is_not_optional():
    """Truncating at the end of the pulse loses the residual response, which
    IS the low-frequency half of the spectrum."""
    A, tau, dt = 20.0, 0.011, 0.011 / 400
    _, short = shock.pulse_history("half_sine", A, tau, dt, 0.0)
    _, long_ = shock.pulse_history("half_sine", A, tau, dt, 3.0)
    f = [2.0]
    assert (shock.srs_of_history(short, dt, f, 0.0)[0]
            < 0.2 * shock.srs_of_history(long_, dt, f, 0.0)[0])


# ------------------------------------------------------------------ the spec

def test_srs_table_interpolates_log_log():
    spec = [(100.0, 10.0), (1000.0, 100.0)]        # one decade, one decade
    assert shock.srs_at(spec, 100.0) == pytest.approx(10.0)
    assert shock.srs_at(spec, 1000.0) == pytest.approx(100.0)
    assert shock.srs_at(spec, 316.227766) == pytest.approx(31.6227766, rel=1e-6)


def test_srs_holds_outside_the_band_rather_than_zeroing():
    """A PSD is zero where it is not written; an SRS is not. Its top value is
    the ZPA, which every stiffer mode sees — zeroing it would silently drop
    the response of the modes most likely to matter."""
    spec = [(100.0, 10.0), (2000.0, 100.0)]
    assert shock.srs_at(spec, 10.0) == pytest.approx(10.0)
    assert shock.srs_at(spec, 50000.0) == pytest.approx(100.0)


def test_pulse_and_table_inputs_produce_the_same_shape_of_answer():
    fs = [50.0, 500.0]
    a = shock.spectrum_for({"input": "pulse", "pulse": "half_sine",
                            "pulse_g": 20.0, "pulse_ms": 11.0}, fs)
    b = shock.spectrum_for({"input": "spectrum",
                            "spec": [[10, 5], [2000, 50]]}, fs)
    for out in (a, b):
        assert len(out["srs"]) == 2
        assert out["zpa"] > 0
        assert out["source"]


# ----------------------------------------------------------- the combination

def test_combination_rules_are_ordered():
    """SRSS <= NRL <= ABS, always. Each rule assumes more of the modes peak
    together than the last."""
    v = [10.0, 6.0, 3.0, 1.0]
    s = shock.combine(v, "srss")
    n = shock.combine(v, "nrl")
    a = shock.combine(v, "abs")
    assert s < n < a
    assert s == pytest.approx(math.sqrt(100 + 36 + 9 + 1))
    assert n == pytest.approx(10 + math.sqrt(36 + 9 + 1))
    assert a == pytest.approx(20.0)


def test_combination_is_sign_blind():
    """Deliberate: the sign of a participation factor is not recoverable from
    effective mass, so no rule here may depend on one."""
    for rule in shock.RULES:
        assert (shock.combine([3.0, -4.0], rule)
                == shock.combine([-3.0, 4.0], rule)
                == shock.combine([3.0, 4.0], rule))


def test_single_mode_gives_itself_under_every_rule():
    for rule in shock.RULES:
        assert shock.combine([7.0], rule) == pytest.approx(7.0)


def test_participation_is_normalisation_invariant():
    """The physical response is phi * Gamma * S_d. Scaling the mode shape by c
    scales m_gene by c^2 and Gamma by 1/c, so the product does not move —
    which is why Gamma is taken from the mass table rather than assumed.
    """
    m_eff = 0.4
    for c in (0.5, 2.0, 10.0):
        base = shock.participation(m_eff, 1.0)
        scaled = shock.participation(m_eff, 1.0 * c * c)
        assert scaled == pytest.approx(base / c)
        assert c * scaled == pytest.approx(base)      # phi*Gamma invariant


def test_participation_of_a_dead_mode_is_zero():
    assert shock.participation(0.0, 1.0) == 0.0
    assert shock.participation(0.4, 0.0) == 0.0


# ----------------------------------------------------------- the modal table

def _modes():
    return [{"n": 1, "f": 120.0, "m_gene": 1.0, "eff": [0.02, 0.01, 0.70]},
            {"n": 2, "f": 340.0, "m_gene": 1.0, "eff": [0.60, 0.03, 0.10]},
            {"n": 3, "f": 900.0, "m_gene": 1.0, "eff": [0.05, 0.80, 0.05]}]


CFG = {"input": "pulse", "pulse": "half_sine", "pulse_g": 20.0,
       "pulse_ms": 11.0, "damping": 0.05, "rule": "srss"}


def test_modal_table_reports_what_the_basis_missed():
    out = shock.modal_table(_modes(), CFG, total_mass=0.5, axis=2)
    assert out["mass_captured"] == pytest.approx(0.85)
    assert out["missing_mass"] == pytest.approx(0.15)
    # the residual mass rides at the ZPA — the measured one, not the pulse
    # amplitude it is very nearly equal to
    zpa = out["input"]["zpa"]
    assert out["missing_force_N"] == pytest.approx(0.15 * 0.5 * zpa * shock.G_MM)


def test_interface_force_is_effective_mass_times_spectrum():
    """One mode, everything in it: the answer must be the hand calculation."""
    modes = [{"n": 1, "f": 120.0, "m_gene": 1.0, "eff": [0.0, 0.0, 1.0]}]
    out = shock.modal_table(modes, CFG, total_mass=0.5, axis=2)
    row = out["rows"][0]
    assert out["missing_mass"] == pytest.approx(0.0)
    assert row["force_N"] == pytest.approx(0.5 * row["srs_g"] * shock.G_MM)
    assert out["force_N"] == pytest.approx(row["force_N"])


def test_modal_displacement_is_the_spectral_displacement():
    """q = Gamma * S_a / omega^2 — the peak modal coordinate, in mm."""
    out = shock.modal_table(_modes(), CFG, total_mass=0.5, axis=2)
    r = out["rows"][0]
    w = 2 * math.pi * r["f"]
    assert r["q"] == pytest.approx(r["gamma"] * r["srs_g"] * shock.G_MM / w ** 2)


def test_rule_choice_moves_the_answer_the_right_way():
    got = {rule: shock.modal_table(_modes(), {**CFG, "rule": rule},
                                   0.5, 2)["force_N"]
           for rule in shock.RULES}
    assert got["srss"] < got["nrl"] < got["abs"]


def test_axis_selects_the_direction():
    """Mode 3 owns Y and mode 2 owns X, so the driven axis must change which
    mode dominates."""
    x = shock.modal_table(_modes(), CFG, 0.5, 0)
    y = shock.modal_table(_modes(), CFG, 0.5, 1)
    assert max(x["rows"], key=lambda r: r["force_N"])["mode"] == 2
    assert max(y["rows"], key=lambda r: r["force_N"])["mode"] == 3
    assert x["axis"] == "X" and y["axis"] == "Y"


# ------------------------------------------- rigid vs periodic (RG 1.92 Rev 2)

def test_alpha_is_the_lindley_yow_ratio():
    assert shock.rigid_fraction(20.0, 20.0) == pytest.approx(1.0)   # at the ZPA
    assert shock.rigid_fraction(40.0, 20.0) == pytest.approx(0.5)   # half rigid
    assert shock.rigid_fraction(200.0, 20.0) == pytest.approx(0.1)  # resonant
    # softer than the input: it rings after the event, which is periodic
    assert shock.rigid_fraction(5.0, 20.0) == 0.0
    assert shock.rigid_fraction(0.0, 20.0) == 0.0


def test_an_all_rigid_basis_gives_newtons_second_law():
    """The identity that makes the algebraic sum obviously right.

    If every mode has come down to the ZPA, nothing resonates: the model is a
    rigid body on a shaking base, and the load into the supports is its whole
    mass times the acceleration. Nothing less.

    SRSS cannot produce this. It is the check that the previous version failed.
    """
    zpa = 20.0
    modes = [{"n": i, "f": 4000.0 + 500 * i, "m_gene": 1.0,
              "eff": [0, 0, 0.25]} for i in range(4)]
    cfg = {"input": "spectrum", "spec": [[10, zpa], [20000, zpa]],
           "rule": "srss", "damping": 0.05}
    out = shock.modal_table(modes, cfg, total_mass=0.8, axis=2)

    assert out["mass_captured"] == pytest.approx(1.0)
    assert all(r["alpha"] == pytest.approx(1.0) for r in out["rows"])
    assert out["force_N"] == pytest.approx(0.8 * zpa * shock.G_MM)
    assert out["force_periodic_N"] == pytest.approx(0.0)
    assert out["rigid_share"] == pytest.approx(1.0)
    # and the old behaviour would have been wrong by sqrt(4)
    assert out["force_N"] == pytest.approx(2.0 * out["force_modal_N"], rel=1e-9)


def test_a_resonant_mode_stays_mostly_periodic():
    """The other end: one sharply amplified mode is not a rigid response and
    must not be added algebraically to anything."""
    modes = [{"n": 1, "f": 120.0, "m_gene": 1.0, "eff": [0, 0, 0.9]}]
    cfg = {"input": "spectrum", "spec": [[10, 20.0], [120, 400.0], [20000, 20.0]],
           "rule": "srss", "damping": 0.05}
    out = shock.modal_table(modes, cfg, 0.5, 2)
    assert out["rows"][0]["alpha"] < 0.1
    assert out["force_periodic_N"] > 5 * out["force_rigid_N"]


def test_one_mode_is_unchanged_by_the_split():
    """Splitting a single mode and recombining it must give it back — alpha^2
    plus (1 - alpha^2) is 1, so this is a check that the split is a rotation
    and not a rescaling."""
    modes = [{"n": 1, "f": 120.0, "m_gene": 1.0, "eff": [0, 0, 1.0]}]
    out = shock.modal_table(modes, CFG, 0.5, 2)
    assert out["missing_mass"] == pytest.approx(0.0)
    assert out["force_N"] == pytest.approx(out["rows"][0]["force_N"])


def test_missing_mass_is_rigid_and_adds_algebraically():
    """It is the residual mass riding at the ZPA — in phase with the input and
    with every other rigid term, so it is summed, not SRSS'd, with them."""
    zpa = 20.0
    modes = [{"n": 1, "f": 8000.0, "m_gene": 1.0, "eff": [0, 0, 0.6]}]
    cfg = {"input": "spectrum", "spec": [[10, zpa], [20000, zpa]],
           "rule": "srss", "damping": 0.05}
    out = shock.modal_table(modes, cfg, 1.0, 2)
    assert out["missing_mass"] == pytest.approx(0.4)
    # 0.6 rigid + 0.4 missing = the whole mass at the ZPA
    assert out["force_rigid_N"] == pytest.approx(1.0 * zpa * shock.G_MM)
    assert out["force_N"] == pytest.approx(1.0 * zpa * shock.G_MM)


def test_a_mode_on_the_plateau_is_rigid_despite_rounding():
    """Log-log interpolation returns 19.999999999999996 for a plateau of 20.

    An exact `S_a < zpa` test therefore made every mode sitting on the ZPA —
    which is most of a high-frequency basis — fully periodic, and silently
    turned the rigid correction off. This is that regression.
    """
    cfg = {"input": "spectrum", "spec": [[10, 20.0], [20000, 20.0]],
           "rule": "srss", "damping": 0.05}
    got = shock.spectrum_for(cfg, [4000.0])
    assert got["srs"][0] != got["zpa"]              # the rounding is real
    assert got["srs"][0] == pytest.approx(got["zpa"], rel=1e-12)
    assert shock.rigid_fraction(got["srs"][0], got["zpa"]) == pytest.approx(1.0)
