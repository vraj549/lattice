# Changelog

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
