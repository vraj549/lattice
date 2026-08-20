"""Bolted-joint sizing: identities that must hold, not numbers I remember.

The absolute preload a standard tabulates depends on choices — which
cross-section governs, what utilisation, which friction pair — that differ
between references. So these test the physics and the internal consistency,
which are checkable here, and the stress areas against the published thread
table, which is unambiguous.
"""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import bolt_sizing as BS  # noqa: E402


# ------------------------------------------------------------- geometry

@pytest.mark.parametrize("d,p,As", [
    (1.6, 0.35, 1.27), (2.0, 0.40, 2.07), (2.5, 0.45, 3.39), (3.0, 0.50, 5.03),
    (4.0, 0.70, 8.78), (5.0, 0.80, 14.2), (6.0, 1.00, 20.1), (8.0, 1.25, 36.6),
])
def test_stress_area_matches_the_thread_table(d, p, As):
    """A_s from d and pitch must reproduce ISO 898-1, or every stress derived
    from it is wrong by the same factor."""
    g = BS.thread_geometry(d, p)
    assert abs(g["A_s"] - As) / As < 0.005


def test_minor_diameter_is_below_pitch_diameter_is_below_nominal():
    g = BS.thread_geometry(8.0, 1.25)
    assert g["d3"] < g["d2"] < g["d"]
    assert g["A_d3"] < g["A_s"] < g["A_N"]


# ---------------------------------------------------------- compliances

def test_bolt_compliance_is_springs_in_series():
    """delta_S is a sum of l/(EA) terms; doubling the grip must add exactly
    the shank term and nothing else."""
    g = BS.thread_geometry(8.0, 1.25)
    a = BS.bolt_compliance(g, l_K=20.0)
    b = BS.bolt_compliance(g, l_K=40.0)
    added = b["delta_S"] - a["delta_S"]
    assert abs(added - 20.0 / (210000.0 * g["A_N"])) / added < 1e-9
    assert abs(a["delta_S"] - sum(v for k, v in a.items() if k != "delta_S")) < 1e-18


def test_a_longer_bolt_is_more_compliant_and_a_stiffer_one_less():
    g = BS.thread_geometry(6.0, 1.0)
    assert BS.bolt_compliance(g, 30.0)["delta_S"] > BS.bolt_compliance(g, 10.0)["delta_S"]
    steel = BS.bolt_compliance(g, 20.0, E_S=210000.0)["delta_S"]
    ti = BS.bolt_compliance(g, 20.0, E_S=114000.0)["delta_S"]
    assert ti > steel


def test_member_compliance_grows_with_grip_and_falls_with_available_material():
    g = BS.thread_geometry(8.0, 1.25)
    thin = BS.member_compliance(g, 10.0, 9.0, 12.0, 30.0)["delta_P"]
    thick = BS.member_compliance(g, 40.0, 9.0, 12.0, 30.0)["delta_P"]
    assert thick > thin, "a longer clamped length is more compliant"
    narrow = BS.member_compliance(g, 20.0, 9.0, 12.0, 13.0)["delta_P"]
    wide = BS.member_compliance(g, 20.0, 9.0, 12.0, 60.0)["delta_P"]
    assert narrow > wide, "more material sideways is stiffer"


def test_members_are_much_stiffer_than_the_bolt_in_a_metal_joint():
    """This is the whole reason preload works: the clamped metal is an order
    of magnitude stiffer than the bolt, so an external load mostly unloads the
    interface instead of reaching the bolt."""
    g = BS.thread_geometry(8.0, 1.25)
    dS = BS.bolt_compliance(g, 20.0)["delta_S"]
    dP = BS.member_compliance(g, 20.0, 9.0, 12.0, 30.0)["delta_P"]
    assert dP < dS / 3.0
    phi = BS.load_factor(dS, dP, n=1.0)
    assert 0.05 < phi < 0.35, f"load factor {phi:.3f} is outside the metal-joint range"


def test_load_factor_limits():
    """Phi is a ratio of compliances and must behave at the extremes."""
    assert BS.load_factor(1.0, 0.0, n=1.0) == 0.0          # rigid members
    assert BS.load_factor(1e-12, 1.0, n=1.0) > 0.99        # rigid bolt
    assert BS.load_factor(1.0, 1.0, n=0.5) == pytest.approx(0.25)


# ------------------------------------------------------------ the sizing

def base(**kw):
    args = dict(d=8.0, pitch=1.25, l_K=20.0, A_s=36.6, R_p02=640.0,
                d_h=9.0, d_W=12.0, D_A=30.0)
    args.update(kw)
    return BS.size_bolt(**args)


def test_a_joint_with_no_load_still_needs_preload_for_embedding():
    r = base(F_A=0.0, F_Q=0.0)
    assert r["F_KR"] == 0.0
    assert r["F_Mmin"] == pytest.approx(r["F_Z"])
    assert r["F_Z"] > 0, "surfaces bed in; that loss has to be preloaded out"


