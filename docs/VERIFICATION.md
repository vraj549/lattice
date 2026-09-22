# Verification

What has been checked against an answer that is known independently of the
tool, and what has not.

Everything below is reproducible:

```bash
pytest tests/test_verification.py -v
```

Cases whose engine is not installed **skip**; they never pass quietly. The
skip list is as much a part of this document as the table.

## What is verified

Executed 21 September 2026, CalculiX 2.23, macOS arm64, tet10 elements.

| Case | Quantity | Solved | Closed form | Error |
|---|---|---|---|---|
| Bar in tension | Elongation `PL/AE` | 0.023729 mm | 0.023810 mm | 0.34 % |
| Bar in tension | Stress `P/A` | 49.9995 MPa | 50.0000 MPa | **0.001 %** |
| Cantilever | Tip deflection `PL³/3EI + PL/kAG` | −1.90454 mm | −1.91962 mm | 0.79 % |
| Cantilever | Bending stress `Mc/I` at mid-span | 299.832 MPa | 300.000 MPa | 0.06 % |
| Cantilever | First bending mode | 833.34 Hz | 835.42 Hz | 0.25 % |
| Cantilever | Refinement reduces the error | monotone | — | — |

The stress cases matter most and were the last to exist. Until this file,
every solved-answer check in the project was a displacement or a frequency —
and stress is what the tool is mostly used to read. Displacement converges
faster than stress, so a tool can be right about deflection and wrong about
the number an engineer actually acts on.

The last row is the property that makes a discretisation trustworthy, asserted
rather than assumed: refine the mesh and the error must fall. Without it a
tolerance says only that one mesh happened to land close, which a compensating
pair of errors can also do.

### Why these tolerances

- **Bar in tension** — no concentration, no singularity, no shear. Nothing can
  excuse an error, so 1 % is a real bound. The stress result lands three orders
  inside it, which is the strongest statement in this document: the recovery
  chain and the unit system (N and mm in, MPa out) are exact.
- **Cantilever deflection, 6 %** — beam theory is itself approximate here. The
  shear term is included because at L/h = 10 it is about 4 % of the answer and
  omitting it would let the tolerance hide a real error.
- **Bending stress, 5 %** — sampled at mid-span, deliberately not at the root.
  The root of a clamped cantilever is a stress singularity in a solid model:
  the value climbs with every refinement, so matching it would mean matching a
  number that does not converge.
- **First mode, 6 %** — the Euler–Bernoulli constant 1.875 ignores shear and
  rotary inertia, which a solid model includes.

## What is NOT verified

**code_aster has never been executed against a closed-form answer.**

This is the important sentence in this document. code_aster is the engine that
does bolt pretension, harmonic, random vibration and shock — which is to say,
everything CalculiX cannot do, which is to say most of the reason this tool
exists. Every test covering that path checks the **generated input text**, a
parser, or arithmetic done in Python. None checks a number the solver
returned.

A benchmark for it is written (`test_aster_uniaxial_stress_is_exact`) and runs
the same bar, against the same closed form, through the same code path the
application uses. It has not been executed, because there is no working
code_aster build for macOS — see `docs/INSTALL.md`. On a machine that has one:

```bash
pytest tests/test_verification.py -v -k aster
```

If you run it, the result belongs in this table.

Also unverified against closed form, on either engine:

| Analysis | Status |
|---|---|
| Harmonic response | Arithmetic unit-tested; no solved case against theory |
| Random vibration | Cross-checked against Miles' equation in `tests/test_random_vib.py`, which is arithmetic, not a solve |
| Shock | Combination rules unit-tested against NRC RG 1.92; no solved case |
| Bolt preload | Round trip asserted in `tests/test_bolts.py` from the deck, not from a solve |
| Contact and slip | Traction decomposition unit-tested; no solved case against theory |

None of that means those results are wrong. It means the evidence for them is
the method and its unit tests, not a solved answer compared to one obtained
another way — and the two are not the same kind of evidence.

## What verification does not cover

A verified solver answers the question it was asked correctly. It cannot tell
you the question was the right one. Every check here is **verification** — the
equations are solved right — and none is **validation**, which would compare
against a physical test.

The assumptions that a correct solve cannot rescue are in
`docs/METHODS.md` and the README's *What to know before trusting it*: linear
elastic, small displacement, small strain, no plasticity, no buckling, no
shells. A model outside those is wrong at full precision.
