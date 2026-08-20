"""Bolted-joint sizing, after VDI 2230 Part 1.

The question this answers is the one an engineer actually has: *what preload
does this bolt need, and will it survive it?* An FE model on its own does not
answer that. It gives the working load a bolt sees; turning that into a
required assembly preload needs the joint's spring behaviour, the losses
between tightening and service, and the scatter of the tightening method.

The chain, in the standard's terms:

    delta_S, delta_P   bolt and clamped-member compliances (springs in parallel)
    Phi = dP/(dS+dP)   load factor: the share of an external axial load that
                       reaches the bolt. Everything else unloads the interface,
                       which is why preload works at all.
    F_KR               residual clamp force the joint must keep — set by
                       friction (no slip) and by not opening
    F_Z                embedding loss: surfaces bed in and preload falls
    alpha_A            tightening factor: how badly the method scatters
    F_Mmin -> F_Mmax   the assembly preload window
    F_Mzul             the most the bolt can take, tension plus the torsion
                       left in it from tightening

Every intermediate value is returned, because a sizing number you cannot
check is not worth having.

Conventions: mm, N, MPa throughout. Angles in radians internally.

Scope and limits, stated rather than buried:
  * Concentric clamping and concentric load introduction. Eccentricity
    (VDI 2230 clause 5.3.2) raises the bolt's share and can dominate a flange;
    it is not modelled here. `phi` is Phi_K in the standard's notation.
  * Static and simple alternating fatigue only.
  * The clamped-member compliance is the standard's substitute-cone model. It
    is an approximation to real flange behaviour — where an FE run has
    measured the bolt's load increase directly, `phi_from_fe` uses that
    instead, which is strictly better.
"""
from __future__ import annotations

import math

# ---------------------------------------------------------------- threads


def thread_geometry(d: float, pitch: float) -> dict:
    """ISO metric thread diameters and areas, from d and pitch.

    d2  pitch diameter        d - 0.6495 P
    d3  minor diameter        d - 1.2269 P
    As  tensile stress area   built on the mean of d2 and d3 — the definition
                              that reproduces the published table (M6 x 1 gives
                              20.1 mm^2, M8 x 1.25 gives 36.6 mm^2)
    """
    d2 = d - 0.649519 * pitch
    d3 = d - 1.226869 * pitch
    ds = 0.5 * (d2 + d3)
    return {
        "d": d, "P": pitch, "d2": d2, "d3": d3,
        "A_N": math.pi * d ** 2 / 4.0,          # nominal shank
        "A_d3": math.pi * d3 ** 2 / 4.0,        # minor-diameter area
        "A_s": math.pi * ds ** 2 / 4.0,         # tensile stress area
        "W_P": math.pi * ds ** 3 / 16.0,        # polar section modulus on d_s
    }


# ------------------------------------------------------------ compliances


def bolt_compliance(g: dict, l_K: float, E_S: float = 210000.0,
                    l_shank: float = None) -> dict:
    """Axial compliance of the bolt, mm/N. Springs in series.

    delta_SK  head                    0.5 d / (E A_N)
    delta_1   plain shank             l_1 / (E A_N)
    delta_2   free threaded length    l_2 / (E A_d3)
    delta_G   engaged thread          0.5 d / (E A_d3)
    delta_M   nut or tapped material  0.4 d / (E A_N)

    The 0.5/0.4 d substitute lengths are VDI 2230's, standing in for the
    elastic deformation of head, engaged thread and nut, which have no simple
    prismatic length of their own.
    """
    d, A_N, A_d3 = g["d"], g["A_N"], g["A_d3"]
    l_1 = l_K if l_shank is None else min(max(l_shank, 0.0), l_K)
    l_2 = max(l_K - l_1, 0.0)
    parts = {
        "delta_SK": 0.5 * d / (E_S * A_N),
        "delta_1": l_1 / (E_S * A_N),
        "delta_2": l_2 / (E_S * A_d3),
        "delta_G": 0.5 * d / (E_S * A_d3),
        "delta_M": 0.4 * d / (E_S * A_N),
    }
    parts["delta_S"] = sum(parts.values())
    return parts


