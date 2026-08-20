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

## Bolted joints — sizing and preload

### The question, and what the FE can answer

An FE model does not tell you what preload a bolt needs. It tells you the
**load each bolt carries**; turning that into a required preload needs the
joint's spring behaviour, the losses between tightening and service, and the
scatter of the tightening method. Lattice does both halves, and keeps them
separate so you can see where each number came from.

### Workflow

1. **Model the joint with preload set to zero** and run the working load.
   With no preload the bolt beam carries exactly its share of the load path,
   which is the external load `F_A` (axial) and `F_Q` (transverse) the joint
   must be preloaded *against*.
2. Open **Solution → Bolt sizing**. It reports the required preload per bolt.
3. **Enter that preload and run again** to verify — ideally with the assembly
   imported as separate parts and a frictional contact, so the interface can
   actually open or slip if the preload is not enough.

Sizing from a run that already has preload applied is refused, not
approximated: the beam force there is the *bolt* force, and feeding it back in
counts the preload twice.

### The chain

Two springs in parallel — the bolt and the material it clamps:

| | |
|---|---|
| `δ_S` | bolt compliance, mm/N: head, shank, free thread, engaged thread and nut in series, using VDI 2230's substitute lengths (0.5 d, 0.5 d, 0.4 d) for the parts that have no prismatic length of their own |
| `δ_P` | clamped-member compliance: two Rötscher pressure cones of 30° half-angle, one from each end of the grip, capped where the cone runs past the free edge |
| `Φ = n·δ_P/(δ_S+δ_P)` | **load factor** — the share of an external axial load that reaches the bolt. `n` is where the load enters the clamped parts (0.5 default; 1.0 is the conservative extreme, right under the head) |

A metal joint has `δ_P` several times smaller than `δ_S`, so Φ lands around
0.1–0.2: most of an external load *unloads the interface* rather than adding
to the bolt. That is the entire reason preload works, and why a longer bolt is
better under fatigue — more compliance in the bolt, less alternating load
reaching it.

The grip length comes from the mesh: the bolt beam spans exactly the clamped
length, so it is measured, not typed. Hole diameter comes from the detected
cylinder, and the clamped material's modulus from its assigned material.

### What sets the preload

The joint must retain enough clamp force `F_KR` for whichever governs:

```
no slip      F_KR ≥ S_slip · F_Q / (μ · n_friction)
no opening   F_KR ≥ S_gap · (1 − Φ) · F_A
```

Then the assembly preload has to cover that, plus what is lost on the way:

```
F_Mmin = F_KR + (1 − Φ)·F_A + F_Z          F_Z = embedding / (δ_S + δ_P)
F_Mmax = α_A · F_Mmin
```

`F_Z` is embedding: surface asperities flatten after tightening and the joint
relaxes by a few microns, which at joint stiffness is a real loss of preload.
`α_A` is the tightening factor — how badly the method scatters, from 1.1 for
measured elongation to 1.6 for a bare torque wrench. The scatter is why the
*maximum* is what the bolt must survive while the *minimum* is what the joint
must have.

### What the bolt can take

Tightening leaves torsion in the shank, so the usable tensile capacity is what
is left of yield after it:

```
F_Mzul = ν · A_s · R_p0.2 / √(1 + 3 k_τ²)
k_τ = 1.5 (d_2/d_S) (P/(π d_2) + 1.155 μ_thread)
```

If `F_Mmax > F_Mzul` there is **no feasible preload** — the joint needs more
than the bolt can give. The answer is a bigger or stronger bolt, more bolts, or
a tightening method that scatters less; not a smaller number.

Tightening torque, for reference:

```
M_A = F_Mmax (0.16 P + 0.58 d_2 μ_thread + 0.5 D_Km μ_head)
```

Surface pressure under the head is checked against the clamped material's
bearing limit. On aluminium, and certainly on a polymer, this frequently
governs before the bolt does.

### Limits, stated

- **Concentric** clamping and load introduction. Eccentricity raises the
  bolt's share and can dominate a flange; it is not modelled, so `Φ` here is
  VDI 2230's `Φ_K`.
- Static, plus a simple alternating check. No fatigue life curve.
- The cone model approximates real flange behaviour. Where a contact run has
  measured the bolt's load increase directly, that measurement is used instead.
- The absolute preload a standard tabulates depends on which cross-section is
  taken to govern and what utilisation is assumed. Every intermediate value is
  reported so you can reconcile it with your own bolt standard rather than
  taking this one on faith.

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
