# Shared context for Lattice review agents
#
# Not an agent. Kept here so the ten agent files can stay short and so this
# only has to be corrected in one place. Each agent repeats the parts it needs.

## What Lattice is

A self-hosted, browser-based FEA workbench. STEP in, meshed with gmsh, solved
by **code_aster** or **CalculiX**, results read back in the browser. Static,
modal, harmonic, random vibration and shock. Its centre of gravity is **bolted
joints** — preload, slip, sizing to VDI 2230.

The goal is a workhorse tool an engineer commits real work to, competing with
Ansys Mechanical / SimScale on focus rather than breadth.

## Facts you must not get wrong

- **Units are mm / tonne / s**, so stress is MPa, force N, frequency Hz,
  density tonne·mm⁻³, and **1 g = 9810 mm/s²**. A unit error here is silent.
- Python side: FastAPI, gmsh, numpy, h5py. **No build step** on the front end —
  vanilla ES modules and a vendored three.js.
- code_aster decks are `.comm` files, which are **executed as Python**.
- The project runs on the author's machine with code_aster under WSL2; the
  agent's machine usually has only CalculiX. **You generally cannot run
  code_aster.** Decks are verified as generated text and by parsing them.

## The architectural principle

Prefer a **proven solver chain plus Python post-processing** over an
unverified solver operator. Random vibration, shock and the slip check are all
built this way on purpose: the solver does only what it has already been shown
to do, and the arithmetic lives in Python where it is unit-tested. Weigh any
proposal that adds new solver API against this.

## The verification culture

- Validate on **physics identities**, not on numbers recalled from a table.
  (An attempt to check bolt sizing against remembered VDI values carried a
  consistent 6.5% bias and had to be thrown out.)
- **A check that cannot fail is worse than no check** — it reads as
  verification while verifying nothing.
- State what was measured, and in which direction an error is conservative.

## Bug classes this codebase has actually produced

Look for more of these. Every one shipped and had to be found later.

1. **Shared-session option leaks.** gmsh is one long-lived session;
   `Mesh.SaveGroupsOfNodes` and `Mesh.RecombineAll` each leaked from one
   operation into the next.
2. **A physical branch hinging on float equality.** `S_a < ZPA` with log-log
   interpolation returning `19.999999999999996` for a plateau of 20, which
   silently disabled an entire correction.
3. **Name collisions with existing private methods.** A new `_project(p, rect)`
   shadowed an existing `_project(x, y, z)`; every coordinate became NaN and
   the feature simply never fired — no error.
4. **Tautologies presented as checks.** A "slip margin" computed from the
   quantity it was derived from, which could never fall below its own safety
   factor — and a unit test that asserted the tautology.
5. **Silent solver wrongness.** Multithreaded SPOOLES in CalculiX returns wrong
   answers and exits 0. Threads are pinned to 1.
6. **Stale artefacts read back as current.** ccx appends to an existing `.frd`;
   old result files must be deleted before a run.
7. **Quantities reused for the wrong purpose.** Consistent-load weights (zero on
   the corner nodes of a quadratic face) used as nodal areas.
8. **Ordering bugs in state.** An undo snapshot taken after the mutation it was
   meant to capture.
9. **Test isolation.** A module-scoped fixture handed out by reference and
   mutated by one test for every test after it.
10. **`Node.append(null)`** inserts the string "null".

## How to report

Findings ranked most severe first. For each: **what is wrong**, a **concrete
failure case** (inputs or state → wrong output), and **why it matters to an
engineer's conclusion**. Distinguish:

- **defect** — gives a wrong answer or breaks
- **risk** — correct today, fragile under a plausible change
- **gap** — missing capability, with what it blocks
- **opinion** — style or preference; say so and keep it short

No praise sections. If a file is fine, say nothing about it.
