"""Shock response spectrum analysis.

A shock is specified one of two ways, and this module accepts both:

* an **SRS** — the peak acceleration a single-degree-of-freedom oscillator of
  frequency f and quality factor Q reaches when its base is driven by the
  event. Pyroshock and most spacecraft specs are written this way.
* a **classical pulse** — half-sine, sawtooth or trapezoid of a stated
  amplitude and duration, as MIL-STD-810 Method 516 defines them. Its SRS is
  computed here, so both inputs meet in the same place.

The structural side is a spectrum combination over the modal basis, which is
the standard method for a linear structure and reuses the modal solve
unchanged. As with `random_vib`, the solver does only what it has already been
proven to do — extract modes — and the spectrum arithmetic lives here where it
is unit-tested.

What an SRS is not
------------------
An SRS is a **peak**, not a history. It says how hard each frequency was hit,
with no phase and no time. So the combination of modes can only be
statistical, every combined quantity is a magnitude with no sign, and two
quantities from the same run do not necessarily occur at the same instant. A
combined stress and a combined displacement are each defensible; their ratio
is not.
"""
from __future__ import annotations

import math

import numpy as np

G_MM = 9810.0     # 1 g in mm/s^2

TRAPEZOID_RISE = 0.1        # fraction of the pulse spent rising, and falling

# MIL-STD-810 Method 516 classical pulses. Each returns a(t)/A on [0, 1] of
# the pulse duration, plus the velocity change as a fraction of A*tau, which
# fixes the low-frequency asymptote of the SRS and is the cleanest check that
# the shape was built right.
# MIL-STD-810 Method 516's three shapes. An initial-peak sawtooth is left out
# on purpose: it starts with a step, no shock machine can produce one, and its
# spectrum never settles to the pulse amplitude — an oscillator hit with a step
# overshoots to 1 + exp(-zeta*pi/sqrt(1-zeta^2)) times it, about 1.85 at Q=10.
PULSES = {
    "half_sine": ("Half-sine", 2.0 / math.pi),
    "sawtooth": ("Terminal-peak sawtooth", 0.5),
    "trapezoid": ("Trapezoid", 1.0 - TRAPEZOID_RISE),
}

RULES = {
    "srss": "SRSS — square root of the sum of squares",
    "nrl": "NRL — largest term plus SRSS of the rest",
    "abs": "Absolute sum — upper bound",
}


# ----------------------------------------------------------------- the input

def _sorted_spec(spec) -> list:
    return sorted((float(a), float(b)) for a, b in spec if float(a) > 0)


def srs_at(spec, f: float, _pts=None) -> float:
    """Input SRS (g) at f, log-log interpolated between breakpoints.

    Outside the written band the end value is **held**, not zeroed. An SRS
    ends at its high-frequency plateau — the zero-period acceleration, which
    every stiffer mode sees — so extending it to zero the way a PSD is
    extended would silently drop the response of exactly the modes most likely
    to matter. `modes_outside_spec` reports how many modes landed there.
    """
    pts = _pts if _pts is not None else _sorted_spec(spec)
    if not pts:
        return 0.0
    if f <= pts[0][0]:
        return pts[0][1]
    if f >= pts[-1][0]:
        return pts[-1][1]
    for i in range(1, len(pts)):
        f0, w0 = pts[i - 1]
        f1, w1 = pts[i]
        if f <= f1:
            if f1 <= f0:
                return w1
            if w0 <= 0 or w1 <= 0:
                t = (f - f0) / (f1 - f0)
                return w0 + (w1 - w0) * t
            lt = math.log(f / f0) / math.log(f1 / f0)
            return math.exp(math.log(w0) + lt * (math.log(w1) - math.log(w0)))
    return pts[-1][1]


def pulse_history(kind: str, amp_g: float, dur_s: float, dt: float,
                  ring_s: float = 0.0):
    """(t, a) for a classical pulse, in g, padded with the quiet time after it.

    The padding is not optional. An oscillator softer than the pulse keeps
    ringing once the pulse ends, and that residual response is what sets the
    whole low-frequency half of the SRS. Cutting the history at the end of the
    pulse loses it.
    """
    n_p = max(int(round(dur_s / dt)), 2)
    n_r = max(int(round(ring_s / dt)), 0)
    t = np.arange(n_p + n_r + 1) * dt
    x = t / dur_s
    a = np.zeros_like(t)
    inside = x <= 1.0
    xi = x[inside]
    if kind == "half_sine":
        a[inside] = np.sin(math.pi * xi)
    elif kind == "sawtooth":
        a[inside] = xi
    elif kind == "trapezoid":
        r = TRAPEZOID_RISE
        a[inside] = np.clip(np.minimum(xi / r, (1.0 - xi) / r), 0.0, 1.0)
    else:
        raise ValueError(f"unknown pulse shape: {kind}")
    return t, a * amp_g


