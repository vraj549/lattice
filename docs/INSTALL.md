# Installing Lattice

Two steps: the app itself, then a solver. The app is the same everywhere; the
solver is the part that differs by platform.

---

## 1. The app

Needs **Python 3.9 or newer**. No Node, no build step, no compiler.

```bash
git clone https://github.com/vraj549/lattice.git
cd lattice
python -m venv .venv
```

Activate it:

| | |
|---|---|
| macOS / Linux | `source .venv/bin/activate` |
| Windows PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows cmd | `.venv\Scripts\activate.bat` |

Then:

```bash
python -m pip install --upgrade pip
pip install -e .
python -m lattice_fea
```

**The pip upgrade is not optional on Python 3.9.** The version bundled with a
3.9 virtual environment (21.x) cannot do an editable install of a package that
has only a `pyproject.toml`, and fails with:

```
A "pyproject.toml" file was found, but editable mode currently
requires a setuptools-based build.
```

The install then appears to succeed while installing nothing, and the app fails
later with `No module named 'gmsh'`. Upgrading pip fixes it. If you would
rather not use an editable install at all, `pip install .` works on any pip —
you just have to re-run it after pulling.

Roughly 400 MB gets pulled in, most of it gmsh and its OCCT geometry kernel.

That opens <http://127.0.0.1:8765>. Import, setup and meshing all work with no
solver installed — only **Run analysis** is disabled.

Check what you have at any time:

```bash
python -m lattice_fea doctor
```

`doctor` lists the Python dependencies, every solver it can find and what each
one can run, and then — per project and per analysis — exactly what is
blocking a run.

---

## 2. A solver

Lattice drives two. You do not need both.

| | code_aster | CalculiX |
|---|---|---|
| Static, modal | yes | yes |
| Harmonic sweep, random vibration, shock | yes | — |
| Bolts, ties, remote loads | yes | — |
| Contacts | yes | yes |
| Effort to install | moderate (WSL2 / Docker / Linux) | one command |

**Rule of thumb:** on Windows install code_aster — it does everything. On macOS
install CalculiX, because code_aster has no working option there (see below).
On Linux, either; code_aster if you want the full feature set.

---

### CalculiX — macOS and Linux

**macOS** (Intel or Apple Silicon):

```bash
brew install costerwi/homebrew-calculix/calculix-ccx
```

