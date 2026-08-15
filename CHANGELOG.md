# Changelog

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