def frustum_compliance(E: float, t: float, d_h: float, D: float,
                       alpha_deg: float = 30.0) -> float:
    """Compliance of one Rötscher pressure cone, mm/N.

    Load spreads from under the head into the clamped material as a cone of
    half-angle alpha. Integrating 1/(E*A(z)) along it closes to

        k = pi E d tan(a) / ln[ (2t tan(a) + D - d)(D + d)
                              / (2t tan(a) + D + d)(D - d) ]

    (Shigley, *Mechanical Engineering Design*, the standard frustum result;
    30 degrees is the conventional half-angle.)

    This replaced a three-case substitute-area model that disagreed with
    itself by 40 % across its own case boundary — the same joint came out
    materially stiffer or softer depending on which branch it fell in. One
    continuous expression is worth more than three whose agreement cannot be
    checked.
    """
    tan_a = math.tan(math.radians(alpha_deg))
    D = max(D, d_h * 1.01)
    num = (2.0 * t * tan_a + D - d_h) * (D + d_h)
    den = (2.0 * t * tan_a + D + d_h) * (D - d_h)
    if num <= 0 or den <= 0 or num / den <= 1.0:
        # degenerate geometry: fall back to a plain sleeve of the same span
        A = math.pi / 4.0 * (D ** 2 - d_h ** 2)
        return t / (E * A)
    k = math.pi * E * d_h * tan_a / math.log(num / den)
    return 1.0 / k


def member_compliance(g: dict, l_K: float, d_h: float, d_W: float,
                      D_A: float, E_P: float = 210000.0,
                      tapped: bool = False, alpha_deg: float = 30.0) -> dict:
    """Compliance of the clamped members, mm/N.

    Two pressure cones in series, one from each end of the grip, each half the
    clamped length. `D_A` is how much material is actually available sideways:
    where the cone would grow past it, the joint behaves as a sleeve of that
    diameter instead, so the cone diameter is capped there.
    """
    d_h = max(d_h, g["d"] * 1.02)
    d_W = max(d_W, d_h * 1.05)
    tan_a = math.tan(math.radians(alpha_deg))
    t = l_K / 2.0

    # diameter the cone would reach at the mid-plane, and what is available
    D_cone = d_W + 2.0 * t * tan_a
    limited = D_A < D_cone
    if limited:
        # cut off by the free edge: a sleeve of the available diameter, in
        # series with the cone that does fit
        t_cone = max((D_A - d_W) / (2.0 * tan_a), 0.0)
        t_sleeve = max(t - t_cone, 0.0)
        A_sleeve = math.pi / 4.0 * max(D_A ** 2 - d_h ** 2, 1e-9)
        d_one = (frustum_compliance(E_P, t_cone, d_h, d_W, alpha_deg)
                 if t_cone > 0 else 0.0) + t_sleeve / (E_P * A_sleeve)
        case = "cone limited by the free edge"
    else:
        d_one = frustum_compliance(E_P, t, d_h, d_W, alpha_deg)
        case = "full cone"

    delta_P = 2.0 * d_one                  # two cones, one per end, in series
    A_ers = l_K / (E_P * delta_P)          # equivalent prismatic area
    return {"delta_P": delta_P, "A_ers": A_ers, "tan_phi": tan_a,
            "D_cone": D_cone, "case": case, "w": 2.0 if tapped else 1.0}


def load_factor(delta_S: float, delta_P: float, n: float = 0.5) -> float:
    """Phi_n = n * delta_P / (delta_S + delta_P).

    `n` is the load-introduction factor: where the external load actually
    enters the clamped parts, relative to the bolt head. Load introduced deep
    inside the joint (n < 1) reaches the bolt less. n = 1 means it enters right
    under the head, which is the conservative extreme; 0.5 is the usual default
    for a flange-like joint.
    """
    return n * delta_P / (delta_S + delta_P)


