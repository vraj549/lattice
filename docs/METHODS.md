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

## Shock

A shock is specified one of two ways, and Lattice takes both.

**An SRS** — for each frequency, the peak acceleration a single-degree-of-freedom
oscillator of that frequency reaches when its base is driven by the event.
Written as breakpoints in (Hz, g) at a stated Q. Pyroshock and most spacecraft
specs look like this.

**A classical pulse** — half-sine, terminal-peak sawtooth or trapezoid of a
stated amplitude and duration, as MIL-STD-810 Method 516 defines them. Its SRS
is computed and applied, so both inputs meet in the same place.

### Why there is no time history

An SRS carries a peak per frequency, with no phase and no time. There is
nothing to integrate. The answer is a combination over the modal basis, which
is the standard method for a linear structure and reuses the modal solve
unchanged — the same division of labour as random vibration, and for the same
reason: the solver does only what it is already proven to do.

The consequence is worth stating plainly: **every number a shock run reports is
a magnitude with no sign, and two of them need not occur at the same instant.**
A combined stress and a combined displacement are each defensible; their ratio
is not.

### The chain

| | |
|---|---|
| `Γ` | participation factor, taken as `sqrt(m_eff / m_gene)`. Doing it from the mass table rather than reading a separate one makes it **normalisation-invariant**: the response is `φ·Γ·S_d`, and scaling the mode shape scales `m_gene` by the square, so the product does not move. Whatever normalisation the solver used, this stays consistent with the mode shapes it wrote. |
| `q = Γ·S_a/ω²` | peak modal coordinate — the spectral displacement of that mode |
| response | `φ·q` per mode, then combined. Bolt end forces and probe displacements are both mode shapes, so both go through unchanged. |
| interface load | `Σ m_eff,i · S_a(f_i)`, combined — the load into the supports |

### Combining modes

Three rules, all of them sign-blind:

- **SRSS** — peaks independent. Right when modes are well separated; the default.
- **NRL** — largest contributor at full value, SRSS for the rest. Written for
  shock, and the safer choice when one mode dominates.
- **ABS** — every mode peaking at once. A true upper bound, usually a loose one.

Sign-blindness is deliberate. The sign of a participation factor is not
recoverable from effective mass, so a rule that needed it — CQC, or an
algebraic sum — could not be computed honestly here, and is not offered.

### Missing mass

A modal basis truncated at some frequency has lost the stiffer modes, which do
not resonate: they ride with the base. Their share is the residual effective
mass at the **ZPA**, the spectrum's high-frequency value, and it is added in
with the periodic terms. The results panel reports the effective mass the basis
captured, because a large residual means the *shape* of the response is not
resolved even though the total force is corrected.

### The pulse spectrum

Computed with Smallwood's ramp-invariant recursive filter — the exact response
of an oscillator to an input taken as piecewise linear between samples, which
is what a sampled history is. The history is padded well past the end of the
pulse: an oscillator softer than the pulse keeps ringing once it stops, and
that residual is the entire low-frequency half of the spectrum.

Checked against a direct RK4 integration of the same oscillator (agreement
better than 0.2%), and against the two asymptotes every SRS has: it tends to
the pulse peak at high frequency, and to `2πf·ΔV` at low frequency, where `ΔV`
is the area under the pulse. An undamped half-sine peaks at 1.766 times the
pulse near `f·τ = 0.8`.

An **initial-peak sawtooth is not offered**. It starts with a step, no shock
machine can produce one, and its spectrum never settles to the pulse
amplitude — an oscillator hit with a step overshoots to
`1 + exp(-ζπ/√(1-ζ²))`, about 1.85 at Q = 10. The ZPA used for the missing-mass
term would have been wrong by that factor, so the shape is excluded rather than
special-cased.

### Limits, stated

- **Q matters.** A spectrum read at one Q and applied at another is a different
  spectrum. The damping entered here is the spectrum's, and it should be the
  one the spec was written at.
- Linear and single-axis. Simultaneous axes would need their own directional
  combination; run them separately and combine outside.
- No stress field. Combining per-mode stress components would be sound, but
  what is combined here is what the modal tables carry: interface load, bolt
  end forces, and displacement at probes.
- The spectrum is applied at the restrained base, so the model needs a support.
  With nothing restrained the modes are free-free and the answer is not merely
  inaccurate, it is meaningless.

## Friction without a Newton loop

A frictional interface is nonlinear because its state is part of the answer:
stuck, sliding or open changes the stiffness. That is true when the state is
genuinely unknown — and it is **not** unknown in a designed friction joint. The
joint is preloaded precisely so it stays stuck, and

> a stuck frictional interface and a bonded one are the same constraint.

They give identical results. So Lattice glues it, solves linearly, and then
reads the interface tractions back to ask whether that was allowed:

```
p      = -n · σ · n                   contact pressure (compression positive)
τ      = |σ·n - (n·σ·n) n|            shear carried across the interface
margin = μ·p / τ                      > 1 means friction was enough
```

Two ways the premise fails, reported separately because they need different
fixes:

- **`margin < 1` somewhere** — it slips there. The bonded solve carried shear
  friction cannot, so it is wrong; the nonlinear run is the one to believe.
