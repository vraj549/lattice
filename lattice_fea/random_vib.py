"""Random-vibration post-processing from a base-excitation sweep.

For a linear system driven by a stationary random base input,

    PSD_out(f) = |T(f)|^2 * PSD_in(f)
    RMS        = sqrt( integral PSD_out(f) df )

so a swept transmissibility plus the input spectrum gives the full answer.
That is exact for linear structures and reuses the harmonic chain that is
already validated, rather than depending on a separate random-analysis
operator whose keywords would be unverified.

Qualification specs are given as breakpoints in (Hz, g^2/Hz) with log-log
interpolation between them, which is what `psd_at` implements.
"""
from __future__ import annotations

import math

G_MM = 9810.0     # 1 g in mm/s^2


def _sorted_spec(spec) -> list:
    """Breakpoints as sorted (Hz, g^2/Hz) pairs."""
    return sorted((float(a), float(b)) for a, b in spec if float(a) > 0)


def psd_at(spec, f: float, _pts=None) -> float:
    """Input PSD (g^2/Hz) at f, log-log interpolated between breakpoints.
    Zero outside the specified band, as a spec is only defined where written.

    `_pts` lets a caller in a loop pass the already-sorted breakpoints; this
    was re-sorting the spec once per swept frequency."""
    pts = _pts if _pts is not None else _sorted_spec(spec)
    if not pts or f < pts[0][0] or f > pts[-1][0]:
        return 0.0
    for i in range(1, len(pts)):
        f0, w0 = pts[i - 1]
        f1, w1 = pts[i]
        if f <= f1:
            if f1 <= f0:
                return w1
            if w0 <= 0 or w1 <= 0:      # linear fallback through a zero
                t = (f - f0) / (f1 - f0)
                return w0 + (w1 - w0) * t
            lt = math.log(f / f0) / math.log(f1 / f0)
            return math.exp(math.log(w0) + lt * (math.log(w1) - math.log(w0)))
    return pts[-1][1]


def grms_input(spec) -> float:
    """Overall g_RMS of the input spectrum itself — the number a spec is
    usually labelled with, and a good check that the table was entered right."""
    pts = _sorted_spec(spec)
    total = 0.0
    for i in range(1, len(pts)):
        f0, w0 = pts[i - 1]
        f1, w1 = pts[i]
        if f1 <= f0:
            continue
        if w0 <= 0 or w1 <= 0:
            total += 0.5 * (w0 + w1) * (f1 - f0)
            continue
        # exact integral of a log-log (power-law) segment
        m = math.log(w1 / w0) / math.log(f1 / f0)
        if abs(m + 1.0) < 1e-9:
            total += w0 * f0 * math.log(f1 / f0)
        else:
            total += w0 / (f0 ** m) * (f1 ** (m + 1) - f0 ** (m + 1)) / (m + 1)
    return math.sqrt(max(total, 0.0))


def transmissibility(freq, module, phase, base_g: float):
    """Absolute-acceleration transmissibility from a RELATIVE displacement FRF.

    Base excitation gives motion relative to the base. At frequency f the
    relative acceleration is -(2 pi f)^2 * u_rel, and the absolute response is
    the base motion plus that. Working with the complex response (module and
    phase are both exported) keeps the phase relationship, which matters
    because near resonance the two terms are far from in phase.

        T(f) = | 1 + a_rel(f) / a_base |
    """
    a_base = base_g * G_MM                 # mm/s^2
    out = []
    for i, f in enumerate(freq):
        w = 2.0 * math.pi * f
        mag = module[i] * w * w            # |a_rel|, mm/s^2
        ph = phase[i] if phase and i < len(phase) else 0.0
        # relative acceleration is 180 deg from relative displacement
        re = -mag * math.cos(ph)
        im = -mag * math.sin(ph)
        out.append(math.hypot(1.0 + re / a_base, im / a_base) if a_base else 0.0)
    return out