def test_transverse_load_sets_the_preload_through_friction():
    """A shear-loaded joint is held by friction, not by the bolt in shear.
    Required clamp force must scale as F_Q / mu."""
    a = base(F_Q=2000.0, mu_joint=0.10)
    b = base(F_Q=2000.0, mu_joint=0.20)
    assert a["F_K_slip"] == pytest.approx(2 * b["F_K_slip"])
    assert a["F_KR"] > b["F_KR"]
    two = base(F_Q=2000.0, mu_joint=0.10, n_friction=2)
    assert two["F_K_slip"] == pytest.approx(a["F_K_slip"] / 2)


def test_axial_load_splits_between_bolt_and_interface():
    """F_A = phi*F_A into the bolt + (1-phi)*F_A off the interface. The clamp
    force lost is what the preload has to cover to keep the joint shut."""
    F_A = 5000.0
    r = base(F_A=F_A, S_gap=1.0)
    phi = r["phi"]
    assert r["F_K_gap"] == pytest.approx((1 - phi) * F_A)
    bolt_share = phi * F_A
    interface_share = (1 - phi) * F_A
    assert bolt_share + interface_share == pytest.approx(F_A)


def test_tightening_scatter_widens_the_required_preload():
    """A worse tightening method needs a higher maximum to guarantee the same
    minimum — which is what eats the bolt's capacity."""
    good = base(F_Q=3000.0, tightening="yield_control")
    poor = base(F_Q=3000.0, tightening="torque_wrench")
    assert good["F_Mmin"] == pytest.approx(poor["F_Mmin"])
    assert poor["F_Mmax"] > good["F_Mmax"]
    assert poor["alpha_A"] > good["alpha_A"]


def test_torsion_from_tightening_reduces_the_usable_preload():
    """Tightening leaves the shank twisted; less tension is then available
    before yield. Lubricated threads recover most of it."""
    dry = base(mu_thread=0.20)["F_Mzul"]
    lub = base(mu_thread=0.08)["F_Mzul"]
    frictionless = base(mu_thread=0.0)["F_Mzul"]
    assert dry < lub < frictionless
    # with no thread friction, only the pitch term twists the bolt
    g = BS.thread_geometry(8.0, 1.25)
    assert frictionless < 0.9 * 36.6 * 640.0


def test_an_impossible_joint_is_reported_not_rounded_off():
    """If the required preload exceeds what the bolt can take, that is the
    answer — a smaller number would be a lie."""
    r = base(F_Q=60000.0, mu_joint=0.10)
    assert not r["feasible"]
    assert any("No feasible preload" in c for c in r["checks"])
    assert r["F_Mmax"] > r["F_Mzul"]


def test_surface_pressure_under_the_head_is_checked():
    """On aluminium or a polymer the clamped material gives up first — this is
    frequently what actually limits the preload."""
    r = base(F_Q=8000.0, p_G=120.0)          # 120 MPa: a soft alloy
    assert r["p_max"] > 0
    if r["p_max"] > 120.0:
        assert any("Surface pressure" in c for c in r["checks"])


def test_slip_margin_is_reported_and_consistent():
    r = base(F_Q=1500.0, mu_joint=0.15, S_slip=1.0)
    assert r["slip_margin"] == pytest.approx(
        0.15 * r["F_Kmin_service"] / 1500.0)
    assert r["slip_margin"] >= 1.0, "sized for no slip, so it must not slip"


def test_fe_measured_load_factor_overrides_the_cone_estimate():
    """The cone model approximates the flange; an FE run measures it."""
    est = base(F_A=4000.0)
    fe = base(F_A=4000.0, phi_from_fe=0.42)
    assert fe["phi"] == 0.42
    assert "FE" in fe["phi_source"]
    assert fe["F_K_gap"] < est["F_K_gap"], "a stiffer bolt unloads the joint less"


def test_tightening_torque_scales_with_preload_and_friction():
    a = base(F_Q=2000.0, mu_thread=0.10, mu_head=0.10)
    b = base(F_Q=2000.0, mu_thread=0.20, mu_head=0.20)
    assert b["M_A_Nm"] > a["M_A_Nm"]
    assert a["M_A_Nm"] > 0


# ------------------------------------------------- driving it from a run

def test_sizing_refuses_a_preloaded_run():
    """With preload applied the beam force is the BOLT force, not the external
    load. Feeding it back in counts the preload twice and asks for several
    times the preload the joint needs — so it is refused, not tabulated."""
    from lattice_fea import server  # noqa: F401  (import cost only)
    import inspect
    src = inspect.getsource(server.create_app)
    assert '"blocked": True' in src
    assert "preload is applied" in src or "has preload applied" in src