- **`p ≤ 0` somewhere** — the interface is in tension. A bonded interface
  pulls; a real one lets go. The joint is opening, and more preload is the fix.

If neither happens, the linear result is not an approximation of the nonlinear
one — **it is the nonlinear one**, at one solve with no Newton loop.

Slip and gapping are reported as a **fraction of interface area**, using a
lumped nodal area rather than a node count. Mesh refinement clusters nodes
exactly where stress concentrates, so counting nodes reads high precisely where
it matters most.

### Where it does not apply

- **Frictionless** and **no-separation** interfaces are never solved this way.
  There is no no-slip premise to validate: they slide by definition, so bonded
  is a different interface, not a testable stand-in for one.
- One nonlinear interface anywhere makes the whole solve nonlinear. A checked
  frictional interface stays glued even then — the premise is the same either
  way, and gluing keeps its status out of the Newton loop.
- The check uses **one normal for the whole interface**. On a curved face
  pressure and shear get mixed; the panel reports the interface's flatness and
  warns below 0.98.

## Element shapes

Tetrahedra by default, and quadratic ones, which is what handles a general
imported solid.

Hexahedra can be asked for. They need a shape that **sweeps** — a plate or a
block, one solid, no holes through the swept face — and where that holds they
are worth having. Measured on a 100 × 40 × 2 mm plate:

| | elements | worst Jacobian |
|---|---|---|
| tetrahedra | 2045 | 0.004 |
| hexahedra | 275 | 0.835 |

The tet mesh there is mostly slivers, which is the thin-wall problem.

Where the shape does **not** sweep, asking for hexahedra does not give you a
worse hex mesh — it gives you no hexahedra at all:

| | result |
|---|---|
| plate with a hole | 0 hexes: 4295 tets + 643 pyramids, worst 0.077 |
| L-bracket | 0 hexes: 5136 tets + 886 pyramids, worst 0.100 |
| two parts in contact | gmsh refuses: non-manifold quad boundaries |

Each of those is worse than the plain tet mesh of the same part. So the request
is treated as a request, not an instruction: Lattice meshes, **measures what it
got**, and falls back to tetrahedra if there are no hexahedra or any inverted
element. The job log says which you ended up with, and the mesh panel reports
the element mix.

A bolted joint has holes and more than one part, so it will mesh with
tetrahedra whatever this is set to. That is the honest outcome, not a
limitation being hidden.

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

The grip length comes from the mesh: the bolt beam spans the outer extreme of
the faces you picked on each side, so it is the clamped length whether you
picked the hole cylinders or the bearing faces under head and nut. Hole
diameter comes from the detected cylinder, and the clamped material's modulus
from its assigned material.

### How the bolt is modelled

A **Timoshenko beam** on the hole axis, section = the tensile stress area,
each end coupled to that side's picked faces by an **RBE3 distributing**
constraint. Preload is an imposed axial strain, calibrated against the
measured beam force (below).

Distributing, not rigid: the coupled faces are not made into a rigid body, so
the parts still deform. Two places where the beam is not the fastener:

| | |
|---|---|
| **Axial stiffness** | A prismatic bar of the grip length is stiffer than a real bolt, which also flexes in the head, the engaged thread and the nut. For an M6 through 16 mm of steel the real bolt is 16% more compliant, so the model gives Φ = 0.19 where the compliance chain above gives 0.168. The beam therefore sends slightly **more** external load to the bolt than reality — the conservative direction for bolt stress. The sizing panel uses the full compliance chain, not the beam, so the two numbers differ by about that much. |
| **Shear** | Coupling to the hole wall makes the bolt a zero-clearance pin. A real clearance bolt carries no shear until the joint has slipped by the clearance, so the model under-predicts slip. Treat the slip margin in the sizing panel, which is a friction calculation, as the authority — not the fact that the FE joint did not slide. |

For a tapped blind hole, pick the bearing face at the first engaged thread
rather than the tapped cylinder: the cylinder's far end is the bottom of the
hole, which is past the clamped length.

### Preload is calibrated, not assumed

An imposed strain does not deliver the force it was derived from. The bolt is
a spring in parallel with the clamped parts, and they take part of the imposed
shortening back:

```
F_achieved = F_requested · δ_S/(δ_S+δ_P) = F_requested · (1 − Φ)
```

which is **15% low on a steel joint and 35% low on aluminium**. So Lattice
measures it: a preload-only solve reads the axial force the beams ended up
with, the imposed strain is rescaled, and it solves again — proportionally on
the first correction, then by a secant step, since several bolts in one joint
pull on each other. It stops within 1%, and the results panel reports what the
run actually contains rather than what was asked for.

This costs one to three extra solves, each stripped to the bolt forces (no
stress recovery, no field output). Set `preload_calibration: false` in the
analysis config to skip it; the run is then low by the amount above and says
so.

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
- Clamp load enters through the picked faces. Picking the hole cylinders — the
  usual choice, since most CAD has no separate washer footprint — spreads it
  over the hole wall rather than the bearing annulus, which makes the clamped
  parts look slightly stiffer than they are.
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
