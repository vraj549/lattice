# Changelog

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
