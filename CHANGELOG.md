# Changelog

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
