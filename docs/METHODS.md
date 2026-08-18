# How Lattice computes what it shows

Reference for the numbers in the results panels: what each one is, how it is
obtained, and where it stops being valid. The panels themselves show data and
anything that needs acting on — this is the rest.

Units throughout: **mm, tonne, s** — so stress in MPa, force in N, frequency in
Hz, density in tonne/mm³.

---

## Contours

| Field | Source | Notes |
|---|---|---|
| Displacement | `DEPL` (code_aster) / `DISP` (CalculiX) | Nodal. Magnitude is √(u²+v²+w²). |
| Equivalent stress | `SIEQ_NOEU` / derived | von Mises, Tresca, principal σ1–σ3 |
| Stress tensor | `SIGM_NOEU` / `STRESS` | Six Cauchy components |

Stresses are **nodal averages**. Where several elements of different stiffness
meet, the averaged value is smoother than the underlying element values — a
peak at a re-entrant corner is mesh-dependent and will keep climbing as you
refine. Judge those against a stress-concentration factor, not against the
contour maximum.

On CalculiX the equivalent stresses are derived from the tensor by Lattice, so
von Mises means the same thing on both solvers:

```
σ_vm = √( ½[(σxx−σyy)² + (σyy−σzz)² + (σzz−σxx)²] + 3(σxy² + σyz² + σzx²) )
```

Tresca is σ₁ − σ₃ from the eigenvalues of the same tensor.

### Deformation scale

**Auto** scales peak displacement to about 5 % of the model diagonal — good for
seeing the shape, useless for judging whether a clearance closes. **True scale
(1×)** is the real deflection; the slider snaps to it.

---

## Modes

Frequencies from `CALC_MODES` (Sorensen/ARPACK) or CalculiX `*FREQUENCY`. Mode
shapes are **normalised, not physical** — the displacement magnitude of a mode
has no units you can use. Only the shape and the frequency mean anything.

### Effective mass

Per mode and direction, from the participation table. The running total tells
you how much of the structure's mass the retained modes actually represent.

**Below about 90 % in the direction you care about, a base-excited result is
low** — base excitation acts through inertia, so missing mass is missing
response. Raise `f max` to pull in more modes.

---

## Frequency response

A sine sweep by modal superposition on a basis extending to 1.6 × f max.

- **Force-driven**: response per unit input. The analysis is linear, so
  response scales exactly with the load — use 1 N and read the curve directly
  as a transfer function. Peak frequencies and Q do not depend on the
  magnitude you enter.
- **Base-driven**: every fixed support becomes the moving fixture
  (`CALC_CHAR_SEISME`, mono-support). Response is **relative to the base**,
  which is what stresses the part; the transmissibility view adds the base
  motion back.

### Peaks and Q

Peaks are refined parabolically from the three samples around each local
maximum. Q comes from the half-power (−3 dB) bandwidth:

```
Q = f_n / (f₂ − f₁)      ζ = 1/(2Q)
```

ζ should come back close to the damping you entered. If it does not, the sweep
is too coarse: a peak needs several points inside its half-power band, whose
width is roughly f/Q. Too few and **Q is under-reported and the peak amplitude
is missed** — Lattice warns when this happens.

---

## Random vibration

```
PSD_out(f) = |T(f)|² · PSD_in(f)          g_RMS = √∫ PSD_out df
```

Input is a breakpoint table in (Hz, g²/Hz), log-log interpolated between rows
and zero outside — the standard qualification-spec format. Solved as a 1 g base
sweep across the spectrum, so the transmissibility is reused rather than
depending on a separate random operator.

3σ is the conventional design peak: a Gaussian response exceeds it about 0.3 %
of the time.

### Miles' equation cross-check

```
g_RMS = √( π/2 · f_n · Q · PSD(f_n) )
```

A single-degree-of-freedom estimate per mode. Close agreement with the
integrated result means one mode dominates and the shortcut is valid; a large
gap means several modes contribute and the integrated number is the one to
trust.

### Coverage

The integral only spans the swept frequencies. A sweep narrower than the
spectrum silently under-reports g RMS, so Lattice reports what fraction of the
input the sweep actually covered.

---

## Bolt forces and stress

Beam end forces from `EFGE_ELNO`, converted on the **tensile stress area** Aₛ —
the same section the beam was given, so nothing new is assumed:

```
σ axial = N / Aₛ        (includes the preload)
τ       = V / Aₛ
σ bend  = M·c / I       on the equivalent circular section
σ eqv   = √( (σ axial + σ bend)² + 3τ² )
```

`% yield` compares σ eqv to the grade's yield stress — the comparison VDI 2230
makes against proof stress.

**A beam does not resolve the thread root, the fillet under the head, or
bending across the first engaged thread** — the actual stress concentrations.
This sizes a joint; it is not a fatigue assessment of the fastener.

Preload only does structural work if the interface can open or slip. In a
**bonded** model the parts can do neither, so the clamp load has no job — use a
frictional or frictionless contact if you want to see joint separation or slip.

---

## Reactions

Sum of nodal reactions (`REAC_NODA` / `FORC`) over the supported faces. For a
static run they should balance the applied load. A large residual means a load
did not attach to the mesh.

CalculiX runs also report the residual explicitly, plus two other checks:

| Check | Catches |
|---|---|
| Equilibrium | reactions not balancing the applied load |
| Fixed supports moved | a solution not satisfying its own boundary conditions |
| Peak displacement vs part size | linear small-displacement theory no longer applying |

These exist because a corrupted factorization can exit cleanly with a plausible
looking answer. See [SOLVERS.md](SOLVERS.md).

---

## What the model assumes

Everything Lattice solves is **linear elastic, small displacement, small
strain**. It does not model plasticity, creep, large rotation, buckling or
temperature. A result ten times too large looks exactly like a result that is
right — the arithmetic does not stop working when the physics does.

The one exception is contact: a sliding or separating interface makes the
static solve nonlinear, because whether the surfaces touch is part of the
answer. The material is still linear elastic.