def test_grip_length_comes_from_the_mesh():
    """The beam spans exactly the clamped length, so the grip is measured
    rather than typed — and it is the single biggest driver of compliance."""
    bolt = {"d_mm": 6.0, "size": "M6", "as_mm2": 20.1, "yield_MPa": 640,
            "E_GPa": 210, "side_a_faces": [7], "side_b_faces": [7]}
    setup = {"materials": [{"id": "s", "E_GPa": 210}], "assignments": {"1": "s"}}
    meta = {"faces": [{"tag": 7, "fit": {"kind": "cylinder", "radius": 3.3},
                       "solids": [1]}]}
    short = BS.size_from_run(bolt, {"length": 8.0}, setup, meta, F_A=0, F_Q=1000)
    long = BS.size_from_run(bolt, {"length": 40.0}, setup, meta, F_A=0, F_Q=1000)
    assert long["bolt"]["delta_S"] > short["bolt"]["delta_S"]
    # a longer grip is a softer bolt, so it takes less of an external load
    assert long["phi"] < short["phi"]


def test_hole_diameter_is_taken_from_the_detected_cylinder():
    bolt = {"d_mm": 6.0, "size": "M6", "side_a_faces": [7], "side_b_faces": []}
    setup = {"materials": [], "assignments": {}}
    meta = {"faces": [{"tag": 7, "fit": {"kind": "cylinder", "radius": 3.3},
                       "solids": [1]}]}
    args = BS.joint_inputs_from_model(bolt, {"length": 12.0}, setup, meta)
    assert args["d_h"] == pytest.approx(6.6)
    assert args["l_K"] == 12.0


def test_unified_sizes_get_an_inch_pitch():
    """#4-40 is 40 threads per inch, not a metric pitch."""
    assert BS._pitch_for(2.845, "4-40") == pytest.approx(25.4 / 40)
    assert BS._pitch_for(6.0, "M6") == pytest.approx(1.0)


def test_the_governing_criterion_is_identifiable():
    """An engineer needs to know WHY the preload is what it is — a shear joint
    and a tension joint are sized by different things."""
    shear = base(F_A=0.0, F_Q=5000.0, mu_joint=0.12)
    assert shear["F_K_slip"] > shear["F_K_gap"]
    tension = base(F_A=5000.0, F_Q=0.0)
    assert tension["F_K_gap"] > tension["F_K_slip"]


# ------------------------------------------------- the member model itself

def test_member_compliance_is_continuous_across_the_cutoff():
    """The previous three-case substitute-area model disagreed with itself by
    40 % across its own case boundary, so the same joint came out materially
    stiffer or softer depending which branch it fell in.

    Continuity is tested the way continuity is defined: refine the step and
    the largest jump must shrink with it. A fixed tolerance would only be
    testing that the function is not steep, which is a different thing — near
    the cutoff it legitimately is.
    """
    g = BS.thread_geometry(8.0, 1.25)

    def worst_jump(step):
        prev, worst = None, 0.0
        D_A = 13.0
        while D_A <= 28.0:
            dP = BS.member_compliance(g, 20.0, 9.0, 12.0, D_A)["delta_P"]
            if prev is not None:
                worst = max(worst, abs(dP - prev) / prev)
            prev, D_A = dP, D_A + step
        return worst

    # A continuous function with a bounded derivative shows a jump that
    # falls in proportion to the step. A discontinuity shows a jump that
    # stops falling, because it is a property of the function, not the grid.
    jumps = [worst_jump(h) for h in (0.5, 0.25, 0.125, 0.0625)]
    for a, b in zip(jumps, jumps[1:]):
        assert b < 0.62 * a, (
            f"halving the step took the largest jump from {a:.4f} to {b:.4f} — "
            "it is not converging, so there is a discontinuity")


def test_material_beyond_the_cone_does_nothing():
    """Once the pressure cone fits, a wider flange cannot stiffen the joint."""
    g = BS.thread_geometry(8.0, 1.25)
    a = BS.member_compliance(g, 20.0, 9.0, 12.0, 40.0)
    b = BS.member_compliance(g, 20.0, 9.0, 12.0, 200.0)
    assert a["delta_P"] == pytest.approx(b["delta_P"], rel=1e-12)
    assert a["case"] == "full cone"


def test_a_longer_bolt_takes_less_of_the_external_load():
    """The reason a fatigue-loaded joint wants a long bolt: more compliance in
    the bolt, less of the alternating load reaching it."""
    g = BS.thread_geometry(6.0, 1.0)
    def phi(L):
        return BS.load_factor(BS.bolt_compliance(g, L)["delta_S"],
                              BS.member_compliance(g, L, 6.6, 9.0, 18.0)["delta_P"],
                              n=1.0)
    assert phi(10.0) > phi(30.0) > phi(50.0)


def test_assumptions_are_completed_for_older_projects():
    """A model saved before an assumption existed must still get a complete,
    editable set — not blank fields that silently size as zero."""
    full = BS.assumptions({})
    assert set(full) == set(BS.DEFAULT_ASSUMPTIONS)
    assert full["mu_joint"] == 0.15 and full["tightening"] == "torque_wrench"
    partial = BS.assumptions({"mu_joint": 0.30})
    assert partial["mu_joint"] == 0.30
    assert partial["n_friction"] == BS.DEFAULT_ASSUMPTIONS["n_friction"]
    # every key must be accepted by the calculator itself
    r = BS.size_bolt(d=8.0, pitch=1.25, l_K=20.0, F_Q=1000.0, **full)
    assert r["F_Mmin"] > 0