# Defaults for every assumption the sizing needs. Merged over whatever a
# project carries, so a model saved before an assumption existed still gets a
# complete, editable set rather than blank fields.
DEFAULT_ASSUMPTIONS = {
    "mu_joint": 0.15, "n_friction": 1,
    "mu_thread": 0.14, "mu_head": 0.14,
    "tightening": "torque_wrench", "embedding_um": 6.0,
    "n_intro": 0.5, "S_slip": 1.2, "S_gap": 1.2,
    "nu_yield": 0.9, "p_G": None,
}


def assumptions(cfg: dict = None) -> dict:
    """A complete assumption set, from a possibly partial one."""
    out = dict(DEFAULT_ASSUMPTIONS)
    for k, v in (cfg or {}).items():
        if k in out and v is not None:
            out[k] = v
        elif k in out and k == "p_G":
            out[k] = v
    return out


# ------------------------------------------------------------- the sizing

# Tightening factor alpha_A: the ratio of maximum to minimum assembly preload
# a method delivers. VDI 2230 Table A8, representative values.
TIGHTENING = {
    "torque_wrench": (1.6, "torque wrench, unlubricated — wide scatter"),
    "torque_lubricated": (1.4, "torque wrench, controlled lubrication"),
    "angle_control": (1.2, "torque + angle beyond yield-free"),
    "yield_control": (1.1, "yield-point controlled"),
    "bolt_elongation": (1.1, "measured bolt elongation / ultrasonic"),
}


