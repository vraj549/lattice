# Changelog

## 0.27.0

### The toolbar follows the selection

Selecting **Connections** now puts Detect contacts, Bolt and Tie on the command
bar; a contact gets Swap sides / Suppress / Delete; a bolt gets Pattern; a
solid gets Hide / Isolate / Show all; an analysis gets Support / Load. The same
reason a ribbon has context tabs — what you can do depends on what you have in
hand.

That needed the tree's group rows to become selectable at all. **Geometry**,
**Connections** and **Probes** were expand-only: clicking one just folded it,
because those nodes carried no `kind`. A group is a place in the model, and
selecting one should say what is in it and let you add to it, so each now has a
panel as well.

### Probe snapping

Clicking a tessellated surface puts a probe near the feature you meant, off by
half a facet — and then every number it reports is for somewhere else. Picking
now snaps to **corners, circle centres, edge midpoints and edges**, with the
targets taken from the BREP rather than the triangles, so a hole centre is the
hole centre exactly.

Intersections are not a separate snap: in a solid model, edges meet at
vertices, so the corner snap already is one.

Candidates rank by **specificity, not distance** — a corner inside the
tolerance beats a circle centre, which beats an edge. Ranking by proximity
alone makes the snap flicker between kinds as the cursor moves a pixel, which
is worse than no snapping. The marker's shape says what it found, each kind can
be switched off while picking, and the log records which snap the probe landed
on or says nothing was close enough.

### Two of my own bugs, both silent

- `Node.append()` stringifies anything that is not a Node, so a context group
  that came back `null` printed the word **"null"** into the toolbar.
- The new screen-projection helper was called `_project`, and the viewer
  **already had** a `_project(x, y, z)` taking three scalars. The array arrived
  as `x`, every coordinate came out `NaN`, and snapping simply never fired —
  no error, no marker, nothing to notice. It is `_toScreen(point, rect)` now,
  and takes the rect as an argument because it runs over every candidate on
  every mouse move and `getBoundingClientRect()` forces a layout.


## 0.26.0

The second UI pass: the parts that make a tool feel like one you can work in
rather than one you have to operate.

### Undo

There wasn't any. That is the most conspicuous thing that can be missing from a
pre-processor — every mis-click was permanent, on a model someone has spent
real time building. `⌘Z` / `⇧⌘Z`, sixty steps.

Getting it right took two attempts, and the first failure is the interesting
one. Snapshotting the model when `mutate()` is entered looks obviously correct
and is wrong: twelve call sites in this file change the model and *then* call
`A.mutate(() => {})` purely to save and re-render, so the snapshot captured the
edit after it had happened and undoing it did nothing. Deleting a bolt and
pressing undo demonstrated it immediately. The snapshot is now taken at the
**end** of each committed edit, which is correct whichever pattern a call site
uses and does not depend on future call sites remembering which one they are.

View state — selection, hidden solids, explode — is deliberately not in the
history. Undo should take back a change to the model, not move the camera.

### The controls you use while looking at a result were in a panel

Field, component, step/mode, deformation scale and animate now live on the
command bar. They are what you change *while looking at the thing you are
changing*, and reaching across to the right-hand panel to do it meant looking
away — then pressing a second button ("Show contours") to apply it. Changing
any of them now just shows it.

What stays in the panel is styling — bands, palette — which is set once.

### Keyboard

`F` fit · `0`–`6` standard views · `←`/`→` step modes · `Space` animate ·
`P` probe · `M` edges · `X` explode · `E` section · `Delete` · `⌘Z` / `⇧⌘Z` ·
`⌘↵` run · `Esc` cancel a pick · `H` explanatory notes · `?` the list itself.

Discoverable rather than folklore: the list is a keystroke away and also a
button. Nothing fires while a field has focus — a tool that eats the `1` you
are typing into a preload box is worse than one with no shortcuts.

### Also

- Seven standard views on the bar. A view cube you have to aim at is slower
  than a button you can hit.
- The trailing toolbar group is **pinned** to the right edge. The bar scrolls
  when the window is narrow, and undo scrolling off the end is not acceptable
  for undo.


## 0.25.0

A methods review against the standards the methods claim to follow. Three
defects, all of which made the tool report something more comfortable than the
truth.

### Rigid responses were being combined statistically

A mode whose spectral acceleration has come down to the ZPA does not resonate —
it rides with the input. Its response is in phase with the input and with every
other rigid response, so **rigid responses add algebraically**; only resonant
ones are combined statistically, and the two then combine as
`√(rigid² + periodic²)`. That is US NRC RG 1.92 Rev. 2. Lattice SRSS'd
everything, which replaces a sum of *N* equal rigid terms with `√N` of them.

On a representative bolted-plate shock the response is **82% rigid**, and the
interface load came out **21% low** — in the non-conservative direction. High-
frequency shock is exactly where this is worst, which is to say pyroshock.

The rigid fraction per mode is Lindley-Yow's `α = ZPA/S_a` where `S_a ≥ ZPA`,
0 below. The missing-mass term is a rigid response and now joins the rigid sum
rather than being SRSS'd in — which gives the identity that makes the whole
thing checkable: **if every mode is rigid, the answer is the model's mass times
the ZPA.** Newton's second law. SRSS cannot produce it, and there is now a test
that demands it.

Writing that test immediately caught a second bug in the fix: log-log
interpolation returns 19.999999999999996 for a plateau of 20, and an exact
`S_a < ZPA` comparison therefore classified every mode sitting *on* the ZPA —
most of a high-frequency basis — as fully periodic, silently turning the whole
correction back off. A physical branch cannot hinge on the last bit of a float.

The algebraic sum needs to know which way each mode moves, and effective mass
is a square, so it gives `|Γ|` and no sign. Lattice now asks the solver for the
participation factors too. With them, probe displacements and bolt forces are
summed correctly; without them those two fall back to summing magnitudes — an
upper bound — and say so. The interface load never needed signs: it is mass
times acceleration, all in the direction of the input.

### A check that could not fail

The bolt sizing reported a "slip margin" of `μ·n·F_Kmin_service / F_Q`. But
`F_Kmin_service` is `F_KR` **by construction** — `F_Mmin` was defined as `F_KR`
plus the losses that get subtracted back off to produce it — and `F_KR` is at
least `S_slip·F_Q/(μ·n)`. So the margin was `S_slip` echoed back, and the
"joint slips" warning attached to it could never fire for any joint whatsoever.

