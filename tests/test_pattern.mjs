// Bolt-pattern mapping tests. Run via tests/test_pattern.py (pytest), or
// directly with `node tests/test_pattern.mjs` from the repo root.
//
// The geometry here is synthetic so the expected answer is known exactly:
// a hole pattern in two stacked plates, with the awkward cases that make a
// naive "nearest face" mapping wrong.

import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../lattice_fea/ui/js/pattern.js"), "utf8");
const P = await import("data:text/javascript;base64," + Buffer.from(src).toString("base64"));

let failures = 0;
function check(name, cond, detail = "") {
  if (cond) { console.log(`  ok   ${name}`); return; }
  failures++;
  console.log(`  FAIL ${name}${detail ? " — " + detail : ""}`);
}
function eq(name, got, want) {
  check(name, JSON.stringify(got) === JSON.stringify(want),
        `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

/** Two plates with `holes` at the given (x, y), lower plate 0..tA, upper tA..tA+tB. */
function twoPlates(holes, { tA = 8, tB = 8, r = 3.3 } = {}) {
  const faces = [];
  let tag = 100;
  faces.push({ tag: 1, area: 4000, com: [50, 25, 0], solids: [1], fit: { kind: "plane" } });
  faces.push({ tag: 2, area: 4000, com: [50, 25, tA], solids: [1, 2], fit: { kind: "plane" } });
  faces.push({ tag: 3, area: 4000, com: [50, 25, tA + tB], solids: [2], fit: { kind: "plane" } });
  const lower = [], upper = [];
  for (const [x, y] of holes) {
    lower.push(tag);
    faces.push({ tag: tag++, area: 2 * Math.PI * r * tA, com: [x, y, tA / 2], solids: [1],
                 fit: { kind: "cylinder", radius: r, axis: [0, 0, 1], center: [x, y, tA / 2], height: tA } });
    upper.push(tag);
    faces.push({ tag: tag++, area: 2 * Math.PI * r * tB, com: [x, y, tA + tB / 2], solids: [2],
                 fit: { kind: "cylinder", radius: r, axis: [0, 0, 1], center: [x, y, tA + tB / 2], height: tB } });
  }
  return { geo: { solids: [{ tag: 1, name: "Plate A" }, { tag: 2, name: "Plate B" }],
                  faces, interfaces: [], diag: 120 }, lower, upper };
}

// ---------------------------------------------------------------- uniform
{
  console.log("uniform 4-hole pattern, equal plates");
  const { geo, lower, upper } = twoPlates([[20, 20], [80, 20], [20, 60], [80, 60]]);
  const tmpl = { side_a_faces: [lower[0]], side_b_faces: [upper[0]], preload_N: 8000 };
  const ref = P.defaultReferenceFace(geo, tmpl);
  eq("reference is the template's hole", ref, lower[0]);

  const cands = P.candidateTargets(geo, [tmpl], ref).map((f) => f.tag);
  eq("candidates are the other three holes in the same plate", cands, lower.slice(1));

  for (let i = 1; i < 4; i++) {
    const m = P.mapBoltToTarget(geo, tmpl, ref, lower[i]);
    eq(`hole ${i} maps both sides`, [m.ok, m.side_a_faces, m.side_b_faces],
       [true, [lower[i]], [upper[i]]]);
  }
}

// ------------------------------------------------- unequal plate thickness
{
  // Pure translation from the template puts the far-side face at the wrong
  // height, so this only works via the coaxial fallback.
  console.log("unequal plate thickness (translation alone misses the mate)");
  const { geo, lower, upper } = twoPlates([[20, 20], [80, 20]], { tA: 6, tB: 30 });
  const tmpl = { side_a_faces: [lower[0]], side_b_faces: [upper[0]] };
  const m = P.mapBoltToTarget(geo, tmpl, lower[0], lower[1]);
  eq("far side found by coaxial search", [m.ok, m.side_a_faces, m.side_b_faces],
     [true, [lower[1]], [upper[1]]]);
}

// ------------------------------------------------------------- ambiguity
{
  // An exact hit is never ambiguous, however close the runner-up sits.
  console.log("an exact mate wins over a near decoy");
  const { geo, lower, upper } = twoPlates([[20, 20], [80, 20]]);
  geo.faces.push({ tag: 900, area: 2 * Math.PI * 3.3 * 8, com: [80.4, 20, 12], solids: [2],
                   fit: { kind: "cylinder", radius: 3.3, axis: [0, 0, 1],
                          center: [80.4, 20, 12], height: 8 } });
  const tmpl = { side_a_faces: [lower[0]], side_b_faces: [upper[0]] };
  const m = P.mapBoltToTarget(geo, tmpl, lower[0], lower[1]);
  eq("takes the exact match", m.side_b_faces, [upper[1]]);
}
{
  // Genuinely ambiguous: the far side of the target does not sit where the
  // template says, and two candidates straddle that position equally. Picking
  // either would bolt the joint to the wrong hole, so it must refuse.
  console.log("ambiguous mate is refused, not guessed");
  const { geo, lower, upper } = twoPlates([[20, 20], [80, 20]]);
  // move the real mate away and put two equidistant decoys around the target
  geo.faces = geo.faces.filter((f) => f.tag !== upper[1]);
  for (const [i, x] of [[0, 78], [1, 82]]) {
    geo.faces.push({ tag: 910 + i, area: 2 * Math.PI * 3.3 * 8, com: [x, 20, 12], solids: [2],
                     fit: { kind: "cylinder", radius: 3.3, axis: [0, 0, 1],
                            center: [x, 20, 12], height: 8 } });
  }
  const tmpl = { side_a_faces: [lower[0]], side_b_faces: [upper[0]] };
  const m = P.mapBoltToTarget(geo, tmpl, lower[0], lower[1]);
  check("refuses the ambiguous pair", m.ok === false,
        `ok=${m.ok} b=${JSON.stringify(m.side_b_faces)}`);
}

// ------------------------------------------------------------ wrong target
{
  console.log("a planar target cannot become a joint");
  const { geo, lower, upper } = twoPlates([[20, 20], [80, 20]]);
  const tmpl = { side_a_faces: [lower[0]], side_b_faces: [upper[0]] };
  const m = P.mapBoltToTarget(geo, tmpl, lower[0], 1);
  check("refused", m.ok === false, JSON.stringify(m.warnings));
}

// ------------------------------------------------------------- claimed set
{
  console.log("holes already bolted are not offered again");
  const { geo, lower, upper } = twoPlates([[20, 20], [80, 20], [20, 60]]);
  const b1 = { side_a_faces: [lower[0]], side_b_faces: [upper[0]] };
  const b2 = { side_a_faces: [lower[1]], side_b_faces: [upper[1]] };
  const cands = P.candidateTargets(geo, [b1, b2], lower[0]).map((f) => f.tag);
  eq("only the free hole remains", cands, [lower[2]]);
  const claimed = P.claimedFaces([b1, b2]);
  check("claimed covers both sides", claimed.has(upper[0]) && claimed.has(upper[1]));
}

// -------------------------------------------------------- rotated pattern
{
  // Holes drilled along X instead of Z: the template must be rotated onto the
  // target axis, not just translated.
  console.log("target hole on a different axis");
  const geo = {
    solids: [{ tag: 1, name: "A" }, { tag: 2, name: "B" }], interfaces: [], diag: 120,
    faces: [
      { tag: 10, area: 166, com: [20, 20, 4], solids: [1],
        fit: { kind: "cylinder", radius: 3.3, axis: [0, 0, 1], center: [20, 20, 4], height: 8 } },
      { tag: 11, area: 166, com: [20, 20, 12], solids: [2],
        fit: { kind: "cylinder", radius: 3.3, axis: [0, 0, 1], center: [20, 20, 12], height: 8 } },
      { tag: 20, area: 166, com: [60, 40, 30], solids: [1],
        fit: { kind: "cylinder", radius: 3.3, axis: [1, 0, 0], center: [60, 40, 30], height: 8 } },
      { tag: 21, area: 166, com: [68, 40, 30], solids: [2],
        fit: { kind: "cylinder", radius: 3.3, axis: [1, 0, 0], center: [68, 40, 30], height: 8 } },
    ],
  };
  const tmpl = { side_a_faces: [10], side_b_faces: [11] };
  const m = P.mapBoltToTarget(geo, tmpl, 10, 20);
  eq("rotated joint maps", [m.ok, m.side_a_faces, m.side_b_faces], [true, [20], [21]]);
}

console.log(failures ? `\n${failures} failure(s)` : "\nall pattern checks passed");
process.exit(failures ? 1 : 0);