def size_bolt(*, d: float, pitch: float, l_K: float, A_s: float = None,
              R_p02: float = 640.0, E_S: float = 210000.0,
              E_P: float = 210000.0, d_h: float = None, d_W: float = None,
              D_A: float = None, tapped: bool = False,
              F_A: float = 0.0, F_Q: float = 0.0, M_b: float = 0.0,
              mu_joint: float = 0.15, n_friction: int = 1,
              mu_thread: float = 0.14, mu_head: float = 0.14,
              n_intro: float = 0.5,
              tightening: str = "torque_wrench",
              embedding_um: float = 6.0,
              nu_yield: float = 0.9,
              S_slip: float = 1.2, S_gap: float = 1.2,
              p_G: float = None,
              phi_from_fe: float = None) -> dict:
    """Required preload and resulting stresses for one bolt.

    Inputs are the bolt, the joint it clamps, and the working loads the FE
    model reports for it. Everything the calculation uses on the way is
    returned alongside the answer.
    """
    g = thread_geometry(d, pitch)
    if A_s:
        g["A_s"] = A_s                          # trust the table over the formula
        ds = math.sqrt(4.0 * A_s / math.pi)
        g["W_P"] = math.pi * ds ** 3 / 16.0
    d_h = d_h if d_h else d * 1.1               # medium clearance hole
    d_W = d_W if d_W else d * 1.5               # head bearing outer diameter
    D_A = D_A if D_A else d * 3.0               # material available sideways

    bolt = bolt_compliance(g, l_K, E_S)
    mem = member_compliance(g, l_K, d_h, d_W, D_A, E_P, tapped)
    dS, dP = bolt["delta_S"], mem["delta_P"]

    phi = load_factor(dS, dP, n_intro)
    phi_source = "VDI cone model"
    if phi_from_fe is not None and 0.0 <= phi_from_fe <= 1.0:
        phi = phi_from_fe
        phi_source = "measured from the FE run"

    # --- clamp force the joint must retain ---------------------------------
    # friction has to carry the transverse load, or the joint slips
    F_K_slip = (S_slip * abs(F_Q) / (mu_joint * max(n_friction, 1))
                if F_Q else 0.0)
    # and the interface must not open under the tensile part of the load
    F_K_gap = S_gap * (1.0 - phi) * max(F_A, 0.0) if F_A > 0 else 0.0
    F_KR = max(F_K_slip, F_K_gap)

    # --- losses between tightening and service -----------------------------
    # embedding: asperities flatten, and the joint relaxes by that much
    F_Z = (embedding_um * 1e-3) / (dS + dP)

    # --- the preload window ------------------------------------------------
    F_Mmin = F_KR + (1.0 - phi) * max(F_A, 0.0) + F_Z
    alpha_A, tighten_note = TIGHTENING.get(tightening, TIGHTENING["torque_wrench"])
    F_Mmax = alpha_A * F_Mmin

    # --- what the bolt can take -------------------------------------------
    # Tightening leaves torsion in the shank; the usable tensile capacity is
    # what is left of yield after that. VDI 2230 eq. (5.4-1).
    d2, d3, A_s_ = g["d2"], g["d3"], g["A_s"]
    ds_eff = math.sqrt(4.0 * A_s_ / math.pi)
    k_tau = 3.0 / 2.0 * (d2 / ds_eff) * (pitch / (math.pi * d2)
                                         + 1.155 * mu_thread)
    F_Mzul = nu_yield * A_s_ * R_p02 / math.sqrt(1.0 + 3.0 * k_tau ** 2)

    # --- resulting bolt loads and stresses ---------------------------------
    F_Smax = F_Mmax + phi * max(F_A, 0.0)
    sigma_M = F_Mmax / A_s_
    tau_M = (F_Mmax * 0.5 * d2 * (pitch / (math.pi * d2) + 1.155 * mu_thread)
             / g["W_P"])
    sigma_red_M = math.hypot(sigma_M, math.sqrt(3.0) * tau_M)
    sigma_z = F_Smax / A_s_
    sigma_b = (abs(M_b) * ds_eff / 2.0) / (math.pi * ds_eff ** 4 / 64.0) if M_b else 0.0
    sigma_red_B = math.sqrt((sigma_z + sigma_b) ** 2 + 3.0 * tau_M ** 2)

    # --- tightening torque -------------------------------------------------
    D_Km = 0.5 * (d_W + d_h)                    # mean head friction diameter
    M_A = F_Mmax * (0.16 * pitch + 0.58 * d2 * mu_thread + 0.5 * D_Km * mu_head)

    # --- surface pressure under the head ----------------------------------
    A_p = math.pi / 4.0 * (d_W ** 2 - d_h ** 2)
    p_max = F_Mmax / A_p if A_p > 0 else float("inf")

    # --- residual clamp in service ----------------------------------------
    F_Kmin_service = F_Mmin - (1.0 - phi) * max(F_A, 0.0) - F_Z
    slip_margin = (mu_joint * max(n_friction, 1) * F_Kmin_service / abs(F_Q)
                   if F_Q else None)

    feasible = F_Mmax <= F_Mzul
    checks = []
    if not feasible:
        checks.append(
            f"No feasible preload: the joint needs {F_Mmax:.0f} N at assembly "
            f"but the bolt can only take {F_Mzul:.0f} N. Use a larger or "
            f"stronger bolt, more bolts, or a tightening method with less "
            f"scatter than {alpha_A:.1f}x.")
    if p_G is not None and p_max > p_G:
        checks.append(
            f"Surface pressure under the head is {p_max:.0f} MPa against a "
            f"limit of {p_G:.0f} MPa — the clamped material yields before the "
            f"bolt does. Use a washer, a flanged head, or a larger bolt.")
    if slip_margin is not None and slip_margin < 1.0:
        checks.append(
            f"The joint slips: friction carries {slip_margin:.2f} of the "
            f"transverse load. Raise the preload, add bolts, or do not rely "
            f"on friction (dowel or fitted bolt).")

    return {
        "geometry": g, "bolt": bolt, "member": mem,
        "phi": phi, "phi_source": phi_source,
        "F_K_slip": F_K_slip, "F_K_gap": F_K_gap, "F_KR": F_KR, "F_Z": F_Z,
        "alpha_A": alpha_A, "tightening_note": tighten_note,
        "F_Mmin": F_Mmin, "F_Mmax": F_Mmax, "F_Mzul": F_Mzul,
        "F_Smax": F_Smax, "F_Kmin_service": F_Kmin_service,
        "sigma_M": sigma_M, "tau_M": tau_M, "sigma_red_M": sigma_red_M,
        "sigma_red_B": sigma_red_B, "utilisation": sigma_red_B / R_p02,
        "M_A_Nm": M_A / 1000.0, "p_max": p_max, "A_p": A_p,
        "slip_margin": slip_margin, "feasible": feasible, "checks": checks,
    }


