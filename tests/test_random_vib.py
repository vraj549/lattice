"""Random-vibration maths, checked against closed-form results."""
import base64
import json
import math
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import random_vib as rv  # noqa: E402
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


def test_sweep_that_misses_the_spec_is_reported():
    """A sweep narrower than the spectrum under-reports g RMS silently.

    The integral only spans swept frequencies, so stopping at 500 Hz on a
    20-2000 Hz spec throws away most of the input. The number still looks
    plausible, so the shortfall has to be measured and handed back.
    """
    spec = [[20, 0.04], [2000, 0.04]]          # flat, easy to reason about
    full = [20 + i * (1980 / 400) for i in range(401)]
    narrow = [20 + i * (480 / 400) for i in range(401)]   # 20-500 Hz only
    t_full = [1.0] * len(full)
    t_narrow = [1.0] * len(narrow)

    r_full = response(full, t_full, spec, 1.0)
    r_narrow = response(narrow, t_narrow, spec, 1.0)

    assert r_full["input_covered"] > 0.999
    # 480 Hz of a 1980 Hz flat band
    assert abs(r_narrow["input_covered"] - 480 / 1980) < 0.01
    # and the RMS really is low, which is exactly why it must be flagged
    assert r_narrow["grms"] < 0.55 * r_full["grms"]
    assert r_narrow["sweep_band"] == [20, 500.0]
    assert r_narrow["spec_band"] == [20.0, 2000.0]


# ------------------------------------------------- spectrum input validation

@pytest.mark.parametrize("bad", [
    [(0.0, 0.1), (100.0, 0.2)],        # a mistyped first frequency
    [(-5.0, 0.1), (100.0, 0.2)],
    [(20.0, 0.1), (float("nan"), 0.2)],
    [(20.0, -0.1), (100.0, 0.2)],      # negative PSD
])
def test_a_bad_breakpoint_is_refused_not_dropped(bad):
    """Filtering silently is how a two-row table passed the "needs two
    breakpoints" gate and arrived as one usable point: the reported g_RMS came
    back near zero for an input that had been thrown away, which reads as a
    qualification passing with enormous margin."""
    with pytest.raises(ValueError):
        rv.sorted_breakpoints(bad)
    with pytest.raises(ValueError):
        rv.grms_input(bad)


def test_a_valid_spectrum_is_sorted_and_kept_whole():
    pts = rv.sorted_breakpoints([(2000.0, 0.02), (20.0, 0.01)])
    assert pts == [(20.0, 0.01), (2000.0, 0.02)]
    flat = [(20.0, 0.01), (2000.0, 0.01)]
    assert rv.grms_input(flat) == pytest.approx(math.sqrt(0.01 * 1980.0))


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_browser_transmissibility_matches_the_server():
    """The FRF panel draws transmissibility in the browser; the random
    analysis computes it here. One formula, checked against the other."""
    freq = [10.0, 80.0, 99.0, 100.0, 101.0, 400.0]
    module = [2.5e-3, 0.21, 1.9, 2.4, 1.8, 1.1e-3]
    phase = [0.0, -0.3, -1.2, -1.57, -1.9, -3.1]
    want = rv.transmissibility(freq, module, phase, 1.0)
    src = pathlib.Path(__file__).parent.parent / "lattice_fea/ui/js/dynamics.js"
    script = (
        "const m = await import('data:text/javascript;base64,"
        + base64.b64encode(src.read_bytes()).decode() + "');"
        + f"console.log(JSON.stringify(m.transmissibility({freq}, {module}, {phase}, 1)));")
    out = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == pytest.approx(want, rel=1e-12)
