# Solvers

Lattice drives two, on the same model. Geometry, mesh, materials, supports,
loads and contacts are shared; only the deck written at the end differs.

| | code_aster | CalculiX |
|---|---|---|
| Install | WSL2, Docker, or native Linux | package manager, single binary |
| Static | yes | yes |
| Modal | yes | yes |
| Harmonic (sine sweep) | yes | — |
| Random vibration | yes | — |
| Shock (SRS / classical pulse) | yes | — |
| Contacts | yes | yes |
| Bolts (beam + spider + preload) | yes | — |
| Ties | yes | — |
| Remote force / moment | yes | — |
| Frictionless supports | any face | planar faces only |

Pick the engine per analysis in **Analysis Settings → Solver**, or on the
analysis itself. Anything the chosen engine cannot do is listed as a blocker
*before* you run, with a one-click switch to an engine that can.

## CalculiX runs single-threaded, deliberately

Its multithreaded factorization returns silently wrong answers. Measured by
re-running one unchanged deck for a cantilever with a known exact answer:

| threads | runs | wrong | worst error |
|---|---|---|---|
| 1 | 14 | 0 | 0.00 % |
| 2 | 14 | 1 | 43 % |
| 3 | 14 | 4 | 52 % |
| 4, 6, 8 | many | frequent | up to 68 % |

Exit code 0 every time, nothing in the log. This is a correctness setting, not
a performance one.

`LATTICE_CCX_THREADS=n` raises it. If you do, verify your own build first: run
`pytest tests/test_ccx.py` at that thread count and check the cantilever still
matches theory.

Characterised on ccx 2.23 (Homebrew, Apple Silicon, SPOOLES). Other builds may
differ — in either direction.

## Accuracy

Both engines are checked against closed-form results in the test suite:

- Cantilever tip deflection vs `PL³/3EI` + shear — 0.00 %
- First bending frequency vs `1.875²/2π · √(EI/mL⁴)` — within 6 %

## Configuration

| Variable | Effect |
|---|---|
| `LATTICE_ASTER_MODE` | `native` \| `wsl` \| `docker` \| `none` |
| `LATTICE_ASTER_CMD` | how to invoke `run_aster` |
| `LATTICE_WSL_DISTRO` | WSL distribution name |
| `LATTICE_CCX_CMD` | CalculiX binary |
| `LATTICE_CCX_THREADS` | CalculiX threads (default 1 — read above) |
| `LATTICE_NCPUS` | code_aster threads |
| `LATTICE_MEMORY_MB` | code_aster memory limit |

Or the same keys under `[solver]` in `lattice.toml`.