A test that cannot fail reads as verification while verifying nothing. Worse,
one of the unit tests asserted the tautology (`slip_margin >= 1.0`, "sized for
no slip, so it must not slip") and passed forever. Both are gone. Real slip
verification reads interface tractions off a preloaded run and has been there
since 0.22.0.

### No fatigue check, in a tool for preloaded joints

The docs claimed "a simple alternating check". There wasn't one. A preloaded
bolt usually fails in fatigue rather than tension, and Φ is precisely what
governs it. Now checked:

```
σ_a   = Φ·F_A / (2 A_s)                    external load taken as fully alternating
σ_ASV = 0.85 (150/d + 45) MPa              rolled thread, VDI 2230-1:2015 §5.5.3
```

`σ_ASV` **does not depend on property class** — a 12.9 bolt is no better in
fatigue than an 8.8 one, which is the most commonly missed fact about bolted
joints, and there is a test that pins it. A joint with a feasible preload
window is normally fatigue-safe by a wide margin because Φ is small; that is
the reason preloading works, and it is also a test.

### Also

- Documented what is *not* modelled rather than leaving it implied: no thermal
  `ΔF_Vth` term, and the residual clamp is reported rather than checked.
- The shock panel shows the rigid and periodic parts and each mode's α, so the
  split is visible instead of being an internal detail.


## 0.24.0

### The interface had no command surface

Every action lived inside whichever panel happened to be open. To run an
analysis you first had to find and click that analysis in the tree; to mesh,
you clicked Mesh in the tree. Nothing on screen answered *what can I do right
now*, which is the first question an interface has to answer.

There is now a **command bar**, and it is contextual rather than a fixed
ribbon — a bar that always shows everything mostly shows things that do not
apply, which is what makes a ribbon feel cluttered. Mesh, Run and Add analysis
are always there; Fit and Section are always there; Explode appears only with
more than one solid in the geometry view; Edges and Probe only in results.

### Explanations are read once and then re-read forever

Panels carried four- and six-line paragraphs of teaching text above their
numbers, pushing the numbers down a 300 px column. The text is worth keeping —
someone meeting a feature for the first time needs it — but not at that price.

Every section that contains explanation grows a **?** in its heading, and there
is an **Explain** toggle in the command bar for all of them at once. Off by
default, remembered. **Warnings are never folded**: those are state, not
teaching.

### Density without hierarchy is just small

The stylesheet was right that an FEA tree wants 22 px rows, and wrong that
everything in them should be the same size. 10, 11 and 11.5 px were doing
unrelated jobs, icons were 9 px, and an analysis — a major object — looked
exactly like a support. There is now a type scale, a 15 px glyph size, and a
study reads as a study.

### Three navigation systems that disagreed

Clicking a result in the tree left the viewport showing the geometry: the tree,
the panel and the view tabs each had an opinion about what you were doing.
Selecting a result now loads that run's field and switches the view. One axis.

### Also

- A **status bar**: solver, mesh (with element type), result count, and what is
  running. It was previously spread across a chip in the header, a tree row,
  and a collapsed drawer.
- The solver name is off every analysis row. It is a setting that rarely
  changes, and it cost a column to print the same word three times; it appears
  now only when it is not the default or cannot run what is being asked.
- The left pane went from 264 px to 300 px, which stops the truncation of
  ordinary study names.

This is a first pass at the structural problems, not a finished redesign.
Result controls still live in panels rather than the command bar, and there is
no keyboard layer yet.


## 0.23.0

### Hexahedra, properly this time

**I was wrong in 0.22.0.** I said a hole defeats hex meshing. A hole defeats
`setTransfiniteAutomatic`, which needs a four-sided face — and that was the
wrong tool. The right one is **sweeping**, and a swept plate does not care what
its cross-section looks like, because the section is quad-meshed in 2D where a
hole is nothing special.

Two 8 mm plates with bolt holes, 4 mm target size:

| | elements | nodes | DOF | worst Jacobian |
|---|---|---|---|---|
| tetrahedra | 24 304 TET10 | 37 353 | 112 059 | 0.281 |
| hexahedra | 3 396 HEXA20 | 16 880 | 50 640 | 0.375 |

Less than half the DOF and a better worst element, on exactly the geometry I
previously reported as impossible.

**What sweeps** is any prism: two parallel planar caps of equal area, offset
along their normal, area × thickness equal to the volume. Plates with holes,
L-sections, channels, gussets. "Not a box" is not the same as "not sweepable" —
an L-bracket sweeps along its length, which one of these tests had to be
rewritten to admit.

**A stack is swept as a chain.** The volumes form a graph whose nodes are their
cap faces; a stack is a path through it; each volume is extruded from the face
the previous one produced. That is what keeps the interface between two plates
one shared face instead of two coincident ones — a hex volume cannot share a
conformal face with a tet one, and gmsh refuses such a mesh outright.

**The rebuild is hidden, and checked before it is trusted.** gmsh cannot sweep
an imported solid in place, so base faces are extruded into new geometry, which
renumbers every entity — and the whole project is keyed to the tags from
import. Each original face and volume is matched to its rebuilt twin by
centroid and area, and if a single one fails to match, the rebuild is abandoned
and the model meshes with tetrahedra. That check earned its keep immediately:
it caught a real bug where a stack sharing its middle face was extruded the
same way twice.

Quadratic hexes are HEXA20 / C3D20, not the 27-node form — what code_aster and
CalculiX actually read.

Verified end to end on the bolted stack: same face groups, same bolt grip, same
material volumes, deck builds, `.inp` written.


## 0.22.1

**Naming and visibility for solids.**

Rename a solid from its panel. The name is stored in `setup.solid_names` keyed
by tag — with the material assignments, which are already keyed the same way —
so it is saved with the project and is not lost when geometry is re-derived on
import. Every place that says what a part is called now goes through one
function, so a renamed solid reads correctly in the tree, the contact panel,
the interface list, the tie picker, and the auto-generated name of a detected
contact.

Hide and show from an **eye on each tree row**, rather than having to select
each solid and find the button in its panel — the wrong way round when you are
working through a stack. Hidden rows dim and keep their eye visible, so the
only clue that a part is missing is not the part missing from the viewport.
**Isolate** shows one and hides the rest; **Show all** comes back, and says how
many are hidden.

Visibility stays session state, not model state: it changes what you can look
at and click, never what is solved. Hidden faces were already unpickable — the
raycast filters on visibility — which is exactly the point when the face you
want is buried.


## 0.22.0

### Friction without a Newton loop

You remembered right: a friction joint does not have to be a nonlinear solve.

A frictional interface is nonlinear because its state is part of the answer —
stuck, sliding or open changes the stiffness. That is true when the state is
genuinely unknown, and it is **not** unknown in a designed friction joint. The
joint is preloaded precisely so it stays stuck, and

> a stuck frictional interface and a bonded one are the same constraint.

They give identical results. So Lattice now glues it, solves it linearly, and
reads the interface tractions back to ask whether that was allowed:

```
p      = -n · σ · n          contact pressure
τ      = |σ·n - (n·σ·n)n|    shear carried across the interface
margin = μ·p / τ             > 1 means friction was enough
```

Two failures, reported separately because they need different fixes:
**`margin < 1`** — it slips there, and the bonded solve carried shear friction
cannot. **`p ≤ 0`** — the interface is in tension; a bonded one pulls where a
real one lets go, so the joint is opening and the fix is more preload.

If neither happens the linear result is not an approximation of the nonlinear
one, **it is the nonlinear one** — at one solve with no Newton loop. This is
the default for frictional contact now; the old behaviour is one dropdown away
and the panel offers the switch when the check fails.

Frictionless and no-separation are untouched. There is no no-slip premise to
validate there, so bonded is a different interface, not a testable stand-in.

Slip and gapping are reported as a fraction of interface **area**, using a
lumped nodal area — not the consistent-load weights already in the mesh, whose
corner nodes are exactly zero, and not a node count, because refinement
clusters nodes where stress concentrates.

### Auto-explode

A slider in the viewport pulls the parts apart along the line from the
assembly's centre to each solid's own. No stacking direction to infer, and it
separates a bolted stack, a bracket pair and a ring of parts alike. Faces
shared by two solids keep the average of their offsets so an interface stays
between the parts it joins.

The faying surfaces are the faces contacts are made of, and on a stack they are
exactly the ones you cannot click. Now you can.

### Hexahedra, where they are real

`Element shape → Hexahedra` on the mesh panel. It needs a shape that **sweeps**
— a plate or a block, one solid, no holes through the swept face — and there it
is worth having. On a 100 × 40 × 2 mm plate:

| | elements | worst Jacobian |
|---|---|---|
| tetrahedra | 2045 | 0.004 |
| hexahedra | 275 | 0.835 |

Where the shape does not sweep, asking for hexahedra does not give a worse hex
mesh — it gives **no hexahedra at all**:

| | result |
|---|---|
| plate with a hole | 0 hexes: 4295 tets + 643 pyramids, worst 0.077 |
| L-bracket | 0 hexes: 5136 tets + 886 pyramids, worst 0.100 |
| two parts in contact | gmsh refuses outright |

Each is worse than the plain tet mesh of the same part. So the setting is a
request, not an instruction: Lattice meshes, **measures what it got**, and falls
back to tetrahedra on no hexahedra or any inverted element. The job log says
which you ended up with.

Being straight about it: a bolted joint has holes and more than one part, so it
will mesh with tetrahedra whatever this is set to. If the thin-wall case is what
you are after, shell elements are the real answer and this is not that.

### Also

- **Contacts were missing from the solve fingerprint.** Changing an interface
  from bonded to frictional, or changing μ, changed the answer and left the old
  result labelled as current. Contacts and a deck-format token are in it now, so
  every frictional result from before this release is correctly marked stale.
- The CalculiX contact-face map is tetrahedron-specific and silently returned a
  partial map for any other element. It refuses outright now — a contact master
  built on half an interface is wrong in a way nothing downstream can see.


## 0.21.0

**Shock.** Specified either way a shock is specified: as an **SRS** in (Hz, g),
or as a **classical pulse** — half-sine, terminal-peak sawtooth or trapezoid,
MIL-STD-810 Method 516. A pulse's spectrum is computed and applied, so both
inputs meet in the same place.

It reports the peak load into the supports, the peak load in every bolt, and
the peak displacement at every probe, with the per-mode contributions that
produced them.

### There is no time history, on purpose

An SRS carries a peak per frequency with no phase and no time. There is nothing
to integrate: the answer is a combination over the modal basis. So the solve is
exactly the modal one, already proven, and the spectrum arithmetic lives in
`shock.py` where it is unit-tested — the same division random vibration uses.

The consequence is stated in the panel and the docs rather than buried: **every
number here is a magnitude with no sign, and two of them need not happen at the
same instant.** A combined stress and a combined displacement are each
defensible; their ratio is not.

### Three rules, all sign-blind

SRSS (modes independent), NRL (largest at full value, SRSS the rest — written
for shock), ABS (upper bound). Sign-blindness is deliberate: the sign of a
participation factor is not recoverable from effective mass, so CQC and an
algebraic sum could not be computed honestly and are not offered.

The participation factor is taken as `sqrt(m_eff/m_gene)` from the mass table,
which makes it **normalisation-invariant** — the response is `φ·Γ·S_d`, and
scaling the mode shape scales `m_gene` by the square. Whatever normalisation
the solver used, this stays consistent with the mode shapes it wrote.

### The pulse spectrum

Smallwood's ramp-invariant filter, validated three ways rather than trusted:

- against a **direct RK4 integration** of the same oscillator — a different
  algorithm, not a rearrangement of the same one — agreeing to 0.2%
- against both asymptotes every SRS has: the pulse peak at high frequency, and
  `2πf·ΔV` at low frequency. The second is the sharp one, because `ΔV` is the
  area under the pulse and every shape here has a different one.
- an undamped half-sine peaks at 1.766× near `f·τ = 0.8`, the classical result

An **initial-peak sawtooth is not offered**. Writing the test for it showed why:
it starts with a step, so its spectrum never settles to the pulse amplitude —
an oscillator hit with a step overshoots to `1 + exp(-ζπ/√(1-ζ²))`, about 1.85
at Q = 10. The ZPA drives the missing-mass correction, so that would have been
wrong by 85%. ZPA is now **measured off the pulse's own spectrum** rather than
assumed equal to its amplitude, and the shape no shock machine can produce is
gone.

### Also

- **Effective mass was shown as a percentage of itself.** The modal panel
  divided `MASS_EFFE_UN_D*` — code_aster's *unitary* effective mass, already a
  fraction — by the model mass again, printing over 100% for any model lighter
  than a tonne. The random-vibration truncation check read the same column as a
  fraction, so the two disagreed inside one build.
- **Every generated deck is now parsed as Python in the test suite.** A `.comm`
  is executed as Python by code_aster, so a syntax error is a guaranteed
  failure, and the nearest solver that would catch it is on another machine.
  Cheap, and it covers a whole class of deck bugs — bad indentation inside an
  optional block, most of all.


## 0.20.0

**The bolt model.** Two errors sat between the sizing calculation and what the
solver was actually asked to compute. Both are on the number this tool exists
to produce.

### The bolt was half as long as the bolt

The beam spanned the two picked faces' **centroids**. Pick the hole cylinders —
the documented way to define a bolt — and the centroid of a hole through an
8 mm plate sits at 4 mm, so a bolt through two 8 mm plates came out **8 mm
long instead of 16**.

That length is the bolt's elastic length. It sets the bolt's stiffness, so it
sets how much of an external load reaches the bolt, and the sizing panel reads
it straight back as `l_K`:

```
                     bolt's share of an external load, Φ
  before (8 mm beam)                0.319
  after  (16 mm beam)               0.190
  compliance chain (reference)      0.168
```

The beam now spans the **outer extreme** of the picked faces. That gives the
clamped length, and gives the *same* answer whether you picked the hole
cylinders or the bearing faces under head and nut — which is the property that
makes it right rather than merely bigger. Meshes written before this are
refused and re-generated.

### The preload was not the preload

`PRE_EPSI` imposes a shortening, not a force. The clamped parts push back, so
the bolt settles at `F_requested · (1 − Φ)` — **15% low on a steel joint, 35%
on aluminium**. Ask for 10 kN, get 8.4 kN, and every margin computed from it
is wrong.

Lattice now measures it. A preload-only solve reads the axial force the beams
ended up with, the imposed strain is rescaled, and it solves again. One
correction is exact for a single bolt; bolts in one joint pull on each other,
so after the first pass the step becomes a secant one. It stops within 1%, and
the results panel reports **what the run contains**, never an extrapolation.

The correction is applied by rewriting the deck between solves, not by a loop
inside the `.comm`: the solver input uses no command or API call it did not
already use. Cost is one to three extra solves, each stripped to the bolt
forces — no stress recovery, no field output.

### Also

- **An intermittent 38% error in the CalculiX suite.** Three tests set their
  own `OMP_NUM_THREADS=4` — the exact condition the solver pins to 1 because
  multithreaded SPOOLES returns wrong answers and exits 0. It passed in
  isolation and failed in the full run. Tests now use the production
  environment builder, and a test enforces that nothing sets that variable
  itself.
- **The server layer had no tests.** It is where the run is orchestrated, and
  it now has end-to-end coverage through the real HTTP API against the mock
  solver — which had to learn the one piece of physics that makes the
  calibration loop testable.
- A module-scoped fixture handed out by reference: one test rewrote bolt 1
  into an M1.6 for every test that ran after it.
- A spurious `divide by zero` warning from macOS Accelerate on a `matmul` of
  finite values, which only appeared on faces large enough to leave numpy's
  naive path.

### What the beam still is not

Documented rather than papered over, in
[docs/METHODS.md](docs/METHODS.md#how-the-bolt-is-modelled): a prismatic beam
has no head, thread or nut flexibility, so it sends ~13% more external load to
the bolt than the compliance chain does — the conservative direction. And
coupling to the hole wall makes the bolt a zero-clearance pin, so the model
under-predicts slip; the friction margin in the sizing panel is the authority
there, not the fact that the FE joint did not slide.


## 0.19.0

**Bolt sizing.** The tool answers the question it was being used for: *how much
preload does each bolt need, and will the bolt survive it?*

### The methodology

An FE model does not give you a required preload. It gives each bolt's share of
the load path; turning that into a preload needs the joint's spring behaviour,
the losses between tightening and service, and the scatter of the tightening
method. Both halves are now there, kept separate so every number is traceable.

**Workflow**

1. Run with **preload set to zero**. With no preload the beam carries exactly
   the external load `F_A` and `F_Q` the joint must be preloaded against.
2. **Solution → Bolt sizing** reports, per bolt: load factor Φ, clamp force
   needed against slip and against opening, embedding loss, required assembly
   preload and its torque, what the bolt can take, and the margins.
3. Enter that preload and run again to verify — as separate parts with a
   frictional contact, so the interface can genuinely open or slip.

Sizing from a run that already has preload applied is **refused**, not
approximated: the beam force there is the bolt force, and feeding it back
counts the preload twice. It asked for roughly three times the real preload
before this was caught.

### What it computes

```
Φ = n·δ_P/(δ_S+δ_P)     the share of an external load that reaches the bolt
F_KR                     clamp needed:  slip  S·F_Q/(μ·n)
                                        gap   S·(1−Φ)·F_A
F_Mmin = F_KR + (1−Φ)F_A + F_Z          F_Z = embedding/(δ_S+δ_P)
F_Mmax = α_A · F_Mmin                   α_A = 1.1 … 1.6 by method
F_Mzul = ν·A_s·R_p0.2/√(1+3k_τ²)        what is left after tightening torsion
```

Grip length is **measured from the mesh** — the beam spans exactly the clamped
length. Hole diameter comes from the detected cylinder, the clamped modulus
from the assigned material. Head bearing diameter, available material and
friction are editable assumptions.

If `F_Mmax > F_Mzul` it reports **no feasible preload** rather than a smaller
number: the joint needs a bigger bolt, more bolts, or less tightening scatter.
Surface pressure under the head is checked too — on aluminium or a polymer that
often governs first.

### Two things found while building it

- **The clamped-member model disagreed with itself by 40 %** across its own
  case boundary: the same joint came out materially stiffer or softer depending
  which branch of a three-case substitute-area formula it fell into. Replaced
  with Rötscher pressure cones (Shigley's frustum result), which is one
  continuous expression. Continuity is now tested by refining the step and
  checking the largest jump falls with it — a fixed tolerance would only have
  tested that the function is not steep, which near the cutoff it legitimately
  is.
- Validated on identities rather than remembered table values: stress areas
  reproduce ISO 898-1 to 0.1 %, compliances are springs in series, the members
  come out several times stiffer than the bolt so Φ lands in 0.05–0.35, load
  splits sum to the applied load, and a longer bolt takes less of it — which is
  why a fatigue joint wants one.

The absolute preload a standard tabulates depends on which cross-section is
taken to govern and what utilisation is assumed, so every intermediate value is
reported for reconciliation against your own bolt standard.

Method in [docs/METHODS.md](docs/METHODS.md#bolted-joints--sizing-and-preload).


## 0.18.0

Documentation rewritten, and the installation path fixed — it was broken.

### `pip install -e .` did not work on a clean clone

The `pip` bundled with a Python 3.9 virtual environment cannot do an editable
install of a `pyproject.toml`-only package:

```
A "pyproject.toml" file was found, but editable mode currently
requires a setuptools-based build.
```

It **fails while looking like it succeeded** — nothing is installed, and the
app dies later with `No module named 'gmsh'`, which points nowhere near the
cause. That was the exact command in the README. Both the README and the new
install guide now upgrade pip first and explain why, with `pip install .` as
the alternative. Verified by cloning the repo fresh, following the documented
steps, and booting the app.

### `doctor` was giving wrong answers

The environment check is the thing you run when installation is not working, so
it being wrong is worse than it being absent.

- It **did not know CalculiX exists** — reported "no solver" and "geometry/mesh
  only" on a machine with a working one.
- It reported **"no support with faces" for every project**, because it read
  supports from the model. They moved into each analysis in 0.9.
- It told people to `docker pull codeastersolver/codeaster-seq`, which the
  README documents as unusable — 2019, no solver binary. That was also still
  the built-in default; it is now `simvia/code_aster:17.4.22`.

It now lists every engine and what each can run, and gives per-analysis
blockers using the same rules the app uses — including a mesh that needs
re-generating after the 0.17.2 fix.

### Documentation

- **[docs/INSTALL.md](docs/INSTALL.md)** — new. Per-platform install for the
  app and both solvers, configuration, running, updating, troubleshooting.
- **[docs/SOLVERS.md](docs/SOLVERS.md)**, **[docs/METHODS.md](docs/METHODS.md)** —
  linked from the README and from the results panels.
- The README described a code_aster-only tool that could not do contact or
  random vibration and whose bolts stopped at M24. It now covers two solvers,
  the bonded/separate-parts import choice, the full contact set, and bolt
  grades including titanium and PEEK.
- **Corrected: "Apple Silicon — use `--demo-solver` for the interface."** That
  was true when Docker was the only route. CalculiX runs natively there, so
  the advice was sending Mac users away from a working setup.
- The startup overlay still read "on gmsh + code_aster".

### Documentation is now tested

`tests/test_docs.py` asserts the docs against the code: every internal link
resolves, every `LATTICE_*` environment variable is documented, the documented
defaults are the real defaults, the bolt-size range matches the table, the
install guide covers each platform, and the Apple Silicon note points at
CalculiX. Docs rot quietly; these fail loudly.


## 0.17.2

Fixes the regression that broke code_aster, and makes a failed run say what
went wrong.

### Root cause: a leaked gmsh option corrupted the mesh file

CalculiX support (0.16.0) writes an Abaqus deck alongside the UNV, and turns on
`Mesh.SaveGroupsOfNodes` to do it. **gmsh options are global to the session,
and the server keeps one session for its whole life** — so that setting stayed
on and applied to the *next* project's UNV.

A UNV written that way puts node entities inside the element groups. code_aster
reads them as `GROUP_NO`, and the deck's own
`DEFI_GROUP(CREA_GROUP_NO=_F(TOUT_GROUP_MA='OUI'))` then tries to create a group
that already exists — the run aborts.

Measured on one unchanged model, meshed three times in one process:

```
run 1:  unv = 2,264,458 bytes   sha=7615471f53fe     <- correct
run 2:  unv = 2,762,528 bytes   sha=be33a63580f3     <- 1,659 nodes in SUP1_1
run 3:  unv = 2,762,528 bytes   sha=be33a63580f3
```

That is exactly the reported behaviour: **the first mesh after starting the
server solved, and every mesh after it failed.**

Write options are now set explicitly before every write and restored
afterwards. Two tests cover it — one asserts repeated meshes are byte-identical,
one asserts no group ever contains a node entity.

**Meshes already on disk are still bad.** They are versioned now
(`mesh_format`), flagged in the tree as needing a re-mesh, and refused by the
deck writer with that reason rather than failing inside the solver.

### "exit code 1" now says what happened

A failure reported the exit code and "see log", without saying which log.
Failures now extract the lines that matter — `<EXCEPTION>`, `<S>_ERROR`,
`DIAGNOSTIC JOB`, Python tracebacks, gmsh errors — echo them into the job
output, and name the log file path. The message reads as the actual complaint
instead of a number.

### Meshing no longer fails for CalculiX's benefit

Three passes exist purely to serve CalculiX (nodal load weights, element-face
maps, face normals). They ran after the UNV was already written, unguarded, so
a failure in any of them threw away a completed mesh — for a user who may not
even have CalculiX. They now warn and continue.

### Also

- Two tests called `gmsh.finalize()` on the shared session, so everything that
  ran after them failed with "Gmsh has not been initialized" — a long way from
  the cause.

## 0.17.1

Results panels show results.

Roughly 1,200 characters of method explanation came out of the results panels —
how von Mises is formed, what Q means, why beam bolt stress is on the stress
area, what 3σ is for, how Miles' equation relates to the integrated answer. It
was correct and it was in the wrong place: reference material you re-read on
every single run is noise.

It now lives in **[docs/METHODS.md](docs/METHODS.md)**, with a quiet *method
notes* link at the foot of each results panel. **[docs/SOLVERS.md](docs/SOLVERS.md)**
covers the two engines and the CalculiX threading finding.

What stayed on screen is anything that says something about *this* run and
needs a decision:

- Results out of date, and why
- Modal truncation below 90 % effective mass — the result is low
- Too few sweep points inside a half-power band — Q is under-reported
- Equilibrium / fixed-support / small-displacement check failures
- Solver messages

The "✓ truncation is acceptable" all-clear went too: an absent warning already
says that.

Bolt forces, reactions and modes now carry no prose at all — a table, a link,
and the exports.

## 0.17.0

Switching solvers is now a dropdown, and the tool works the same either side of it.

### Parity

CalculiX gained what it was missing for ordinary work:

| | before | now |
|---|---|---|
| Pressure loads | refused | `*DLOAD` on element faces |
| Rotational body loads | refused | `*DLOAD ... CENTRIF` |
| Frictionless / symmetry supports | refused | `*TRANSFORM` into the face frame |
| von Mises, Tresca, principals | missing | derived from the stress tensor |
| Reactions table | missing | same block shape as code_aster |

The field and component menus, the contour view, the probe, the exports and
the Solution branch are now identical whichever engine ran. A CalculiX result
opens in the same panels, with the same names, as a code_aster one.

### Choosing an engine

- The **solver is a field on the analysis itself**, not buried in settings —
  it is the first thing you check when a run behaves differently.
- The **tree row shows which engine** each analysis uses.
- **Capability blockers appear before you run**, from a table the server
  publishes and the deck writer enforces, so the panel cannot drift from what
  the writer will accept. Pick an engine that cannot run a study and it says
  why, and offers a one-click switch to one that can.
- The status chip reports **every** engine present. On a Mac with CalculiX
  installed it used to say "no solver — demo mode", because it only ever
  looked for code_aster.

Bolts, ties, remote loads, harmonic and random remain code_aster only, and are
now stated as such up front rather than discovered at run time.

### Found while testing the parity

- **The equilibrium check was 0.6 % out on every model.** Corner nodes of a
  quadratic face carry no load, so they were absent from the face node map —
  and their reactions were being left out of the sum. They are now recorded
  with zero weight: the load is unchanged, the node set is complete, and the
  residual dropped to 1e-4 %.
- **Pressure loads were invisible to the check** — `applied_total` only added
  up force loads, so a pressure-loaded model was never verified at all. A
  safety check with a hole in it is decoration.
- **Reactions at transformed nodes are reported in the local frame.** Summing
  them as global was wrong; they are rotated back now.
- **A symmetry face sharing an edge with another support** would rotate that
  support's "fixed" into the symmetry frame, and sharing an edge with a loaded
  face would rotate part of the load. Shared nodes are now excluded from the
  transform: the more restrictive constraint wins, and loads always act in the
  direction asked for.
- The "fixed supports moved" check no longer fires on frictionless or
  prescribed-displacement supports, which are supposed to move.

Verified end to end through the running server: reaction 800.0000 N against
800 N applied, residual 5.5e-9.

## 0.16.1

Stress-testing CalculiX found a correctness bug, in CalculiX.

### Multithreaded CalculiX returns silently wrong answers

Re-running one unchanged deck for a cantilever whose exact answer is known:

| threads | runs | wrong | worst error |
|---|---|---|---|
| 1 | 14 | 0 | 0.00 % |
| 2 | 14 | 1 | 43 % |
| 3 | 14 | 4 | 52 % |
| 4, 6, 8 | many | frequent | up to 68 % |

Same input, same machine, exit code 0 every time, nothing in the log. The
factorization simply produces a corrupted solution on some runs.

Lattice was setting `OMP_NUM_THREADS` to the machine's core count, so this
would have produced wrong results roughly a third of the time on an 8-core Mac.
**CalculiX now runs single-threaded by default.** This is a correctness
setting, not a performance one, and there is a test asserting it.
`LATTICE_CCX_THREADS` can raise it, and says loudly what you are accepting.

Single-threaded is exact at every scale tested — 4k to 107k nodes (320k DOF),
0.00 % against beam theory throughout.

### Three checks on every CalculiX result

Wrong answers that exit cleanly need to be caught, not assumed away:

- **Equilibrium** — support reactions must balance the applied load. Summed
  over the supported nodes specifically: CalculiX's nodal force field includes
  applied loads, so over the whole model it is zero by construction and proves
  nothing.
- **Fixed supports must not move** — a corrupted solve can add a rigid-body
  component, which balances perfectly and is still wrong.
- **Small-displacement sanity** — peak displacement against the size of the
  part. Linear theory stops applying long before it stops producing numbers;
  a load 10× too large returns a deflection 10× too large and looks normal.

Together the first two caught 2 of 5 corrupted runs with no false alarms in 13
clean ones. That is a backstop, not a proof — which is exactly why the thread
default is the actual fix.

### Also fixed

- **CalculiX appends to an existing `.frd`**, so a stale file from a previous
  attempt was being read back as the current answer. This invalidated my own
  first determinism test, which is how the bug hid. Stale outputs are now
  removed before every run.
- Failure modes verified to fail loudly: no supports, no material, ν = 0.5,
  frictionless supports and pressure loads on CalculiX are all refused at deck
  time with the reason, before any solver time is spent.

## 0.16.0

Contact, and CalculiX as a second solver.

### Contact — so preload means something

You were right that a bolt preload in a fully bonded model does almost nothing.
A bonded interface can neither separate nor slide, so the clamp load has no
job to do and joint slip cannot be assessed at all. That is now fixed at the
root.

`occ.fragment()` merges coincident faces into one shared face — which is what
makes an assembly bonded and conformal, and also why there was nothing there to
slide. So the assembly treatment is now a choice at import:

- **Bonded** (default, unchanged) — merge touching faces, parts share nodes.
- **Separate parts** — keep both surfaces, and detect the interfaces between
  them by matching area and centroid.

Detected interfaces become **Contacts** in the tree, each with a behaviour:

| Behaviour | Can gap | Can slide | Solve |
|---|---|---|---|
| Bonded | no | no | linear (`LIAISON_MAIL` tie) |
| No separation | no | yes | nonlinear |
| Frictionless | yes | freely | nonlinear |
| Frictional | yes | above μN | nonlinear |

Anything that can slide or separate makes the static solve nonlinear —
`STAT_NON_LINE` with `DEFI_CONTACT` on code_aster, `*CONTACT PAIR` on
CalculiX — because whether the surfaces touch is part of the answer.

**Preload is applied before the external load.** With contact present the run
uses two ramps: the bolts tighten over the first step and hold, then the load
comes on over the second. Applying both at once lets the joint be pushed open
before it was ever clamped — not the sequence the hardware sees, and a good way
to lose convergence.

Modal and harmonic are linear by definition and use the bonded state; that is
stated in the panel rather than left to be discovered.

### CalculiX

A second solver, because code_aster only runs through WSL or Docker and so
does not exist on a Mac at all. CalculiX installs from a package manager, and
Lattice now detects it, offers it per analysis in **Analysis Settings →
Solver**, writes its deck, and reads its `.frd` results into the same viewer.

Covers **static and modal**, including contact. Bolts, harmonic and random stay
on code_aster, and choosing an engine that cannot run a study says so rather
than failing later.

Validated end to end against closed-form results, not just "it ran":

- Cantilever tip deflection vs `PL³/3EI` plus shear: **0.00 %** difference.
- First bending frequency vs `1.875²/2π · √(EI/mL⁴)`: within 6 %.
- Both are tests, not one-off checks.

Distributed loads are applied as consistent nodal forces integrated from the
element shape functions at mesh time — CalculiX addresses face loads by element
face number, which a mesh exported from physical groups does not carry. The
weights integrate to the true face area exactly (verified to 1e-9).

### Also

- Bolt sizes stop at M8.
- Frictionless supports and pressure loads are refused on CalculiX with the
  reason, rather than approximated.

## 0.15.0

Production audit — a line-by-line pass over every file — plus the first batch
of workbench improvements.

### Fixed: typing into any field lost focus after one character

`A.mutate` rebuilt the property panel on every edit, which destroyed the input
being typed into. Entering "4500" meant clicking the field four times. Text and
number fields now edit the model without re-rendering the panel; the tree and
viewport still follow live, and the panel re-renders on blur or Enter.

### Fixed: unbounded GPU memory growth

`Group.clear()` detaches children but does not release their geometry or
material. Every contour load, every project open, and every keystroke (see
below) leaked the previous ones. Everything now goes through a disposing
helper.

Boundary-condition symbols were also rebuilt from scratch on every model edit —
cones, cylinders and tubes allocated per keystroke. They are now rebuilt only
when something they actually draw has changed: typing a preload value or a
name rebuilds nothing.

### Fixed: a bolt could be silently left out of the solve

`_active_bolts` matched mesh records to bolts by ID but validated by *count*.
Deleting one bolt and adding another — exactly what patterning onto a different
hole does — left the count unchanged, so the new bolt was dropped from the deck
without a word and the model solved with a joint that existed in the tree and
in no matrix. The ID sets must now agree.

The mesh also recorded every *requested* face group rather than the ones it
wrote, so the stale-mesh guard passed for groups that did not exist and the
solver aborted on GROUP_MA-not-found minutes later.

### Fixed: project IDs could escape the workspace

`dir()` tested `startswith(root)`, which a sibling path like `../projects-x`
satisfies. Now compared against `root + separator`, with separators rejected in
the ID outright.

### Performance

- **Island counting** was a pure-Python union-find over every tet — millions of
  dict lookups on a large mesh, on every single mesh. Rewritten as vectorised
  label propagation.
- **Physical groups** rebuilt the full surface list from a fresh gmsh call for
  every face tag in every group. Queried once now.
- **Bolt geometry** rebuilt a tag→face index per bolt; shared now, which
  matters once a joint is patterned across a flange.
- **Uploads** ran a synchronous copy inside an async endpoint, blocking the
  event loop for the whole transfer — including the job polling that shows
  import progress. Now on a worker thread, chunked, with a 512 MB cap.
- **Job logs** grew without bound and jobs were never evicted; a long solve
  could hold tens of MB of text nobody reads. Capped to the tail, with client
  offsets kept correct across trimming.
- `psd_at` re-sorted the spectrum on every swept frequency.

### Safety

- One solve per analysis and one mesh per project at a time — a double-click on
  Run had two jobs writing the same directory.
- Material properties are validated on the way to the solver: ν outside
  (−1, 0.5) makes the stiffness matrix singular, and E ≤ 0 is meaningless.
- A random-vibration sweep narrower than its input spectrum now reports how
  much of the input it actually covered. Integrating only the swept band
  silently under-reports g RMS, and nothing else in the result looks wrong.
- Half-picked bolts no longer abort the entire mesh; they are skipped with a
  warning, since the run blocker already catches them.
- `run_solver` could raise `NameError` on the exit-code line and mask the real
  error.

### Workbench

- **Bolt stress**, not just bolt force: σ axial, τ, σ bend, von Mises and % of
  yield per bolt, computed on the same stress area the beam uses — with an
  explicit note that a beam does not resolve the thread root.
- **Titanium Grade 5, PEEK and PEEK GF30** bolt grades. Modulus travels with
  the grade: a PEEK screw left at 210 GPa would take a steel bolt's share of
  the joint.
- **Custom materials** — name, E, ν, density, yield — validated on entry.
- **Free rotation.** Vertical dragging hit an invisible wall at the poles; it
  now wraps through and flips the up-vector.
- **Zoom to the cursor** rather than the screen centre, with the distance
  clamped so you cannot zoom inside the model or past the far plane.
- **Orientation cube** behind the axis arrows — three bare arrows meeting at a
  point are ambiguous from many viewpoints.
- **Selecting a solid highlights it** in the viewport.
- **Deformation scale** shows the true factor, has a **True scale (1×)** button
  and snaps to it while dragging, and **Animate now works for static** results.
- FRF plots **redraw on resize** instead of stretching their bitmap.
- Larger tree expand arrows.
- Bolt sizes now stop at M8.

## 0.14.0

### Small fasteners

Sizes now run from **M1.6** and **#0-80** up, in two series:

- Metric ISO coarse: M1.6, M2, M2.5, M3, M4, M5, M6, M8, M10, M12, M14, M16,
  M20, M24 — stress areas per ISO 898-1.
- Unified inch: **#0-80 UNF, #2-56 UNC, #4-40 UNC, #6-32 UNC** — stress areas
  per ASME B1.1, converted at 645.16 mm²/in².

A **grade** goes with the size, because a preload suggestion is meaningless
without one: ISO classes 8.8 / 10.9 / 12.9 for metric, ASTM A574 alloy socket
head or A2-70 / 18-8 stainless for the inch sizes, and the yield stress stays
editable so you can enter whatever your supplier actually certifies. Changing
between series swaps the grade to a valid one rather than leaving a class 8.8
on a #4-40. The suggestion is 65 % of yield on the stress area, and now says so
along with the numbers it used.

### Bolts are modelled on their stress area, not their shank

The beam section was sized on the nominal major diameter. A bolt carries axial
load through its thread, and on these small sizes that is not a rounding
detail — an M1.6 modelled on a ⌀1.6 shank is **58 % stiffer** than the real
screw, which changes how the joint shares load with the parts around it. The
section is now the equivalent circle of the tensile stress area (M6: ⌀5.06
rather than ⌀6.00), shown in the panel as "Modelled as".

Preload was, and remains, exact either way — it is applied as a pre-strain
ε = −F/(E·A) so the beam force comes back as −F for whatever A is used. That
only holds while the section and the strain use the *same* area, so they now
share one function, with a test that asserts the round trip.

Projects saved before this fall back to the major diameter, so they solve
unchanged; re-picking the size adopts the stress area.

## 0.13.0

### Copy a bolted joint onto every other hole

Build one bolt, then **Copy to other holes…** in its Pattern section: pick each
target hole in the viewport and Lattice creates a joint there from the same
template — size, preload, modulus, and *both* face sets.

The far side is the part that matters. A bolt is a hole cylinder on one part
and a mating cylinder or bearing face on the other, so the copy carries every
face of the template across by the rigid transform that takes the reference
face onto the target — including a rotation when the target hole runs on a
different axis. Where a pure offset misses (plates of different thickness), it
falls back to the cylinder sharing the target's axis on the other part, which
is what actually defines the other side of a joint.

Nothing is created from a guess:

- A hole that already carries a bolt is skipped and reported.
- If two candidate faces straddle the position the mate should occupy, the
  target is refused rather than bolted to whichever was marginally nearer.
- If the template has two sides and only one can be identified, the copy is
  refused — a beam anchored at one end would mesh, solve, and be wrong.
- Every skipped or incomplete target is named in the job log.

The reference face defaults to the template's hole cylinder and can be changed
with **Change reference**, and the panel says how many unused holes of a
matching diameter remain on that part.

Also: **Duplicate** on supports, loads, bolts, ties and probes, copying the
definition in place as `name (2)`.

### The mesh knows when bolts change

Bolt beams and probe nodes are built at mesh time, so adding a joint leaves it
present in the tree and absent from the matrices. The Mesh row now carries a
warning mark listing exactly what has diverged — bolts added or removed,
probes added, boundary conditions changed — the Mesh panel offers **Re-mesh
now**, and affected analyses will not run until it is regenerated.

### Fixed

- Panes no longer stay narrow after a window is widened. Clamping wrote its
  result back as the requested width, which made it a one-way ratchet: shrink
  the window once and the layout never recovered. The width you asked for is
  now kept separately from the width that currently fits.

## 0.12.0

The simulation tree rebuilt as one real hierarchy, the way Ansys Mechanical and
the SimScale workbench present a model — a single top-to-bottom structure you
work down, rather than a stack of flat section headers.

```
bolted plates                    2 solids
  Geometry                       15 faces
    Solid 1                      Structural steel S235
    Bonded interfaces            1
  Connections                    2
    Bolt @25                     M6 · 8362 N
  Probes                         1
  Mesh                           34,221 n
  Preload + pull-off             ✓
    Analysis Settings
    Base                         1 face
    Pull-off                     4500 N
    Solution                     ✓
      Contours                   2 fields
      Bolt forces
      Reactions
```

- **Solution is a node, not a heading.** It sits under its analysis, collapses
  with it, carries its own status mark, and holds the individual results.
- **Analysis Settings is its own node.** The analysis row answers "can I run
  this"; the settings row answers "what exactly am I running". They were one
  panel that had grown to a screen and a half of scrolling.
- **Boundary conditions sit directly under their analysis**, as in Ansys,
  rather than inside Supports/Loads sub-headers.
- **Icons on every row**, per type — solids, bolts, ties, probes, mesh,
  supports, loads, and a distinct glyph per analysis type, so the type no
  longer has to spend a text column next to a name you chose yourself.
  Constraint violet and load amber carry over from the viewport.
- **Everything collapses**, with indent guides, and the open/closed state
  persists per project. A finished study can be folded away.
- **Keyboard navigation**: up/down to move, left/right to collapse and expand,
  Home/End, Enter to select.
- **Insert menus** on the "+" of a row rather than "+ add" text buttons.
  Options that do not apply are shown disabled with the reason — a base-driven
  study offers "Load" greyed out with "this study is driven through its
  supports", which teaches the model rather than just hiding a control.

### Fixed

- **The whole shell could be forced wider than the window.** A grid item's
  default minimum width is its min-content, so the toolbar — which cannot
  shrink past its chips — widened the app and pushed the right panel
  off-screen at anything under about 1000px. Nothing in the shell may do that
  now, and the solver chips truncate or drop instead.
- Insert menus clamp into the viewport and scroll their row into view first,
  instead of opening below the window when anchored to a scrolled-out row.
- The "+" affordance no longer disappears after a click: revealing it only on
  hover meant it vanished on every selection, because selecting re-renders the
  row and CSS `:hover` does not re-evaluate until the pointer moves.
- Tree scroll position survives a re-render.
- A Solution node reached before its run says so instead of waiting forever on
  a 404.

## 0.11.0

Interface overhaul: results became a place you go, not a wall you scroll, and
the tool now tells you when what you are looking at no longer matches the model.

### Are these results still valid?

- **Every run is fingerprinted.** A SHA-256 over the analysis type and config,
  its supports and loads, materials, assignments, bolts, ties, probes and the
  mesh is stamped into the results when they are written, and re-checked
  whenever they are served. Change any of it and the analysis is flagged
  **out of date**.
- **Status symbols in the tree**: green `✓` current, amber `!` out of date,
  amber pulse running, red `✕` failed. The Mesh row carries the same `!` when
  boundary conditions have moved on since it was generated.
- Any panel showing stale numbers leads with an **Out of date** banner and a
  Re-run button. Results written before fingerprinting existed say so rather
  than claiming to be current.
- A `results-status` endpoint re-checks this after every edit, so the badges
  keep up while you work. The comparison lives on the server only — a second
  implementation in the browser would eventually disagree with it.

### Results you can actually use

- **Each output is its own tree item and its own panel** — Contours, Modes,
  Frequency response, Random response, Bolt forces, Reactions, Solver
  messages. They used to be concatenated below the Run button, so finding a
  number meant scrolling past every other number.
- **Exports everywhere.** Per-panel and whole-run CSV of every table, FRF and
  random-response result, plus **nodal values for the field on screen** (all
  components, streamed, so it works on the mesh you actually ran). Exported
  files carry a header line recording whether they were current when written.

### Choosing an analysis

- The `prompt()` asking you to type "harmonic" is gone. A **dialog** describes
  what each study does, and for a harmonic sweep asks up front whether it is
  driven by force or by base acceleration.
- **Inapplicable options are not offered.** A base-driven harmonic or a random
  vibration study shows no Loads branch and no "+ add" — instead it shows what
  is driving it (`Base excitation · 1 g · Z`, `PSD 6.06 g · Z`). Modal has
  neither.
- Removed a warning that said base excitation was not implemented. It has been
  since 0.9; the sweep guidance now follows the excitation you selected.

### Layout

- **Resizable panes** with draggable separators, persisted across sessions,
  keyboard-operable (arrows nudge, Shift for coarse, Home resets). Saved
  widths are clamped to the current window, so a layout from a wide monitor
  cannot reopen with the viewport crushed to nothing.
- Expanding an analysis and selecting it are separate clicks now; the caret
  owns expansion. Selecting an analysis no longer jumps into its results.

### Fixed

- **Skin triangles are wound consistently outward.** Nothing had guaranteed
  this: the four faces of a tet, listed by a fixed corner ordering, come out
  with mixed handedness, so roughly a third of the surface faced inward. Per
  node normals partly cancelled, which is what made contours look speckled,
  and front/back-face tests flickered triangle to triangle. Checked by a test
  that sums signed volumes over the closed surface and compares with the known
  enclosed volume.
- Contour shading amplitude cut from 28 % to 12 % of range, with the
  brightness fold at the silhouette removed. A contour plot is read by colour;
  shading that competes with the band colour is misleading.
- The mesh overlay line colour follows the theme — a fixed near-black line
  vanished into the dark viewport.
- **Fixed: opening a result and pressing Show contours did nothing.** The
  active-result state was only initialised by the old "View results" button,
  so reaching contours any other way left it null and every action no-opped.
- A collapsed job drawer no longer keeps the height the splitter gave it.
- Opening a project asks once which analyses have results instead of probing
  each one and eating a 404 for every analysis that has never run.
- Added a favicon.

## 0.10.0

- **Mesh overlaid on the result contours**, the way commercial post-processors
  show them. The wireframe is built from **element face outlines**, not the
  display sub-triangulation — drawing the sub-triangles would show internal
  splits that are not element boundaries. For Tet10 the outline follows the
  mid-side nodes, so curved edges stay curved. Toggle with **Mesh** in the
  viewport toolbar; it deforms in step with the contours, including during
  mode animation.
- **Probe**: hover the result and it snaps to the nearest node, showing the
  value and that node's coordinates in a label pinned to the node.
  Picking runs against a hidden twin geometry carrying the **deformed**
  positions — the shader deforms on the GPU, which a raycast cannot see, so
  probing undeformed geometry would report the wrong node whenever a
  deformation scale was applied.
- Coordinates in the probe are the node's original position on the part, not
  its deformed location, since that is what you would measure on the drawing.

## 0.9.4

- **Solver resources shown next to the solver status**: threads and memory the
  solver is actually allowed, against what the machine has —
  `8/10 cores · 5.9 / 16.0 GB`. In WSL mode the ceiling reported is the RAM
  **inside the WSL VM**, queried from the distro, because WSL2 takes about half
  the host by default and that, not Windows, is what limits a solve.
- An **ⓘ button** opens a Solver resources dialog: current allocation, the
  environment variables and `lattice.toml` keys to change it, and the
  `.wslconfig` block to give WSL more RAM — with a warning when the solver
  limit is within 15 % of the ceiling, which is the setup where a run gets
  killed rather than failing cleanly.
- Host cores and RAM are probed without adding a dependency (GlobalMemoryStatusEx
  on Windows, sysctl on macOS, /proc/meminfo on Linux).

## 0.9.3

- **Fixed: a mesh generated before 0.9.0 makes every solve fail.** Scoping
  boundary conditions per analysis renamed the mesh groups (`SUP1` →
  `SUP1_1`), but nothing checked that the mesh on disk used the same scheme.
  The deck referenced groups the mesh did not contain and code_aster aborted
  with `le GROUP_MA SUP1_1 ne fait pas partie du maillage` — after the run had
  already started.
- Lattice now verifies **every group a deck will reference exists in the
  mesh** before writing the deck, and refuses with the missing names and a
  "re-mesh" instruction. This is a general guard: it also catches a support or
  load added after meshing, which previously failed the same way.
- The same check runs in the UI, so it appears as a run blocker on the analysis
  and a warning on the Mesh panel — before you click Run, not minutes into a
  solve.

**If you have an existing project: re-mesh it once.** Geometry, materials,
connections and analysis setup are all preserved; only the mesh needs
regenerating.

## 0.9.2

- **Fixed: the property panel went blank for supports and loads.** When BCs
  moved onto analyses in 0.9.0, the tree, the actions and the run-blocker check
  were all updated but the *panel* functions still read `setup.supports` and
  `setup.loads`. Those no longer exist, so selecting or adding a support or
  load threw and the whole right pane rendered empty. The model panel's
  validation had the same stale reference.
- Support and load panels now show which analysis they belong to.
- **A panel that throws can no longer blank the sidebar.** Panel rendering is
  wrapped: a failure shows the error, says it is a Lattice bug rather than a
  model problem, and offers a way back — instead of silently presenting an
  empty pane with no indication anything went wrong.

## 0.9.1

**Interface restyled against how production FEA tools actually look**, and one
real rendering bug fixed.

- **Fixed: the axis triad sat in an opaque black box.** three.js `render()`
  clears colour by default; inside the triad's scissored corner that painted a
  black rectangle. The renderer now clears manually once per frame and the
  triad clears depth only, so it floats directly on the viewport.
- The 3D viewport ground follows the theme properly (it was hard-coded dark),
  with ambient light raised on the light ground so surfaces keep their shading.

Styling, with the reasoning: production pre/post-processors are **dense**
(22px tree rows, 12px base type — you want 40 items visible, not 20),
**sharp** (2px radii; rounded pills read as consumer software), **quiet**
(grey chrome, colour reserved for meaning — violet constraints, amber loads,
green/amber/red status), **hairline-separated** rather than spaced apart, and
**flat** — no shadows or gradients competing with the model.

- Viewport controls are one grouped toolbar instead of buttons floating over
  the model; readouts sit in bordered boxes rather than free-floating text.
- Status chips are square-cornered swatch+text, not 20px pills.
- Uppercase letterspaced labels are pulled back to section headers only.
- Panel rows use right-aligned tabular monospace, the way a solver prints them.

## 0.9.0

**Boundary conditions now belong to an analysis, not the model.**

Previously one global set of supports and loads applied to every analysis, so
a modal run appeared to require a load and a random run appeared to require a
force. Each analysis now owns its own supports and loads, and the tree shows
them as branches — the structure Ansys and SimScale use:

    Geometry / Connections / Probes / Mesh      (shared)
    Analyses
      Static structural
        Supports, Loads
      Modal
        Supports                                 (no Loads section at all)
      Random vibration
        Supports, PSD spectrum

- Only the sections an analysis actually uses are shown, and run blockers are
  reported per analysis ("This analysis has no support with faces").
- Existing projects migrate automatically: global BCs are copied into every
  analysis, with loads omitted from modal.
- Mesh face groups are scoped by analysis (`SUP1_1`, `LOA2_1`, …) since the
  mesh is shared but the BCs are not; otherwise the second analysis's groups
  would overwrite the first's.
- The viewport shows the BC symbols of the selected analysis only.
- **PSD input is now a real table** with Hz / g²/Hz cells, per-row delete,
  add-row, a "Typical spec" preset, and a **Paste…** that accepts rows copied
  from a spec document (tab, comma or space separated). Input overall g RMS
  updates as you type.

## 0.8.0

**Base excitation and random vibration.**

- **Harmonic gains an excitation mode**: force (loads in the tree) or **base
  acceleration**. Base uses `CALC_CHAR_SEISME(MONO_APPUI='OUI')` to build the
  -M.d inertial load, so every fixed support becomes the moving fixture. Drive
  at 1 g and the result reads directly as transmissibility.
- **Random vibration analysis type**: enter the qualification spec as
  (Hz, g^2/Hz) breakpoints, log-log interpolated. Solved as a 1 g base sweep
  across the spectrum; the response PSD is `|T(f)|^2 * PSD_in(f)`, integrated
  for overall g RMS and 3-sigma.
- Results: RMS / 3-sigma per probe, response-PSD plot against the input
  spectrum, and a **Miles' equation cross-check** per mode — agreement means a
  single mode dominates and the shortcut is valid, a gap means it is not.
- **Modal-truncation check.** Base excitation acts through inertia, so missing
  effective mass makes the answer low while looking normal. The cumulative
  effective mass in the drive direction is reported, with a warning below 90 %.

The random maths lives in `random_vib.py` and is computed from the swept
transmissibility rather than a separate solver operator, so it is unit-tested:
transmissibility recovered from a relative-displacement FRF matches closed-form
SDOF base-excitation theory to 0.2 %, peak transmissibility equals Q, a flat
spectrum integrates to sqrt(W*df), and the full integration agrees with Miles
for an isolated mode. 8 new tests, 28 total.

Also: the demo solver now models base excitation (relative displacement for a
1 g input) instead of fabricating an arbitrary FRF, so demo-mode random results
are physically sensible rather than absurd.

## 0.7.0

- **Light mode is now the default**, with a toggle in the app bar that is
  remembered. The 3D viewport follows the theme (pale ground in light, as
  commercial pre/post-processors use).
- **Probe markers**: a green ball and axis crosshair at each response point, so
  probes are as visible as loads and supports.
- **Frequency-response viewer**: a large log-log FRF plot with automatic peak
  detection, on-plot annotation, and a peak table listing frequency,
  amplitude, **Q** (from the half-power bandwidth) and the implied damping
  zeta = 1/(2Q). Y axis switches between amplification, response per unit
  force, and raw response.
- **Sweep-resolution warning.** A peak is only resolved if several sweep points
  land inside its half-power band (width ~ f/Q). 200 log steps over 20-2000 Hz
  is 2.33 % per step against a 4 % band at zeta = 0.02 — only 1.7 points, which
  silently under-reports Q and misses peak amplitude. The panel now computes
  this and says how many steps are actually needed.
- Harmonic setup states plainly that the sweep is force-driven, that a linear
  analysis scales exactly with input (so 1 N reads directly as a transfer
  function), and that shaker qualification specifies base acceleration, which
  is not implemented yet.
- Fixed: the FRF peak table was appended from a `requestAnimationFrame`
  callback, which raced with the panel re-render that opening results triggers
  — the table was silently dropped. Peaks are computed synchronously now; only
  the canvas draw is deferred.

## 0.6.0

- **Banded contours**, the commercial-post-processor look: results are
  quantised into discrete colour levels with hard boundaries instead of a
  smooth gradient. Selectable 5 / 9 / 13 / 18 / 27 bands or smooth; 9 is the
  default, matching Ansys. The legend draws matching solid blocks with the
  value at every band boundary.
- **Rainbow palette** (blue → cyan → green → yellow → red), the classic
  structural-FEA scale, now the default. Turbo remains available.
- Banding is done in the shader by snapping to band centres, so changing bands
  or palette restyles the live result without refetching the field.

- **Fixed: a silent result-scrambling bug in the MED reader.** gmsh's MED
  writer stores node coordinates component-major, and the reader's fallback
  heuristic guessed interleaved — producing a nonsense 90x90x90 bounding box
  and scrambling every value across nodes. The bounding-box check masked it for
  normal projects; anything falling back would have shown plausible-looking
  but wrong contours. The fallback now scores candidate layouts by how
  distinguishable their coordinate columns are, and agrees with the
  bbox-validated answer. Regression test added.
- **Fixed: a failed re-run could present the previous run's results.** Only
  `result.med` and `meta.json` were cleared before solving, so stale CSV tables
  survived, satisfied the "did we get anything?" check, and were reported as
  current. All run artifacts are now cleared first.
- Fixed: the demo solver wrote fields in the wrong interlace and computed them
  from gmsh's node order rather than the file's, both of which scrambled the
  demo contours. Measured edge-to-edge field variation dropped from 0.19 of
  full range to 0.008.

## 0.5.1

First real **modal** run: code_aster extracted all 10 modes correctly
(267.5 Hz … 8110.7 Hz, error norms ~1e-10, Sturm-verified) and then Lattice
discarded them.

- **Fixed: `RECU_TABLE(NOM_PARA='NUME_ORDRE')` aborted every modal and
  harmonic run.** A `MODE_MECA` numbers its modes with **`NUME_MODE`**;
  `NUME_ORDRE` does not exist on it, so the command raised and killed the job.
- **Fixed: output ordering threw away completed work.** The frequency table was
  written *before* the MED, so the abort happened before any mode shape was
  saved and the run directory was left empty — nothing to recover. The MED is
  now written first, by `IMPR_RESU`, the most version-stable command here.
- **Every table is now non-fatal.** A `.comm` executes as Python and
  code_aster `<EXCEPTION>` errors are catchable, so each optional output
  (frequencies, model mass, effective mass, reactions, bolt end forces) is
  emitted inside `try/except`. A parameter one version doesn't publish now
  prints a note and continues instead of destroying the solve.
- New tests: generated decks must parse as Python (guards the try/except
  indentation), must not contain `NUME_ORDRE`, and must write the MED before
  any table.

## 0.5.0

- **Orientation triad** in the bottom-right corner: RGB = XYZ, the CAD
  convention. Rendered as a separate scissored viewport so it always sits on
  top, never scales with the model, and turns as you orbit.
- **Boundary-condition and load symbols in 3D**, following the conventions the
  commercial tools use (Abaqus draws a distinct arrow per type; Ansys colours
  supports blue and loads red; textbook FEA uses ground-triangles for encastre
  and rollers for frictionless):

  | Item | Symbol |
  |---|---|
  | Fixed support | violet cone into the face + ground pad |
  | Frictionless / symmetry | violet rollers on the face |
  | Prescribed displacement | violet arrow into the face |
  | Force | amber arrow along the force vector |
  | Pressure | amber arrows pressing into the face |
  | Gravity | one large amber arrow at the model centre |
  | Rotational velocity | amber curved arrow about the spin axis |
  | Remote force / moment | amber arrow at the point + dashed RBE3 spider legs |
  | Bolt | steel-blue shank with head and nut |

  Symbols are spread *spatially* across a face (grid sampling of the
  tessellation) rather than by vertex index, which otherwise clustered them
  wherever the mesher happened to emit vertices.

## 0.4.2

**code_aster ran a Lattice-generated deck end to end for the first time**, on
Windows/WSL2. It reached `FIN()` with no solver error — the failure was in
Lattice's own logging, after the physics was finished.

- **Fixed: `UnicodeEncodeError` killed runs on Windows.** The run log was
  opened without an explicit encoding, so Python used the locale codec
  (cp1252), which cannot represent code_aster's French output. Every text file
  the app opens (18 call sites: logs, run.comm/export, project.json, meshes,
  result tables) is now explicitly UTF-8.
- Log writing can no longer fail a run: by the time output streams, the solve
  is already done, so write errors are swallowed rather than raised.
- **Result recovery.** `POST /api/projects/{pid}/results/{aid}/reparse` rebuilds
  results from whatever the solver left on disk. A failed job now automatically
  attempts recovery, and offers a "Recover results from files" button — a
  completed solve is never thrown away because a later step tripped.

## 0.4.1

- **Fixed: Ctrl+C could not stop the server on Windows.** Child processes
  (`wsl.exe`, `docker`, the gmsh worker) were spawned into the parent's console
  process group, so a console Ctrl+C hit them too and a child that mishandles
  it left the server unkillable. Children are now spawned isolated
  (`CREATE_NEW_PROCESS_GROUP` on Windows, `start_new_session` on POSIX).
- Shutdown is now guaranteed: the first Ctrl+C terminates every tracked child
  and shuts down cleanly; a **second Ctrl+C force-quits immediately**
  regardless of what any child is doing. `Ctrl+Break` works too on Windows.
- Every child process is tracked and reaped on exit, so no orphaned gmsh or
  solver processes survive the server.

## 0.4.0

- **`--demo-solver`**: a bundled stand-in solver that fabricates results so the
  entire workflow can be exercised without code_aster. It writes a real MED
  file (mesh section produced by gmsh's MED writer) plus genuine-format
  code_aster CSV tables, so the whole downstream pipeline runs for real. The UI
  shows an unmissable banner — results are invented, never for engineering use.
- This validated the highest-risk component end to end: **the MED reader
  correctly parsed a file written by a real MED writer**, auto-detecting the
  coordinate layout (skin bounding box matched the model exactly), parsing T10
  connectivity, extracting boundary faces, and packaging contours. Static and
  modal both render.
- Docker findings, recorded in the README so nobody repeats them:
  `codeastersolver/codeaster-seq` is a 2019 image with the legacy `as_run` and
  **no solver binary**; `simvia/code_aster:17.4.22` is the right image but its
  amd64 binaries hit `Illegal instruction` under Rosetta on Apple Silicon
  (they need AVX), and forcing QEMU stops the Docker daemon from starting.

## 0.3.3

Diagnostics, after two rounds of "the button is still grey" that were both
environment staleness rather than logic:

- **Version-skew banner.** The UI knows its own build and compares it to the
  server's. A running Python process holds the old code in memory, so a
  `git pull` alone changes nothing — the page now says so in red instead of
  presenting a mysteriously dead button. Hovering the wordmark shows both
  versions.
- **`lattice doctor` now reports live project state**: per project it lists
  analyses/loads/bolts, whether it is meshed, and *exactly what is blocking a
  run* — or "nothing — should be clickable". Also prints the workspace path to
  delete for a clean slate.
- Startup banner prints the version and the restart-after-pull reminder.

## 0.3.2

- **Fixed: the 0.3.1 button fix could not reach the browser.** `index.html`
  version-stamps `main.js?v=…`, but ES module imports inside it (`./ui.js`,
  `./viewer.js`, …) carry no query string, so the browser kept serving a cached
  `ui.js` — which is exactly where the `el()` fix lived. The UI is now served
  `Cache-Control: no-store` and never answers `304`. Caching a local
  single-user tool bought nothing and cost a shipped fix.

## 0.3.1

- **Fixed: "Run analysis" was permanently disabled.** The DOM helper called
  `setAttribute("disabled", false)`, and because `disabled` is a boolean
  attribute it disables on *presence* — `disabled="false"` still disables. The
  button could never be clicked, in any state. Boolean attributes are now only
  set when true.
- The run button now lists **every** reason it is blocked (no solver, unmeshed,
  missing material, no support, no load) instead of greying out silently.
- **Recheck solver** button + `POST /api/config/recheck`: re-runs detection
  without restarting the server, for when code_aster is installed or configured
  while Lattice is already running.

## 0.3.0

- **Frictionless / symmetry support** (`FACE_IMPO` `DNOR=0`) — normal-only
  constraint, the Ansys "Frictionless Support" and symmetry-plane condition.
- **Remote force / moment** — a coupling node off the body, RBE3-distributed to
  picked faces (`FORCE_NODALE` + `LIAISON_RBE3`). This is the only way to apply
  a **moment** to solid elements, which have no rotational DOF.
- **Rotational velocity** (`ROTATION`) — centrifugal body load in rpm about an
  axis and center. Excluded from harmonic sweeps by design.
- Stale-mesh guard extended to remote loads (their coupling node is created
  during meshing).
- Fixed: bolt-force table mapped rows to bolts by table order instead of parsing
  the `BOLT<n>` label — wrong names/preload ratios when the solver reordered
  blocks.
- Fixed: job-output drawer opened expanded on load, eating 150 px of viewport.
- Fixed: `CALC_CHAMP` stress recovery now restricted to volume groups, and
  `EFGE_ELNO` to beam groups, so mixed solid/beam models don't ask for
  solid stresses on beams.
- Fixed: numpy warnings from degenerate normals during cylinder fitting.

## 0.2.0

- **Bolted joints**: preloaded beam bolts (`POU_D_T` shank + `LIAISON_RBE3`
  spiders + `PRE_EPSI` axial pre-strain), cylinder auto-detection with M-size
  and preload suggestions, per-bolt axial/shear/bending tables from
  `EFGE_ELNO`.
- **Ties**: `LIAISON_MAIL` glued face-to-volume constraints for non-conformal
  interfaces.
- `bolted_plates.step` example.

## 0.1.0

- STEP import for parts and assemblies with automatic conformal bonding.
- Tet10 meshing with curvature adaptivity and per-face local refinement.
- Static, modal, and harmonic (modal superposition) analyses on code_aster via
  WSL2 / native / docker.
- three.js workbench UI with face picking, contours, mode animation, FRF plots.
