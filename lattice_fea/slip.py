"""Whether a friction joint slips — checked on a linear solve.

Why this exists
---------------
A frictional interface is nonlinear because its state is part of the answer:
whether it is stuck, sliding or open changes the stiffness. But that is only
true when the state is genuinely unknown. A designed friction joint is meant
to be **stuck** — that is the whole point of preloading it — and a stuck
frictional interface and a bonded one are the same constraint. They give
identical results.

So: solve it bonded, which is linear and fast, then read the interface
tractions back and ask whether the premise held.

    p   = -n . sigma . n           contact pressure (compression positive)
    tau = |sigma.n - (n.sigma.n) n|  shear carried across the interface
    margin = mu * p / tau           > 1 means friction was enough

Two ways the premise fails, and both are reported:

* `margin < 1` somewhere — the joint slips there. The bonded solve carried
  shear the friction cannot, so it is wrong, and the nonlinear run is the
  one to believe.
* `p <= 0` somewhere — the interface is in tension. A bonded interface pulls;
  a real one lets go. The joint is opening.

If neither happens, the linear answer is not an approximation of the
nonlinear one — it **is** the nonlinear one, and it cost one solve with no
Newton loop. If either happens, this says where and by how much, which is
what tells you whether to pay for the nonlinear run.

This is the standard way the check is done by hand; doing it from the stress
field makes it exact rather than eyeballed.
"""
from __future__ import annotations

import math

import numpy as np

# code_aster's SIGM_NOEU component order
COMPONENTS = ("SIXX", "SIYY", "SIZZ", "SIXY", "SIXZ", "SIYZ")


def tractions(sig, normal):
    """(pressure, shear) per node, from stress tensors and one unit normal.

    `sig` is (n, 6) in code_aster's SIXX, SIYY, SIZZ, SIXY, SIXZ, SIYZ order.
    Pressure is positive in compression, which is the sign convention every
    friction calculation is written in.
    """
    s = np.asarray(sig, dtype=float).reshape(-1, 6)
    n = np.asarray(normal, dtype=float)
    ln = np.linalg.norm(n)
    if ln <= 0:
        raise ValueError("interface normal is zero")
    n = n / ln
    xx, yy, zz, xy, xz, yz = (s[:, i] for i in range(6))
    # t = sigma . n, written out rather than built as matrices: this is one
    # pass over the interface nodes on every result read.
    tx = xx * n[0] + xy * n[1] + xz * n[2]
    ty = xy * n[0] + yy * n[1] + yz * n[2]
    tz = xz * n[0] + yz * n[1] + zz * n[2]
    sn = tx * n[0] + ty * n[1] + tz * n[2]          # + is tension
    shear = np.sqrt(np.maximum(
        (tx - sn * n[0]) ** 2 + (ty - sn * n[1]) ** 2 + (tz - sn * n[2]) ** 2,
        0.0))
    return -sn, shear


def margins(pressure, shear, mu: float):
    """mu*p/tau per node. Infinite where there is no shear to resist, zero
    where the interface is in tension and has no friction to give."""
    p = np.asarray(pressure, dtype=float)
    t = np.asarray(shear, dtype=float)
    cap = mu * np.maximum(p, 0.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        m = np.where(t > 0, cap / np.where(t > 0, t, 1.0), np.inf)
    return np.where(cap <= 0, 0.0, m)


def areas_for(weights: dict, nodes) -> "list|None":
    """Nodal areas for `nodes`, or None if the map does not cover them.

    code_aster names nodes "N<tag>" while the mesh's area map is keyed by the
    gmsh tag, so a literal lookup misses every node and the check quietly
    degrades to counting nodes instead of weighing area. Match on the number.
    """
    if not weights:
        return None
    out = []
    for n in nodes:
        key = str(n)
        v = weights.get(key)
        if v is None:
            v = weights.get(key.lstrip("Nn"))
        if v is None:
            return None
        out.append(v)
    return out


def check(sig, normal, mu: float, areas=None, flatness: float = 1.0) -> dict:
    """Slip and gapping over one interface.

    `areas` are nodal areas so the answer is a fraction of the interface's
    AREA rather than of its node count — a slipping patch around a bolt hole
    covers far fewer nodes than the mesh refinement there implies.
    """
    p, t = tractions(sig, normal)
    m = margins(p, t, mu)
    n = p.size
    if n == 0:
        return {"nodes": 0}
    w = np.asarray(areas, dtype=float) if areas is not None else np.ones(n)
    if w.size != n or w.sum() <= 0:
        w = np.ones(n)
    w = w / w.sum()

    open_ = p <= 0.0
    slipping = (~open_) & (m < 1.0)
    finite = np.isfinite(m)
    return {
        "nodes": int(n),
        "mu": float(mu),
        "min_margin": float(m[finite].min()) if finite.any() else float("inf"),
        "area_slipping": float(w[slipping].sum()),
        "area_open": float(w[open_].sum()),
        "p_max": float(p.max()), "p_mean": float((w * p).sum()),
        "tau_max": float(t.max()),
        "mu_required": float((t[~open_] / np.maximum(p[~open_], 1e-12)).max())
                       if (~open_).any() else float("inf"),
        "stuck": bool(not slipping.any() and not open_.any()),
        "flatness": float(flatness),
    }


def verdict(c: dict) -> "tuple[bool, str]":
    """(premise_held, one sentence). The sentence is the only prose this
    module produces, and it exists because 'stuck: false' does not tell an
    engineer which of the two failures happened."""
    if not c.get("nodes"):
        return True, "No interface stress was recovered."
    if c["area_open"] > 0 and c["area_slipping"] > 0:
        return False, (
            f"{100 * c['area_open']:.1f}% of the interface is in tension and "
            f"{100 * c['area_slipping']:.1f}% exceeds friction — the bonded "
            "solve is holding it both closed and stuck. Run it nonlinear.")
    if c["area_open"] > 0:
        return False, (
            f"{100 * c['area_open']:.1f}% of the interface is in tension. The "
            "joint is opening; a bonded solve pulls where a real one lets go. "
            "Run it nonlinear, or raise the preload.")
    if c["area_slipping"] > 0:
        return False, (
            f"{100 * c['area_slipping']:.1f}% of the interface exceeds "
            f"friction (needs mu = {c['mu_required']:.2f}, has "
            f"{c['mu']:.2f}). The joint slips. Run it nonlinear, or raise the "
            "preload until it does not.")
    return True, (
        f"Stuck and closed everywhere — worst margin {c['min_margin']:.2f}, "
        f"needs mu = {c['mu_required']:.2f} against {c['mu']:.2f}. A stuck "
        "frictional interface and a bonded one are the same constraint, so "
        "this linear result is the nonlinear one.")