def response(freq, trans, spec, base_g: float = 1.0):
    """Response PSD and RMS from transmissibility and an input spectrum.

    Returns dict with the response PSD (g^2/Hz), overall g_RMS, 3-sigma, and
    the input g_RMS for comparison.
    """
    pts = _sorted_spec(spec)
    psd_out, psd_in = [], []
    for i, f in enumerate(freq):
        pin = psd_at(spec, f, pts)
        psd_in.append(pin)
        psd_out.append(trans[i] ** 2 * pin)

    # trapezoidal over the swept grid (log-spaced sweeps are fine here since
    # we integrate in linear f)
    total = 0.0
    for i in range(1, len(freq)):
        total += 0.5 * (psd_out[i] + psd_out[i - 1]) * (freq[i] - freq[i - 1])
    rms = math.sqrt(max(total, 0.0))

    # How much of the INPUT spectrum the sweep actually covered.
    #
    # The integral above only spans the swept frequencies. If the sweep stops
    # at 500 Hz and the spec runs to 2000, everything above 500 contributes
    # nothing and the reported g RMS comes back low — with no other sign that
    # anything is missing. That is a wrong answer that looks like a right one,
    # so it is measured and reported rather than left to the reader to notice.
    covered = 0.0
    if pts and len(freq) >= 2:
        f_lo, f_hi = min(freq), max(freq)
        lo, hi = max(pts[0][0], f_lo), min(pts[-1][0], f_hi)
        if hi > lo:
            covered = _band_power(pts, lo, hi)
    total_in = _band_power(pts, pts[0][0], pts[-1][0]) if pts else 0.0
    frac = (covered / total_in) if total_in > 0 else 1.0

    return {
        "freq": list(freq), "psd_out": psd_out, "psd_in": psd_in,
        "grms": rms, "three_sigma": 3.0 * rms,
        "grms_in": grms_input(spec),
        "input_covered": frac,
        "sweep_band": [min(freq), max(freq)] if len(freq) else [0.0, 0.0],
        "spec_band": [pts[0][0], pts[-1][0]] if pts else [0.0, 0.0],
    }


def _band_power(pts, lo: float, hi: float) -> float:
    """Integral of the input PSD between lo and hi, exact on log-log segments."""
    total = 0.0
    for i in range(1, len(pts)):
        f0, w0 = pts[i - 1]
        f1, w1 = pts[i]
        a, b = max(f0, lo), min(f1, hi)
        if b <= a:
            continue
        if w0 <= 0 or w1 <= 0 or f1 <= f0:
            total += 0.5 * (psd_at(None, a, pts) + psd_at(None, b, pts)) * (b - a)
            continue
        m = math.log(w1 / w0) / math.log(f1 / f0)
        c = w0 / (f0 ** m)
        if abs(m + 1.0) < 1e-9:
            total += c * math.log(b / a)
        else:
            total += c * (b ** (m + 1) - a ** (m + 1)) / (m + 1)
    return total


def miles(fn: float, q: float, spec) -> dict:
    """Miles' single-degree-of-freedom estimate, for cross-checking.

        g_RMS = sqrt( pi/2 * fn * Q * PSD(fn) )

    Valid when one mode dominates the response; it ignores every other mode,
    so agreement with the full integration is evidence the assumption holds.
    """
    w = psd_at(spec, fn)
    g = math.sqrt(max(math.pi / 2.0 * fn * q * w, 0.0))
    return {"fn": fn, "q": q, "psd_at_fn": w, "grms": g, "three_sigma": 3.0 * g}


def cumulative_participation(part_block) -> "list|None":
    """Running sum of unitary effective mass per direction from the
    participation table, so truncation can be judged."""
    if not part_block:
        return None
    cols = part_block.get("columns", [])
    try:
        ix = [cols.index(f"MASS_EFFE_UN_D{c}") for c in ("X", "Y", "Z")]
    except ValueError:
        return None
    tot = [0.0, 0.0, 0.0]
    out = []
    for row in part_block.get("rows", []):
        for k in range(3):
            v = row[ix[k]]
            if isinstance(v, (int, float)):
                tot[k] += v
        out.append(list(tot))
    return out
