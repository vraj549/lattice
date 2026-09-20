// Undo/redo tests. Run via tests/test_history.py (pytest), or directly with
// `node tests/test_history.mjs` from the repo root.
//
// This exists because the feature was completely dead and nothing noticed.
// pushUndo() ran BEFORE the edit, so the document still equalled the
// baseline at that moment, and a guard reading `if (doc === baseline &&
// !stack.length) return` could therefore never do anything but refuse — and
// the stack could only stop being empty through that same function. Every
// edit made through mutate() was dropped and Undo stayed greyed out for the
// whole session. The first test here is that exact sequence.

import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../lattice_fea/ui/js/history.js"), "utf8");
const H = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));

let failures = 0;
function check(name, cond, detail = "") {
  if (cond) { console.log(`  ok   ${name}`); return; }
  failures++;
  console.log(`  FAIL ${name}${detail ? " — " + detail : ""}`);
}

/** A stand-in for S.project.setup plus the mutate() shape around it. */
function harness(initial = { probes: [{ id: "p1", x: 0 }] }) {
  let doc = initial;
  const h = new H.History(() => JSON.stringify(doc), (j) => { doc = JSON.parse(j); });
  h.reset();
  return {
    h,
    get doc() { return doc; },
    // exactly what A.mutate does: record, change, commit
    mutate(fn) { h.push(); fn(doc); h.commit(); },
    // the other shape: change first, then call mutate with an empty body
    mutateAfter(fn) { fn(doc); h.push(); h.commit(); },
  };
}

console.log("undo records edits made through mutate()");
{
  const t = harness();
  check("nothing to undo before the first edit", !t.h.canUndo);
  t.mutate((d) => { d.probes[0].x = 10; });
  check("one edit enables undo", t.h.canUndo, `canUndo=${t.h.canUndo}`);
  t.mutate((d) => { d.probes[0].x = 20; });
  t.mutate((d) => { d.probes[0].x = 30; });
  check("three edits, three steps", t.h.undoStack.length === 3,
        `length=${t.h.undoStack.length}`);
  t.h.undo();
  check("undo returns the previous value", t.doc.probes[0].x === 20, `x=${t.doc.probes[0].x}`);
  t.h.undo(); t.h.undo();
  check("undo all the way back", t.doc.probes[0].x === 0, `x=${t.doc.probes[0].x}`);
  check("and then stops", !t.h.canUndo);
}

console.log("redo");
{
  const t = harness();
  t.mutate((d) => { d.probes[0].x = 10; });
  t.h.undo();
  check("undo enables redo", t.h.canRedo);
  t.h.redo();
  check("redo restores the edit", t.doc.probes[0].x === 10, `x=${t.doc.probes[0].x}`);
  t.h.undo();
  t.mutate((d) => { d.probes[0].x = 99; });
  check("a new edit forks the future", !t.h.canRedo);
}

console.log("the call sites that edit first and call mutate(() => {}) after");
{
  // A dozen places in main.js do this. The baseline is snapshotted at the END
  // of the previous edit precisely so both shapes undo correctly; snapshotting
  // on entry captured the change after it had already happened, and undo was
  // a no-op.
  const t = harness();
  t.mutateAfter((d) => { d.probes.push({ id: "p2", x: 5 }); });
  check("the added item is there", t.doc.probes.length === 2);
  t.h.undo();
  check("undo removes it", t.doc.probes.length === 1, `n=${t.doc.probes.length}`);
}

console.log("typed fields commit on blur, one step per field");
{
  const t = harness();
  t.doc.probes[0].x = 1;            // typing, no push
  check("a keystroke alone records nothing", !t.h.canUndo);
  t.doc.probes[0].x = 12;
  t.doc.probes[0].x = 123;          // still the same field
  check("committed once", t.h.commitIfChanged() === true);
  check("one step for the whole field", t.h.undoStack.length === 1,
        `length=${t.h.undoStack.length}`);
  check("committing again with no change does nothing",
        t.h.commitIfChanged() === false);
  t.h.undo();
  check("undo takes back the whole field", t.doc.probes[0].x === 0, `x=${t.doc.probes[0].x}`);
}

console.log("bounds");
{
  const t = harness();
  for (let i = 0; i < H.UNDO_LIMIT + 20; i++) t.mutate((d) => { d.probes[0].x = i; });
  check("the stack is capped", t.h.undoStack.length === H.UNDO_LIMIT,
        `length=${t.h.undoStack.length}`);
  check("and drops the oldest, not the newest",
        JSON.parse(t.h.undoStack[t.h.undoStack.length - 1]).probes[0].x
          === H.UNDO_LIMIT + 18);
}

console.log("a different project does not undo into the previous one");
{
  const t = harness();
  t.mutate((d) => { d.probes[0].x = 10; });
  t.h.reset();
  check("history is cleared", !t.h.canUndo && !t.h.canRedo);
}

console.log(failures ? `\n${failures} failure(s)` : "\nall passed");
process.exit(failures ? 1 : 0);
