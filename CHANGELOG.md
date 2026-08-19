# Changelog

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