**Linux**: `apt install calculix-ccx`, `dnf install calculix`, or a build from
[dhondt.de](http://www.dhondt.de/). Any `ccx` on `PATH` is found automatically.

**Windows**: binaries are available from the CalculiX site, but if you are on
Windows you almost certainly want code_aster instead — it covers more.

Verify:

```bash
ccx_2.23 -v          # or whatever version you installed
python -m lattice_fea doctor
```

> **CalculiX runs single-threaded on purpose.** Its multithreaded factorization
> returns silently wrong answers — measured, reproducibly, with exit code 0 and
> nothing in the log. This is not caution; see
> [SOLVERS.md](SOLVERS.md#calculix-runs-single-threaded-deliberately) for the
> numbers before you consider raising it.

---

### code_aster — Windows via WSL2 (recommended)

The code_aster team publishes a ready-made WSL2 distribution
([forum post](https://forum.code-aster.org/public/d/28014-salome-meca-20241-on-wsl2-ready-to-use-distribution)).

```powershell
wsl --install --no-distribution     # if WSL2 is not set up yet, then reboot
# download smeca-2024.1-wsl2-verified.tar.gz from the forum post, then:
wsl --import smeca-2024 C:\smeca C:\Downloads\smeca-2024.1-wsl2-verified.tar.gz
wsl -d smeca-2024 bash -lc "run_aster --version"      # sanity check
```

Restart Lattice — it scans WSL distributions for `run_aster` automatically. If
yours uses a wrapper script, point at it:

```powershell
set LATTICE_ASTER_MODE=wsl
set LATTICE_WSL_DISTRO=smeca-2024
set LATTICE_ASTER_CMD=run_aster
```

**RAM matters more than you would expect.** WSL2 takes about half the machine's
RAM by default, and that — not Windows — is the ceiling on a solve. On a 16 GB
machine, create `%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=10GB
processors=8
```

then `wsl --shutdown`, and set `LATTICE_MEMORY_MB=8000` so code_aster's own
limit fits inside it. The **ⓘ** button next to the solver status in the app
shows both numbers and warns when they are too close.

---

### code_aster — Linux, native

```bash
conda install -c conda-forge code-aster        # linux-64
```

or a distribution package, or a source build — anything that puts `run_aster`
on `PATH`.

---

### code_aster — Docker

```bash
docker pull simvia/code_aster:17.4.22
export LATTICE_ASTER_MODE=docker
export LATTICE_DOCKER_IMAGE=simvia/code_aster:17.4.22
```

`simvia/code_aster` tracks current code_aster (v17/v18) and ships `run_aster`.

> `codeastersolver/codeaster-seq` does **not** work: last built in 2019, ships
> the legacy `as_run` launcher, and contains only dependencies — no solver
> binary. It was Lattice's default for a while; it is not any more.

> **Apple Silicon: this image cannot run.** It is amd64-only, and under
> Rosetta the v17 binaries die with `Illegal instruction` — they need AVX,
> which Rosetta-for-Linux does not provide. Forcing QEMU instead prevented the
> Docker daemon from starting. **Install CalculiX instead**; it is native and
> fast on Apple Silicon.

---

## Trying the interface with no solver

```bash
python -m lattice_fea --demo-solver
```

Runs with a stand-in that fabricates plausible results, so the whole workflow —
solve, contours, mode animation, FRF curves, bolt tables — can be exercised with
nothing installed. **The numbers are invented, not computed**, and the app says
so in a banner you cannot miss. Useful for demos, and for telling "the tool is
broken" apart from "my solver is not set up".

---

## Configuration

Environment variables, or the same keys under `[solver]` in a `lattice.toml`
next to wherever you launch.

| Variable | Default | Effect |
|---|---|---|
| `LATTICE_ASTER_MODE` | auto | `native` \| `wsl` \| `docker` \| `none` |
| `LATTICE_ASTER_CMD` | `run_aster` | how to invoke it |
| `LATTICE_WSL_DISTRO` | auto | WSL distribution name |
| `LATTICE_DOCKER_IMAGE` | `simvia/code_aster:17.4.22` | image to run |
| `LATTICE_CCX_CMD` | auto | CalculiX binary |
| `LATTICE_CCX_THREADS` | `1` | **read [SOLVERS.md](SOLVERS.md) first** |
| `LATTICE_NCPUS` | cores − 2 | code_aster threads |
| `LATTICE_MEMORY_MB` | `6000` | code_aster memory limit |
| `LATTICE_TIME_LIMIT_S` | `14400` | per-run time limit |

```toml
[solver]
mode = "wsl"
wsl_distro = "smeca-2024"
memory_mb = 8000
ncpus = 6
```

Detection order: environment variables → `lattice.toml` → auto-detect
(`PATH`, then WSL distributions, then Docker). Whatever it settles on is
printed at startup and shown in `doctor`.

---

## Running it

```bash
python -m lattice_fea                 # http://127.0.0.1:8765
python -m lattice_fea --port 9000     # a different port
python -m lattice_fea --no-browser    # do not open a browser
python -m lattice_fea --workspace /path/to/projects
```

Projects live in `workspace/projects/<id>/` next to wherever you launch —
geometry, mesh and every run. Delete that directory to start clean.

### Stopping it

`Ctrl+C` once shuts down and terminates any running mesh or solve. **A second
`Ctrl+C` force-quits** regardless of what a child process is doing — a
`wsl.exe` or `docker` child can otherwise hold the console. On Windows
`Ctrl+Break` also works, and if the console is wedged entirely:

```
taskkill /F /IM python.exe
```

---

## Updating

```bash
git pull
pip install -e .          # only needed if dependencies changed
```

If you installed without `-e`, re-run `pip install .` after every pull.

**Restart the server after pulling.** The Python process holds the code in
memory, so pulling alone changes nothing. The app shows a red banner when its
UI build and the server version disagree, which is what that means.

After an update that changes the mesh format, existing meshes are flagged in
the tree as needing a re-mesh. Re-mesh and run again.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| **Run analysis** greyed out | The panel lists every reason underneath it. `doctor` prints the same list. |
| "no solver" but one is installed | Not on `PATH` for the account running Lattice. Set `LATTICE_ASTER_CMD` / `LATTICE_CCX_CMD` to an absolute path. |
| A run fails with an error you cannot place | The job log names the file it came from; the full output is in `workspace/projects/<id>/runs/<analysis>/log.txt`. |
| Mesh row shows **!** | Boundary conditions, bolts or probes changed since meshing, or the mesh predates a format change. Re-mesh. |
| Red version-mismatch banner | The server is running older code than the browser has. Restart it, then hard-refresh. |
| Solve killed rather than failing | Out of memory. Lower `LATTICE_MEMORY_MB`, or give WSL more RAM (above). The Mesh panel estimates what a model needs. |
