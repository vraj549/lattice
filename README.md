# Lattice

**Browser-based FEA workbench.** Import STEP parts or assemblies, mesh them, and run
**static structural**, **modal**, and **harmonic (frequency response)** analyses on
[code_aster](https://code-aster.org) — from a SimScale-style UI in your browser.
Everything runs locally; nothing leaves your machine.

```
STEP  ─▶  OCCT/gmsh import  ─▶  tet10 mesh  ─▶  code_aster (MUMPS)  ─▶  contours · modes · FRF
              (fragment = bonded assemblies)         static / modal / harmonic
```

| | |
|---|---|
| Geometry | STEP (AP203/AP214) parts and assemblies; shared faces become bonded interfaces automatically |
| Mesh | quadratic tetrahedra (Tet10), curvature-adaptive, per-face refinement, quality report |
| Static | force, pressure, remote force/moment, gravity, rotational velocity; fixed / frictionless / prescribed supports; von Mises, principal, displacement, reactions |
| Modal | Sorensen/ARPACK via `CALC_MODES`; frequencies, animated mode shapes, effective-mass participation |
| Harmonic | modal superposition `DYNA_VIBRA`; log/linear sweep, modal damping ζ, FRF (module + phase) at probes, optional full fields at chosen frequencies |
| Bolts | preloaded beam bolts: Timoshenko shank + RBE3 spiders onto picked hole/bearing faces, `PRE_EPSI` preload, per-bolt axial/shear/bending force table |
| Ties | `LIAISON_MAIL` glued face-to-volume constraints for non-conformal interfaces |
| Units | mm / N / MPa / tonne·mm⁻³ / Hz (STEP files in mm) |

---

## Install (all platforms)

Requires **Python 3.9+**. No Node, no build step.

```bash
git clone <this-repo>
cd lattice
python -m venv .venv
# Windows:  .venv\Scripts\activate      macOS/Linux:  source .venv/bin/activate
pip install -e .
python -m lattice_fea        # opens http://127.0.0.1:8765
```

Without a solver installed, import, setup, and meshing all work — only *Run
analysis* is disabled. Check your environment any time with:

```bash
python -m lattice_fea doctor
```

`doctor` lists every project and states exactly what is blocking a run
("not meshed", "no solver", "solids without material", or "nothing — should be
clickable").

### Trying the interface without a solver

```bash
python -m lattice_fea --demo-solver
```

Runs with a stand-in that fabricates plausible results so the whole workflow —
solve, contours, mode animation, FRF curves, bolt tables — can be exercised
with no code_aster present. **Results are invented, not computed**; the UI says
so in a banner you cannot miss. Useful for demos, for UI development, and for
telling "the tool is broken" apart from "my solver isn't set up".

> **After a `git pull`, restart the server.** The Python process holds the code
> in memory, so pulling alone changes nothing. The UI shows a red banner when
> its build and the server's version disagree.

### Stopping it

`Ctrl+C` once shuts down and kills any running mesh/solve. **A second `Ctrl+C`
force-quits** regardless of what a child process is doing. On Windows
`Ctrl+Break` also works, and if the console is wedged entirely:

```
taskkill /F /IM python.exe
```

## Solver setup (code_aster)

Lattice looks for `run_aster` in this order: native `PATH` → WSL2 distros → docker
image. Auto-detection is printed at startup; override with env vars or `lattice.toml`
(see below).

### Windows — WSL2 (recommended)

The Salome-Meca / code_aster team publishes a ready-made WSL2 distribution
([forum post](https://forum.code-aster.org/public/d/28014-salome-meca-20241-on-wsl2-ready-to-use-distribution)):

```powershell
wsl --install --no-distribution        # if WSL2 is not set up yet, then reboot
# download smeca-2024.1-wsl2-verified.tar.gz from the forum post, then:
wsl --import smeca-2024 C:\smeca C:\Downloads\smeca-2024.1-wsl2-verified.tar.gz
wsl -d smeca-2024 bash -lc "run_aster --version"   # sanity check
```

Restart Lattice — it scans WSL distros for `run_aster` automatically. If your distro
uses a wrapper script, point Lattice at it:

```powershell
set LATTICE_ASTER_MODE=wsl
set LATTICE_WSL_DISTRO=smeca-2024
set LATTICE_ASTER_CMD=run_aster
```

> **RAM note:** WSL2 takes 50 % of system RAM by default. On a 16 GB machine, create
> `%UserProfile%\.wslconfig` with `[wsl2]` / `memory=10GB`, and set
> `LATTICE_MEMORY_MB=8000` so code_aster's limit fits inside it.

### Linux — native

```bash
conda install -c conda-forge code-aster     # linux-64
# or a distro package / source build that puts run_aster on PATH
```

### Any platform — docker

```bash
docker pull simvia/code_aster:17.4.22
export LATTICE_ASTER_MODE=docker
export LATTICE_DOCKER_IMAGE=simvia/code_aster:17.4.22
```

`simvia/code_aster` tracks current code_aster (v17/v18) and ships `run_aster`.
Note `codeastersolver/codeaster-seq` is **not** usable: it was last built in
2019, ships the legacy `as_run` launcher, and contains only dependencies — no
solver binary.

> **Apple Silicon: verified not to work.** These images are amd64-only, and
> under Docker's Rosetta emulation the v17 binaries die with `Illegal
> instruction` — the containers require AVX, which Rosetta-for-Linux does not
> provide. Forcing QEMU instead prevented the Docker daemon from starting.
> On an M-series Mac use `--demo-solver` for the interface and run real solves
> on x86 (WSL2 or Linux).

### `lattice.toml` (optional, next to where you launch)

```toml
[solver]
mode = "wsl"                  # native | wsl | docker | none
wsl_distro = "smeca-2024"
cmd = "run_aster"
memory_mb = 8000
time_limit_s = 14400
ncpus = 6
```

## Using it

1. **New project** → name it and upload a STEP. Assemblies are *fragmented* on import:
   coincident faces between parts are merged, so the mesh is continuous and parts are
   bonded — no contact setup needed. The Connections tree entry lists what got bonded.
2. **Materials** — click each solid, pick from the library (or note the checks panel
   nagging you).
3. **Analyses** — *+ add* opens a dialog: static, modal, harmonic or random.
   Each analysis owns its own supports and loads, so only what that study needs
   appears beneath it. A base-driven harmonic or a random study is driven
   through its supports, so it offers no loads at all.
4. **Supports / Loads / Probes** — add one under an analysis, then click faces
   (or a point) in the viewport and press *Done*. Faces stay tinted violet
   (supports) / amber (loads).
5. **Mesh** — set a target size or take the default, *Generate mesh*. Watch the
   node/DOF count and the memory estimate against your solver limit.
6. **Run analysis** — the job log streams the real code_aster output.
7. **Results** appear as their own branch under the analysis: contours with
   deformation scaling and banded colour, mode shapes with the effective-mass
   table, FRF curves with annotated peaks and Q, random-vibration g RMS with a
   Miles' cross-check, bolt end forces, reactions. Every panel exports to CSV,
   and *Export nodal values* dumps the whole field on screen.

The tree is one hierarchy you work top to bottom — geometry, connections,
probes and mesh are shared by the model; each analysis owns its settings, its
boundary conditions and a **Solution** node holding its results:

```
bracket assembly                 3 solids
  Geometry / Connections / Probes / Mesh
  Static structural              ✓
    Analysis Settings
    Fixed base                   2 faces
    Bearing load                 1 face
    Solution                     ✓
      Contours · Bolt forces · Reactions
```

Every branch collapses (state is remembered per project), rows carry a type
icon and a status mark, and the **+** on a row inserts into it. Arrow keys
navigate; left/right collapse and expand. Panes are resizable — drag the
separators, or focus one and use the arrow keys (Shift for coarse steps, Home
to reset).

**Status symbols.** Each analysis carries a mark in the tree: green ✓ results
are current, amber ! **out of date**, red ✕ the run failed. Out of date means
the mesh, materials, connections or boundary conditions changed after the run —
Lattice fingerprints all of it and re-checks as you edit, so results that no
longer describe the model on screen say so instead of being presented as
current.

Try it immediately with `examples/bracket_assembly.step` (regenerate with
`python examples/make_examples.py`).

## Bolted joints

Lattice implements the standard linear bolt idealization used by Ansys beam
connections and Nastran spider models:

```
      RBE3 spider          POU_D_T beam           RBE3 spider
 hole faces (side A) ──▶ ● ══════════════ ● ◀── hole faces (side B)
                      end node          end node
```

1. **Bolts → + add**, then pick the hole cylinder (or bearing face) on each
   side. Cylindrical faces are auto-detected — the panel shows the hole ⌀ and
   suggests a nominal size (M3–M24).
2. Set the **preload**; the *Suggest* button proposes 65 % of class-8.8 yield
   on the tensile stress area. Preload is applied as an axial pre-strain
   (`PRE_EPSI`, ε = −F/EA) in static runs.
3. After solving, the **Bolt forces** table lists per-bolt axial force
   (preload + working load), resultant shear, and bending from `EFGE_ELNO` —
   the inputs a VDI 2230-style margin calc needs.

**Patterning.** Build one joint, then use **Copy to other holes…** in its
Pattern section: pick each target hole and Lattice creates a bolt there with
the same size, preload and modulus, and with *both* face sets mapped across by
the transform that takes the reference face onto the target — so the nut-side
hole comes with it, including when the target runs on a different axis. Where a
pure offset would miss (plates of unequal thickness) it falls back to the
cylinder sharing the target's axis on the other part.

It refuses rather than guesses: a hole that already has a bolt is skipped, an
ambiguous mate is reported instead of picked, and a copy that can only find one
side of the joint is not created at all. Everything skipped is named in the job
log. Bolt beams are built at mesh time, so re-mesh after patterning — the Mesh
row will tell you.

Physics honesty: the joint interface itself is *bonded* (fragment or ties), so
gapping/separation under load is not modeled — that needs nonlinear contact.
Preload does not stiffen modal/harmonic results (linear analyses have no
stress stiffening); bolt beams do contribute stiffness and mass everywhere.

## Boundary conditions and loads

The linear-analysis set that Ansys / Abaqus / SimScale share:

| Industry name | Lattice | code_aster |
|---|---|---|
| Fixed support / ENCASTRE | ✅ | `DDL_IMPO` DX=DY=DZ=0 |
| Frictionless support / symmetry | ✅ | `FACE_IMPO` `DNOR=0` |
| Displacement | ✅ per-DOF, blank = free | `DDL_IMPO` |
| Force (total N on faces) | ✅ | `FORCE_FACE` (÷ area → traction) |
| Pressure | ✅ | `PRES_REP` |
| **Remote force / moment** | ✅ RBE3 to a remote point | `FORCE_NODALE` + `LIAISON_RBE3` |
| Standard earth gravity | ✅ | `PESANTEUR` |
| **Rotational velocity** | ✅ rpm + axis + center | `ROTATION` |
| Bolt pretension | ✅ see above | `PRE_EPSI` |
| Bearing load, thermal, moment-on-face | ❌ roadmap | — |

Remote loads are the only way to apply a **moment** — solid elements have no
rotational DOF, so a moment needs a coupling node, exactly as in Ansys. Note
that adding or moving a remote load invalidates the mesh (its coupling node is
created during meshing); Lattice refuses to solve a stale mesh rather than
silently using the old one.

Rotational velocity is a static body load and is deliberately excluded from
harmonic sweeps.

### Contact / connection types, and where Lattice stands

| Type (industry name) | Lattice v0.2 | Notes |
|---|---|---|
| Bonded, conformal | ✅ automatic | assemblies are fragmented on import; coincident faces share mesh |
| Bonded/tied, non-conformal (MPC) | ✅ Ties | `LIAISON_MAIL` face→volume gluing |
| Preloaded fastener (beam + spider) | ✅ Bolts | RBE3 distributing couplings, like Ansys "Deformable" |
| No-separation, frictionless, frictional | ❌ roadmap | nonlinear (`DEFI_CONTACT`/`STAT_NON_LINE`) |
| Point fasteners (CBUSH/CFAST, Huth) | ❌ roadmap | relevant for sheet stacks |

## What to know before trusting it

- **v0.1 scope:** linear statics, real modes, modal-superposition harmonic. No
  nonlinear contact, no thermal, no shells/midsurfacing (thin parts: use 2–3 solid
  elements through thickness), no buckling, no PSD/random vibration yet.
- **Assemblies bond by shared geometry.** Parts that merely *touch* without
  coincident faces are not connected — the mesh step warns about disconnected groups.
  Interference fits and frictional contact are out of scope for now.
- **Force loads** are applied as uniform traction (total force ÷ face area).
  Moments and remote loads are not implemented yet.
- **Sanity-check your results** the way you would with any solver: reaction sums are
  printed for statics; mode 1 of a healthy constrained model should not be ~0 Hz.
- The generated `run.comm` for every analysis is kept in
  `workspace/projects/<id>/runs/<analysis>/` — it is a plain code_aster command file
  you can read, edit, and rerun by hand with `run_aster run.export`.

## Development

```bash
pip install -e .[dev]
python examples/make_examples.py
pytest                        # geometry → mesh → comm pipeline, no solver needed
```

Backend: FastAPI + gmsh (subprocess-isolated) + h5py MED reader.
Frontend: vanilla ES modules + three.js (vendored, no build step).
`window.lattice` in the browser console exposes app state for debugging.

## License

MIT. code_aster (GPL), gmsh (GPL), and three.js (MIT) are separate installs /
vendored components under their own licenses.