def srs_of_history(accel_g, dt: float, freqs, damping: float):
    """Maximax SRS of an acceleration history, in the units of `accel_g`.

    Smallwood's ramp-invariant recursive filter: the exact response of the
    oscillator to an input taken as piecewise linear between samples, which is
    what a sampled history is. It is the standard algorithm precisely because
    a naive convolution needs a far finer step to reach the same accuracy near
    the Nyquist end of the spectrum.

    `tests/test_shock.py` checks it against a direct integration of the same
    oscillator, and against the asymptotes every SRS must have.
    """
    x = np.asarray(accel_g, dtype=float)
    z = float(damping)
    out = []
    for fn in freqs:
        wn = 2.0 * math.pi * float(fn)
        wd = wn * math.sqrt(max(1.0 - z * z, 0.0))
        E = math.exp(-z * wn * dt)
        K = wd * dt
        C = E * math.cos(K)
        S = E * math.sin(K)
        Sp = S / K if K else 1.0
        b = (1.0 - Sp, 2.0 * (Sp - C), E * E - Sp)
        a = (-2.0 * C, E * E)
        y = np.zeros_like(x)
        y1 = y2 = x1 = x2 = 0.0
        for i in range(x.size):
            xi = x[i]
            yi = b[0] * xi + b[1] * x1 + b[2] * x2 - a[0] * y1 - a[1] * y2
            y[i] = yi
            x2, x1 = x1, xi
            y2, y1 = y1, yi
        out.append(float(np.abs(y).max()))
    return out


def pulse_spectrum(kind: str, amp_g: float, dur_ms: float, freqs,
                   damping: float = 0.05) -> list:
    """SRS of a classical pulse, sampled at `freqs`.

    The time step resolves the highest requested frequency, and the history
    runs on past the pulse long enough for the *lowest* one to complete
    several cycles — otherwise its residual peak has not happened yet.
    """
    dur = max(float(dur_ms), 1e-9) / 1000.0
    fs = [float(f) for f in freqs if float(f) > 0]
    if not fs:
        return []
    f_hi, f_lo = max(fs), min(fs)
    dt = min(dur / 200.0, 1.0 / (40.0 * f_hi))
    ring = max(2.0 * dur, 3.0 / f_lo)
    _, a = pulse_history(kind, float(amp_g), dur, dt, ring)
    return srs_of_history(a, dt, fs, damping)


def spectrum_for(cfg: dict, freqs) -> dict:
    """The input SRS at the given frequencies, whichever way it was specified.

    Returns {"srs": [g], "zpa": g, "source": ..., "spec": breakpoints}. `zpa`
    is the zero-period acceleration — what an infinitely stiff mode sees, and
    what the missing-mass correction is applied at.
    """
    fs = [float(f) for f in freqs]
    damping = float(cfg.get("damping", 0.05))
    if (cfg.get("input") or "spectrum") == "pulse":
        kind = cfg.get("pulse", "half_sine")
        amp = float(cfg.get("pulse_g", 20.0))
        dur = float(cfg.get("pulse_ms", 11.0))
        srs = pulse_spectrum(kind, amp, dur, fs, damping) if fs else []
        # ZPA is measured off the pulse's own spectrum, not assumed equal to
        # its amplitude. They coincide only for a pulse that starts and ends
        # at zero, and only in the limit — a trapezoid is still a fraction of
        # a percent high where a real modal basis ends.
        f_zpa = max(50.0 / (dur / 1000.0), 10.0 * max(fs)) if fs else 0.0
        zpa = (pulse_spectrum(kind, amp, dur, [f_zpa], damping)[0]
               if f_zpa else abs(amp))
        return {"srs": srs, "zpa": zpa, "source": PULSES[kind][0],
                "pulse": {"kind": kind, "amp_g": amp, "dur_ms": dur},
                "damping": damping}
    pts = _sorted_spec(cfg.get("spec") or [])
    return {"srs": [srs_at(None, f, pts) for f in fs],
            "zpa": pts[-1][1] if pts else 0.0,
            "source": "SRS table", "spec": pts, "damping": damping}


# ----------------------------------------------------------- the combination

def combine(values, rule: str = "srss") -> float:
    """Combine per-mode peaks into one peak.

    An SRS carries no phase, so the modes cannot simply be added. The three
    rules here are the ones shock work actually uses:

    * **SRSS** assumes the peaks are independent — right when the modes are
      well separated, and the usual default.
    * **NRL** takes the largest contributor at full value and SRSS's the rest.
      It is the Naval Research Laboratory rule, written for shock, and it is
      the safer choice when one mode dominates.
    * **ABS** adds magnitudes: every mode peaking at once. A true upper bound,
      and usually a very loose one.

    All three are sign-blind, which is deliberate: the sign of a participation
    factor is not recoverable from effective mass, so a rule that needed it
    (CQC, or an algebraic sum) could not be computed honestly here.
    """
    v = [abs(float(x)) for x in values if x is not None]
    if not v:
        return 0.0
    if rule == "abs":
        return float(sum(v))
    if rule == "nrl":
        top = max(v)
        rest = sum(x * x for x in v) - top * top
        return float(top + math.sqrt(max(rest, 0.0)))
    return float(math.sqrt(sum(x * x for x in v)))


