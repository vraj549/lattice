"""Random-vibration maths, checked against closed-form results."""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea.random_vib import (  # noqa: E402
    G_MM, grms_input, miles, psd_at, response, transmissibility)

# A standard-shaped qualification spec
SPEC = [(20, 0.01), (80, 0.04), (350, 0.04), (2000, 0.007)]


def test_psd_interpolation():
    assert psd_at(SPEC, 80) == pytest.approx(0.04)
    assert psd_at(SPEC, 350) == pytest.approx(0.04)
    assert psd_at(SPEC, 200) == pytest.approx(0.04)      # flat plateau
    assert psd_at(SPEC, 10) == 0.0                       # outside the spec
    assert psd_at(SPEC, 5000) == 0.0
    # geometric midpoint of a log-log ramp is the geometric mean of the levels
    fm = math.sqrt(20 * 80)
    assert psd_at(SPEC, fm) == pytest.approx(math.sqrt(0.01 * 0.04), rel=1e-9)


def test_flat_spec_grms_closed_form():
    """A perfectly flat W over [f0,f1] has g_RMS = sqrt(W*(f1-f0))."""
    flat = [(20, 0.05), (2000, 0.05)]
    assert grms_input(flat) == pytest.approx(math.sqrt(0.05 * 1980), rel=1e-9)


def test_grms_of_ramp_segment():
    """Integral of a log-log ramp, checked against direct quadrature."""
    ramp = [(10, 0.001), (100, 0.1)]
    n = 200001
    tot = 0.0
    prev = psd_at(ramp, 10)
    for i in range(1, n):
        f = 10 + (100 - 10) * i / (n - 1)
        cur = psd_at(ramp, f)
        tot += 0.5 * (cur + prev) * ((100 - 10) / (n - 1))
        prev = cur
    assert grms_input(ramp) == pytest.approx(math.sqrt(tot), rel=1e-4)


def test_transmissibility_far_below_resonance():
    """Well below the first mode the structure rides with the base, so the
    relative motion is ~0 and transmissibility -> 1."""
    freq = [1.0, 2.0]
    module = [1e-12, 1e-12]          # essentially no relative displacement
    phase = [0.0, 0.0]
    t = transmissibility(freq, module, phase, base_g=1.0)
    assert t[0] == pytest.approx(1.0, abs=1e-6)


def test_transmissibility_matches_sdof_theory():
    """Drive a known SDOF: relative displacement of a base-excited oscillator
    is u = -a_base/(wn^2 - w^2 + 2*i*zeta*wn*w). Feed that in and the recovered
    transmissibility must equal the textbook |T| for base excitation."""
    fn, zeta, base_g = 100.0, 0.02, 1.0
    wn = 2 * math.pi * fn
    a_base = base_g * G_MM
    freq, module, phase = [], [], []
    for i in range(400):
        f = 10 + i * 0.75
        w = 2 * math.pi * f
        den = complex(wn * wn - w * w, 2 * zeta * wn * w)
        u = -a_base / den                      # complex relative displacement
        freq.append(f); module.append(abs(u)); phase.append(math.atan2(u.imag, u.real))
    t = transmissibility(freq, module, phase, base_g)

    for i, f in enumerate(freq):
        r = f / fn
        expect = math.sqrt((1 + (2 * zeta * r) ** 2) /
                           ((1 - r * r) ** 2 + (2 * zeta * r) ** 2))
        assert t[i] == pytest.approx(expect, rel=2e-3), f"at {f} Hz"


def test_peak_transmissibility_is_Q():
    """At resonance |T| ~ Q = 1/(2 zeta) for light damping."""
    fn, zeta = 100.0, 0.02
    wn, a_base = 2 * math.pi * fn, G_MM
    freq, module, phase = [], [], []
    for i in range(4001):
        f = 90 + i * 0.005
        w = 2 * math.pi * f
        u = -a_base / complex(wn * wn - w * w, 2 * zeta * wn * w)
        freq.append(f); module.append(abs(u)); phase.append(math.atan2(u.imag, u.real))
    t = transmissibility(freq, module, phase, 1.0)
    assert max(t) == pytest.approx(1 / (2 * zeta), rel=0.02)


def test_response_and_miles_agree_for_single_mode():
    """With one isolated mode inside the band, integrating |T|^2 * PSD must
    land close to Miles' equation — that is exactly Miles' assumption."""
    fn, zeta = 200.0, 0.02
    q = 1 / (2 * zeta)
    wn, a_base = 2 * math.pi * fn, G_MM
    freq, module, phase = [], [], []
    n = 20000
    for i in range(n):
        f = 20 + (2000 - 20) * i / (n - 1)
        w = 2 * math.pi * f
        u = -a_base / complex(wn * wn - w * w, 2 * zeta * wn * w)
        freq.append(f); module.append(abs(u)); phase.append(math.atan2(u.imag, u.real))
    t = transmissibility(freq, module, phase, 1.0)
    r = response(freq, t, SPEC, 1.0)
    m = miles(fn, q, SPEC)
    # Miles omits the below-resonance rigid-body content, so the full
    # integration is slightly higher; they must still be the same size.
    assert r["grms"] == pytest.approx(m["grms"], rel=0.25)
    assert r["three_sigma"] == pytest.approx(3 * r["grms"])
    assert r["grms_in"] == pytest.approx(grms_input(SPEC))


def test_response_zero_outside_spec():
    freq = [1.0, 2.0, 5.0]
    t = [1.0, 1.0, 1.0]
    r = response(freq, t, SPEC, 1.0)
    assert r["grms"] == 0.0
