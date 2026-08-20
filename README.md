# Lattice

**Browser-based FEA workbench.** Import STEP parts or assemblies, mesh them,
and run **static**, **modal**, **harmonic** and **random vibration** analyses
on [code_aster](https://code-aster.org) or
[CalculiX](http://www.dhondt.de/) — from a workbench UI in your browser.
Everything runs locally; nothing leaves your machine.

```
STEP ─▶ OCCT/gmsh import ─▶ tet10 mesh ─▶ code_aster or CalculiX ─▶ contours · modes · FRF
          bonded or                          static / modal /
          separate parts                     harmonic / random
```

| | |
|---|---|
| Geometry | STEP (AP203/AP214) parts and assemblies. Import bonded (coincident faces merged, conformal) or as separate parts with contact between them |
| Mesh | quadratic tetrahedra (Tet10), curvature-adaptive, per-face refinement, quality and memory report |
| Static | force, pressure, remote force/moment, gravity, rotational velocity; fixed / frictionless / prescribed supports; von Mises, principal, displacement, reactions |
| Modal | frequencies, animated mode shapes, effective-mass participation |
| Harmonic | force- or base-driven sweep, modal damping ζ, FRF at probes with annotated peaks and Q |
| Random | PSD breakpoint table in g²/Hz, response PSD, g RMS and 3σ, Miles' cross-check, modal-truncation check |
| Contacts | bonded, no-separation, frictionless, frictional (μ) |
| Bolts | beam shank + RBE3 spiders, preload as axial pre-strain, per-bolt force **and stress** vs yield, pattern one joint onto every other hole |
| Solvers | code_aster (everything) or CalculiX (static, modal), chosen per analysis |
| Units | mm / N / MPa / tonne·mm⁻³ / Hz |

---

## Quick start

```bash
git clone https://github.com/vraj549/lattice.git
cd lattice
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip                   # needed: see below
pip install -e .
python -m lattice_fea                                 # http://127.0.0.1:8765
```

> The `pip` bundled with a Python 3.9 virtual environment is too old for an
> editable install of a `pyproject.toml`-only package and fails with *"editable
> mode currently requires a setuptools-based build"*. Upgrading pip first is
> the fix; `pip install .` (no `-e`) also works if you would rather not.

Import, setup and meshing work with no solver installed — only **Run analysis**
is disabled. Add one:

```bash
# macOS / Linux — one command, covers static and modal
brew install costerwi/homebrew-calculix/calculix-ccx

# Windows — code_aster via WSL2, covers everything
# see docs/INSTALL.md
```

Then check what you have:

```bash
python -m lattice_fea doctor
```

**[docs/INSTALL.md](docs/INSTALL.md)** has the full per-platform detail,
configuration, and troubleshooting.

Try it immediately with `examples/bracket_assembly.step` (regenerate the
examples with `python examples/make_examples.py`).

## Documentation

- **[docs/INSTALL.md](docs/INSTALL.md)** — installing the app and a solver on
  each platform, configuration, updating, troubleshooting.
- **[docs/SOLVERS.md](docs/SOLVERS.md)** — code_aster vs CalculiX: what each
  covers, how to choose, and why CalculiX runs single-threaded.
- **[docs/METHODS.md](docs/METHODS.md)** — what each result is, how it is
  computed, and where it stops being valid.

---

## Using it

1. **New project** → name it, upload a STEP, and choose how the assembly is
   treated. **Bonded** merges coincident faces so parts share nodes and are
   welded together — right for a weldment, and cheapest. **Separate parts**
   keeps both surfaces so you can define contact between them, which is what a
   bolted joint needs if it has to be able to slip or open. This cannot be
   changed later without re-importing.
2. **Materials** — click each solid and pick from the library, or define your
   own with *New custom material*.
3. **Connections** — *Detect contacts* finds the interfaces between separate
   parts. Each starts bonded; change any that can slide or separate. Bolts and
   ties are added here too.
4. **Analyses** — *+ add* opens a dialog: static, modal, harmonic or random.
   Each analysis owns its own supports and loads, so only what that study needs
   appears beneath it. A base-driven harmonic or a random study is driven
   through its supports, so it offers no loads at all.
5. **Supports / Loads / Probes** — add one under an analysis, then click faces
   (or a point) in the viewport and press *Done*. Faces stay tinted violet
   (supports) / amber (loads).
6. **Mesh** — set a target size or take the default, *Generate mesh*. Watch the
   node/DOF count and the memory estimate against your solver limit.
7. **Run analysis** — pick the solver on the analysis itself. Anything the
   chosen engine cannot do is listed *before* you run, with a one-click switch
   to one that can. The job log streams the solver's real output.
8. **Results** appear as their own branch under the analysis: contours with
   deformation scaling and banded colour, mode shapes with the effective-mass
   table, FRF curves with annotated peaks and Q, random-vibration g RMS with a
   Miles' cross-check, bolt forces and stresses, reactions. Every panel exports
   to CSV, and *Export nodal values* dumps the whole field on screen.

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
   suggests a nominal size. Sizes run **M1.6–M8** (ISO coarse) and
   **#0-80, #2-56, #4-40, #6-32** (unified inch).
2. Pick a **grade** — ISO class 8.8 / 10.9 / 12.9, ASTM A574 alloy socket head,
   A2-70 / 18-8 stainless, titanium Grade 5, PEEK or PEEK GF30 — or type a
   yield stress directly. Modulus travels with the grade, so a PEEK screw is
   not silently modelled as steel. Set the
   **preload**; *Suggest* proposes 65 % of yield on the tensile stress area.
   Preload is applied as an axial pre-strain (`PRE_EPSI`, ε = −F/EA) in static
   runs, which returns the beam force as exactly −F.

The beam is sized on the **tensile stress area**, not the major diameter — a
bolt carries axial load through its thread, and an M1.6 modelled on its ⌀1.6
shank comes out 58 % stiffer than the real screw. The panel shows the
equivalent diameter it uses.
3. After solving, **Bolt forces** lists per-bolt axial, shear and bending with
   the resulting stresses against yield.

### Sizing: how much preload does each bolt need?

**Solution → Bolt sizing** answers that, per bolt, following VDI 2230:

1. Run the model with **preload set to zero**. With no preload each bolt beam
   carries exactly its share of the load path — the external load the joint
   has to be preloaded against.
2. Bolt sizing reports, for every bolt: the load factor Φ, the clamp force
   needed against slip and against opening, embedding loss, the **required
   assembly preload** and its tightening torque, what the bolt can actually
   take, and the margins. Grip length is measured from the mesh, hole diameter
   from the detected cylinder.
3. Enter the preload it gives you and run again to verify — with the assembly
   imported as *separate parts* and a frictional contact, so the interface can
   genuinely open or slip if the preload is short.

If the required preload exceeds what the bolt can hold, it says **no feasible
preload** rather than rounding down: the joint needs a bigger bolt, more bolts,
or a tightening method that scatters less. Sizing from a run that already has
preload applied is refused — the beam force there is the bolt force, and
feeding it back would count the preload twice.

Full method in [docs/METHODS.md](docs/METHODS.md#bolted-joints--sizing-and-preload).

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

**Preload only does work if the interface can move.** In a bonded model the
parts can neither separate nor slip, so the clamp load has no job — the bolt
carries preload plus its share of the external load and that is all you learn.
To see gapping or joint slip, import the assembly as *separate parts* and give
the interface a frictionless or frictional contact. That makes the static solve
nonlinear, and the run applies preload first, then the external load.

Preload does not stiffen modal or harmonic results — those are linear and have
no stress stiffening. Bolt beams do contribute stiffness and mass everywhere.

## Boundary conditions and loads

The linear-analysis set that Ansys / Abaqus / SimScale share:

| Industry name | code_aster | CalculiX | Emitted as |
|---|---|---|---|
| Fixed support / ENCASTRE | ✅ | ✅ | `DDL_IMPO` / `*BOUNDARY` |
| Frictionless support / symmetry | ✅ any face | ✅ planar only | `FACE_IMPO DNOR=0` / `*TRANSFORM` |
| Prescribed displacement | ✅ per-DOF, blank = free | ✅ | `DDL_IMPO` / `*BOUNDARY` |
| Force (total N on faces) | ✅ | ✅ | `FORCE_FACE` / consistent `*CLOAD` |
| Pressure | ✅ | ✅ | `PRES_REP` / `*DLOAD` on element faces |
| Standard earth gravity | ✅ | ✅ | `PESANTEUR` / `*DLOAD GRAV` |
| Rotational velocity | ✅ rpm + axis + centre | ✅ | `ROTATION` / `*DLOAD CENTRIF` |
| **Remote force / moment** | ✅ RBE3 to a remote point | ❌ | `FORCE_NODALE` + `LIAISON_RBE3` |
| Bolt pretension | ✅ | ❌ | `PRE_EPSI` |
| Bearing load, thermal, moment-on-face | ❌ | ❌ | — |

Remote loads are the only way to apply a **moment** — solid elements have no
rotational DOF, so a moment needs a coupling node, exactly as in Ansys. Adding
or moving a remote load invalidates the mesh (its coupling node is created
during meshing); Lattice refuses a stale mesh rather than silently using it.

Rotational velocity is a static body load and is deliberately excluded from
harmonic sweeps.

### Connections and contact

| Type (industry name) | Lattice | Notes |
|---|---|---|
| Bonded, conformal | ✅ automatic | import as *bonded*: coincident faces merge, parts share nodes |
| Bonded, non-conformal (MPC) | ✅ | `LIAISON_MAIL` face→volume gluing, stays linear |
| No separation | ✅ | cannot gap, free to slide — nonlinear |
| Frictionless | ✅ | can gap and slide freely — nonlinear |
| Frictional (Coulomb μ) | ✅ | can gap, slides above μN — nonlinear |
| Preloaded fastener (beam + spider) | ✅ | RBE3 distributing couplings, like Ansys "Deformable" |
| Point fasteners (CBUSH/CFAST, Huth) | ❌ | relevant for sheet stacks |

Anything that can slide or separate makes the static solve **nonlinear** —
`STAT_NON_LINE` + `DEFI_CONTACT` on code_aster, `*CONTACT PAIR` on CalculiX —
because whether the surfaces touch is part of the answer. Modal and harmonic
are linear by definition and use the bonded state.

To use contact at all, import the assembly as **separate parts**. A bonded
import merges the coincident faces into one, so there is no second surface left
to slide against.

## What to know before trusting it

**Scope.** Linear elastic, small displacement, small strain. Static, modal,
modal-superposition harmonic, and random vibration derived from a base sweep.
The one nonlinearity is contact status; the material is always linear elastic.

Not modelled: plasticity, creep, large rotation, buckling, thermal, shells or
midsurfacing (for thin parts use 2–3 solid elements through the thickness).

**A wrong answer looks exactly like a right one.** The arithmetic does not stop
working when the physics does — a load ten times too large returns a deflection
ten times too large. Lattice checks what it can and says so:

- Results are fingerprinted against the model and marked **out of date** the
  moment anything they depend on changes.
- Reactions must balance the applied load; nodes held to zero must not move;
  peak displacement is compared against the size of the part.
- Modal truncation below 90 % effective mass, and sweeps too coarse to resolve
  a peak, are reported — both make a result low without looking wrong.

None of that judges whether the *model* is right. Sanity-check the way you would
with any solver: mode 1 of a healthy constrained model is not ~0 Hz, and
reaction sums should match what you applied.

**Assemblies bond by shared geometry.** Parts that merely touch without
coincident faces are not connected — the mesh step warns about disconnected
groups. Interference fits are out of scope.

**Everything is readable.** The generated `run.comm` / `job.inp` for every
analysis is kept in `workspace/projects/<id>/runs/<analysis>/`, next to the
solver's full log. They are plain solver input files you can read, edit and
rerun by hand.

## Development

```bash
pip install -e ".[dev]"
python examples/make_examples.py
pytest                    # geometry → mesh → deck, plus solver checks
node tests/test_pattern.mjs   # bolt-pattern geometry
```

`pytest` needs no solver for most of it. The CalculiX tests run the real binary
when one is on `PATH` and skip when it is not — they check a cantilever against
`PL³/3EI` and the first bending frequency against beam theory, so they fail if
the toolchain starts producing wrong numbers rather than merely crashing.

Backend: FastAPI + gmsh (subprocess-isolated) + h5py MED reader + a `.frd`
reader for CalculiX.
Frontend: vanilla ES modules + three.js (vendored, no build step).
`window.lattice` in the browser console exposes app state for debugging.

Layout:

```
lattice_fea/
  geometry.py    STEP import, fragmenting, contact-pair detection, tessellation
  meshing.py     tet10 meshing, physical groups, UNV + Abaqus export
  comm_writer.py code_aster decks          ccx_writer.py  CalculiX decks
  med_reader.py  code_aster results        frd_reader.py  CalculiX results
  results.py     one results shape for both engines
  server.py      REST API                  solver.py      job running
  ui/js/         tree.js · ui.js · viewer.js · pattern.js · charts.js
```

## License

MIT. code_aster (GPL), gmsh (GPL), and three.js (MIT) are separate installs /
vendored components under their own licenses.