def participation(m_eff: float, m_gene: float) -> float:
    """|Gamma| for one mode, from its effective and generalised mass.

        m_eff = Gamma^2 * m_gene   =>   |Gamma| = sqrt(m_eff / m_gene)

    Taken this way rather than read from a separate table because it is
    **normalisation-invariant**: the physical response is `phi * Gamma * S_d`,
    and scaling the mode shape scales m_gene by the square, so the product is
    unchanged. Whatever normalisation the solver used, this stays consistent
    with the mode shapes it wrote.

    The sign is lost, which is why `combine` offers only sign-blind rules.
    """
    if m_gene <= 0 or m_eff <= 0:
        return 0.0
    return math.sqrt(m_eff / m_gene)


def modal_table(modes, cfg: dict, total_mass: float, axis: int) -> dict:
    """Per-mode shock response for base excitation along one axis.

    `modes` is a list of {n, f, m_gene, eff} where `eff` is the unitary
    effective mass fraction in each of the three directions.

    Returns the per-mode rows plus the combined interface force, the effective
    mass the basis captured, and the missing-mass term for what it did not.
    """
    rows = []
    fs = [m["f"] for m in modes if m.get("f", 0) > 0]
    spec = spectrum_for(cfg, fs)
    rule = cfg.get("rule", "srss")

    k = 0
    for m in modes:
        f = float(m.get("f") or 0.0)
        if f <= 0:
            continue
        S_a = float(spec["srs"][k]); k += 1
        frac = float((m.get("eff") or [0, 0, 0])[axis])
        m_eff = frac * total_mass
        gamma = participation(m_eff, float(m.get("m_gene") or 0.0))
        w = 2.0 * math.pi * f
        rows.append({
            "mode": m.get("n"), "f": f,
            "eff_frac": frac, "m_eff": m_eff,
            "srs_g": S_a, "gamma": gamma,
            # peak modal coordinate: S_d = S_a / omega^2
            "q": gamma * S_a * G_MM / (w * w) if w else 0.0,
            # this mode's share of the load into the supports
            "force_N": m_eff * S_a * G_MM,
        })

    captured = sum(r["eff_frac"] for r in rows)
    missing = max(1.0 - captured, 0.0)
    # The truncated modes are stiffer than the spectrum's knee, so they move
    # with the base: their contribution is the residual mass riding at the
    # zero-period acceleration. Standard missing-mass correction; SRSS'd in
    # with the periodic terms.
    missing_force = missing * total_mass * spec["zpa"] * G_MM

    terms = [r["force_N"] for r in rows]
    return {
        "rows": rows,
        "axis": "XYZ"[axis],
        "rule": rule,
        "input": {k: v for k, v in spec.items() if k != "srs"},
        "srs_at_modes": spec["srs"],
        "force_N": combine(terms + [missing_force], rule),
        "force_modal_N": combine(terms, rule),
        "mass_captured": captured,
        "missing_mass": missing,
        "missing_force_N": missing_force,
        "total_mass_t": total_mass,
    }


# ------------------------------------------------- reading a finished run

def _blocks(meta: dict, key: str) -> list:
    return (meta.get("tables") or {}).get(key) or []


def _col(block: dict, *names):
    for n in names:
        if n in block.get("columns", []):
            return block["columns"].index(n)
    return -1


def model_mass(meta: dict) -> float:
    """Total model mass, tonnes, from the MASS_INER table.

    The effective masses are fractions of it, so without this there is no
    force anywhere in a shock result — only ratios.
    """
    for b in _blocks(meta, "tables"):
        i = _col(b, "MASSE")
        if i >= 0 and b.get("rows"):
            v = b["rows"][0][i]
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
    return 0.0


def modes_from(meta: dict) -> list:
    """[{n, f, m_gene, eff}] from the participation table.

    `eff` is code_aster's MASS_EFFE_UN_D*, the *unitary* effective mass —
    a fraction of the model's mass, not a mass. `modal_table` multiplies it
    back up, and reports the captured total so a basis that does not look
    unitary shows itself rather than quietly scaling every force.
    """
    out = []
    for b in _blocks(meta, "participation"):
        n_i = _col(b, "NUME_MODE", "NUME_ORDRE")
        f_i = _col(b, "FREQ")
        g_i = _col(b, "MASS_GENE")
        e_i = _col(b, "MASS_EFFE_UN_DX")
        if f_i < 0 or e_i < 0:
            continue
        for k, row in enumerate(b.get("rows", [])):
            num = lambda i: (float(row[i]) if 0 <= i < len(row)
                             and isinstance(row[i], (int, float)) else 0.0)
            out.append({
                "n": int(num(n_i)) if n_i >= 0 else k + 1,
                "f": num(f_i),
                "m_gene": num(g_i) or 1.0,
                "eff": [num(e_i), num(e_i + 1), num(e_i + 2)],
            })
    return out


