"""Bolt preload calibration.

The problem
-----------
`PRE_EPSI` (and every equivalent in every other solver: initial strain,
fictitious thermal contraction, `*INITIAL CONDITIONS,TYPE=STRESS`) imposes a
*shortening* on the bolt, not a force. The bolt is a spring in parallel with
the clamped parts. Whatever the bolt pulls in, the parts push back, and the
shank settles at

    F_achieved = F_requested * delta_S / (delta_S + delta_P)

`delta_S/(delta_S+delta_P)` is `1 - Phi`, the complement of the VDI 2230 load
factor. For a normal steel joint that is 0.83-0.85 and for an aluminium one
0.61-0.66: ask for 10 kN and the model is clamped with 8.4 kN, or 6.3 kN.

That error lands squarely on the number this tool exists to produce. A sizing
calculation says "preload each bolt to 9.2 kN"; the verification run has to
actually contain 9.2 kN of preload, or the margins it reports are fiction.

The fix
-------
Measure it. Solve the joint with preload alone, read the axial force the beams
actually ended up with, scale the imposed strain by requested/achieved, and
solve again. The response is linear in the imposed strain, so for a single
bolt one correction is exact; several bolts share a joint and so couple, which
makes the componentwise correction a Jacobi step rather than an exact one, and
it needs a second pass to settle.

Deliberately, none of this happens inside the .comm. The correction is applied
by rewriting the deck between solves, so the solver input uses no command,
keyword or Python-API call it did not already use. A calibration loop written
into the .comm would have been cheaper by one file write and would have put
new, unexercised solver API on the critical path of every bolted run.
"""

MAX_SCALE = 10.0
MIN_RESPONSE = 0.05


class CalibrationFailed(Exception):
    """Calibration cannot produce a meaningful correction for this model."""


def calibrate(targets: dict, solve, tol: float = 0.01,
              max_passes: int = 3) -> dict:
    """Find the strain scale that puts `targets` into the bolts.

    `targets`   {bolt index: requested preload, N}
    `solve`     solve(scale) -> {bolt index: achieved axial force, N};
                called once per pass, with `scale` keyed the same as targets.

    Returns {"scale", "achieved", "passes", "max_error"} — `achieved` is
    always a real measurement of the returned `scale`, never an extrapolation,
    so what is reported is what the run will contain.

    The first correction is proportional, which is exact for a single bolt.
    After that there are two measurements on the path and the response is
    linear in the imposed strain, so a componentwise secant step uses both; on
    a tightly coupled pattern that is worth a whole extra solve. Residual
    error measured on a six-bolt joint after three passes:

        coupling kC/kP    0.05    0.2     0.8     1.5
        proportional     0.03%   0.48%   1.99%   2.66%
        secant           0.02%   0.25%   0.76%   0.95%

    Raises CalibrationFailed when the measurement cannot be trusted, which is
    the honest outcome for a bolt the model does not react (nothing clamped
    between the faces, or a part free to follow the bolt). The caller should
    fall back to the uncorrected strain and say so rather than shipping a
    wild correction.
    """
    if not targets:
        return {"scale": {}, "achieved": {}, "passes": 0, "max_error": 0.0}

    scale = {i: 1.0 for i in targets}
    prev = None
    for p in range(max(1, max_passes)):
        achieved = solve(dict(scale))
        missing = [i for i in targets if i not in achieved]
        if missing:
            raise CalibrationFailed(
                f"the calibration run reported no force for bolt(s) {missing}")

        worst = max(abs(achieved[i] / targets[i] - 1.0) for i in targets)
        if worst <= tol or p == max(1, max_passes) - 1:
            return {"scale": scale, "achieved": achieved,
                    "passes": p + 1, "max_error": worst}

        # A bolt that barely responds is not a stiff-joint effect, it is a
        # modelling error, and scaling by requested/achieved would turn it
        # into an enormous fictitious preload.
        for i, F in targets.items():
            if achieved[i] < MIN_RESPONSE * F:
                raise CalibrationFailed(
                    f"bolt {i} carries {achieved[i]:.3g} N of the {F:.6g} N "
                    "asked for — the model does not react it")

        nxt = {}
        for i, F in targets.items():
            step = None
            if prev is not None:
                ds = scale[i] - prev[0][i]
                dF = achieved[i] - prev[1][i]
                if abs(ds) > 1e-12 and abs(dF) > 1e-9 * F:
                    step = scale[i] + (F - achieved[i]) * ds / dF
            if step is None or not (0.0 < step <= MAX_SCALE):
                step = scale[i] * F / achieved[i]      # proportional fallback
            nxt[i] = min(max(step, 1e-3), MAX_SCALE)
        prev, scale = (scale, achieved), nxt
    raise AssertionError("unreachable")  # pragma: no cover