# ------------------------------------------------------- driving it from FE

def joint_inputs_from_model(bolt: dict, record: dict, setup: dict,
                            meta: dict) -> dict:
    """Everything the sizing needs that the model already knows.

    Grip length comes from the meshed bolt itself — the beam spans exactly the
    clamped length, so it is measured rather than typed. Hole diameter comes
    from the detected cylinder. What the model cannot know (head bearing
    diameter, how much material is available sideways, friction) falls back to
    defaults the user can override.
    """
    faces = {f["tag"]: f for f in meta.get("faces", [])}
    d = float(bolt.get("d_mm") or 8.0)

    # hole diameter: the detected cylinder this bolt is attached to
    d_h = None
    for t in list(bolt.get("side_a_faces", [])) + list(bolt.get("side_b_faces", [])):
        fit = (faces.get(int(t)) or {}).get("fit") or {}
        if fit.get("kind") == "cylinder":
            d_h = 2.0 * float(fit["radius"])
            break

    # clamped material: the stiffest solid the bolt touches is a fair E_P
    E_P = None
    mats = {m["id"]: m for m in setup.get("materials", [])}
    for t in list(bolt.get("side_a_faces", [])) + list(bolt.get("side_b_faces", [])):
        for sd in (faces.get(int(t)) or {}).get("solids", []):
            mid = setup.get("assignments", {}).get(str(sd))
            if mid and mid in mats:
                E_P = max(E_P or 0.0, float(mats[mid]["E_GPa"]) * 1000.0)

    return {
        "d": d,
        "pitch": _pitch_for(d, bolt.get("size")),
        "l_K": float(record.get("length") or 3.0 * d),
        "A_s": float(bolt.get("as_mm2") or 0.0) or None,
        "R_p02": float(bolt.get("yield_MPa") or 640.0),
        "E_S": float(bolt.get("E_GPa") or 210.0) * 1000.0,
        "E_P": E_P or 210000.0,
        "d_h": d_h or d * 1.1,
        "d_W": float(bolt.get("d_W_mm") or 0.0) or d * 1.5,
        "D_A": float(bolt.get("D_A_mm") or 0.0) or d * 3.0,
        "tapped": bool(bolt.get("tapped")),
    }


# ISO metric coarse pitches, by nominal diameter.
_COARSE = {1.6: 0.35, 2.0: 0.4, 2.5: 0.45, 3.0: 0.5, 4.0: 0.7, 5.0: 0.8,
           6.0: 1.0, 8.0: 1.25, 10.0: 1.5, 12.0: 1.75}
# Unified inch sizes carry threads per inch, not a metric pitch.
_UNIFIED_TPI = {"0-80": 80, "2-56": 56, "4-40": 40, "6-32": 32}


def _pitch_for(d: float, size_id: str = None) -> float:
    if size_id in _UNIFIED_TPI:
        return 25.4 / _UNIFIED_TPI[size_id]
    best = min(_COARSE, key=lambda k: abs(k - d))
    return _COARSE[best] if abs(best - d) < 0.3 else 0.15 * d


def size_from_run(bolt: dict, record: dict, setup: dict, meta: dict,
                  F_A: float, F_Q: float, M_b: float = 0.0,
                  **overrides) -> dict:
    """Size one bolt from the loads its FE run reported.

    F_A and F_Q are that bolt's share of the external load, read straight off
    the beam. Run the model with **preload set to zero** to get them: with no
    preload the beam carries exactly the load path's share, which is the
    external load the joint has to be preloaded against. Running with a
    preload already applied gives the bolt force, not the external load, and
    feeding that back in would double-count it.
    """
    args = joint_inputs_from_model(bolt, record, setup, meta)
    args.update({k: v for k, v in overrides.items() if v is not None})
    args.update({"F_A": F_A, "F_Q": F_Q, "M_b": M_b})
    return size_bolt(**args)