def _per_mode(block: dict, want, label_re=None) -> dict:
    """{label: {mode: [values]}} from a POST_RELEVE_T over TOUT_ORDRE."""
    import re as _re
    cols = block.get("columns", [])
    li = _col(block, "INTITULE")
    mi = _col(block, "NUME_ORDRE", "NUME_MODE")
    idx = [cols.index(c) for c in want if c in cols]
    if mi < 0 or len(idx) != len(want):
        return {}
    out = {}
    for row in block.get("rows", []):
        label = str(row[li]) if li >= 0 else ""
        if label_re:
            m = _re.search(label_re, label)
            if not m:
                continue
            label = m.group(0)
        mode = row[mi]
        if not isinstance(mode, (int, float)):
            continue
        vals = [float(row[i]) if isinstance(row[i], (int, float)) else 0.0
                for i in idx]
        out.setdefault(label, {}).setdefault(int(mode), []).append(vals)
    # a probe is a small sphere of nodes, so average what lands in it
    return {lab: {m: [sum(c) / len(v) for c in zip(*v)] for m, v in per.items()}
            for lab, per in out.items()}


def probe_shapes(meta: dict) -> dict:
    out = {}
    for b in _blocks(meta, "mode_probes"):
        out.update(_per_mode(b, ("DX", "DY", "DZ"), r"PROBE\d+"))
    return out


def bolt_shapes(meta: dict) -> dict:
    out = {}
    for b in _blocks(meta, "mode_bolts"):
        out.update(_per_mode(b, ("N", "VY", "VZ", "MFY", "MFZ"),
                             r"BOLT\d+_[AB]"))
    return out


def response(meta: dict, cfg: dict) -> dict:
    """Everything a shock run reports, from the tables the modal deck wrote."""
    axis = int(cfg.get("axis", 2))
    rule = cfg.get("rule", "srss")
    mass = model_mass(meta)
    modes = modes_from(meta)
    warnings = []
    if not modes:
        return {"rows": [], "warnings": ["the run produced no modal "
                                         "participation table"]}
    if mass <= 0:
        warnings.append("The model mass table is missing, so interface forces "
                        "cannot be computed — only the per-mode spectrum.")

    out = modal_table(modes, {**cfg, "damping": cfg.get("damping", 0.05)},
                      mass, axis)
    out["warnings"] = warnings
    if out["mass_captured"] > 1.05:
        warnings.append(
            f"Effective masses sum to {out['mass_captured']:.2f}, which a "
            "unitary column cannot do — treat the forces below as suspect.")
    elif out["mass_captured"] < 0.9:
        warnings.append(
            f"The modal basis carries {100 * out['mass_captured']:.0f}% of the "
            "effective mass along "
            f"{out['axis']}. A shock acts through inertia, so the missing "
            "mass is added at the ZPA — extract more modes to rely on this.")

    q = {r["mode"]: r["q"] for r in out["rows"]}

    probes = []
    for label, per in sorted(probe_shapes(meta).items()):
        comps = []
        for k in range(3):
            comps.append(combine([(per.get(m) or [0, 0, 0])[k] * qi
                                  for m, qi in q.items()], rule))
        probes.append({"probe": label, "dx": comps[0], "dy": comps[1],
                       "dz": comps[2],
                       "mag": math.sqrt(sum(c * c for c in comps))})
    out["probes"] = probes

    # Combine at each bolt END and only then take the worse of the two: the
    # governing end can differ mode to mode, and picking per mode first would
    # build a bolt force out of two different places.
    bolts = {}
    for label, per in bolt_shapes(meta).items():
        idx = int(label.split("_")[0][4:])
        N = combine([(per.get(m) or [0] * 5)[0] * qi for m, qi in q.items()], rule)
        V = combine([math.hypot(*(per.get(m) or [0] * 5)[1:3]) * qi
                     for m, qi in q.items()], rule)
        M = combine([math.hypot(*(per.get(m) or [0] * 5)[3:5]) * qi
                     for m, qi in q.items()], rule)
        cur = bolts.get(idx)
        if cur is None or N > cur["N"]:
            bolts[idx] = {"bolt": idx, "end": label[-1], "N": N, "V": V, "M": M}
    out["bolts"] = [bolts[k] for k in sorted(bolts)]
    return out
