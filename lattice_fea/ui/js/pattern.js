// Copying a joint onto other holes.
//
// A bolt is not one selection — it is a hole cylinder on one part, a mating
// cylinder or bearing face on the other, a diameter and a preload. Re-picking
// all of that for every hole in a flange is the single most tedious thing in
// setting up a bolted assembly, and it is exactly the operation that is easy
// to get subtly wrong by hand (one bolt at the old preload, one pair swapped).
//
// So: pick a REFERENCE face on a bolt you already built, pick TARGET faces,
// and every other face of that bolt is carried across by the same rigid
// transform. Nothing is created from a guess — a target whose mates cannot be
// identified unambiguously is reported, not silently half-built.

const sub = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const cross = (a, b) => [a[1] * b[2] - a[2] * b[1],
                         a[2] * b[0] - a[0] * b[2],
                         a[0] * b[1] - a[1] * b[0]];
const len = (a) => Math.hypot(a[0], a[1], a[2]);
const unit = (a) => { const n = len(a); return n > 1e-12 ? [a[0] / n, a[1] / n, a[2] / n] : [0, 0, 1]; };
const apply = (M, v) => [dot(M[0], v), dot(M[1], v), dot(M[2], v)];
const I3 = [[1, 0, 0], [0, 1, 0], [0, 0, 1]];

function matmul(A, B) {
  return A.map((row) => [0, 1, 2].map((j) => row[0] * B[0][j] + row[1] * B[1][j] + row[2] * B[2][j]));
}

/** Rotation taking axis `a` onto axis `b`.
 *
 *  Cylinder axes recovered from a tessellation carry an arbitrary sign, so
 *  they are treated as undirected — otherwise half the holes in a plate would
 *  come back "rotated 180°" and map their mates to the wrong side. */
function rotationBetween(a, b) {
  a = unit(a);
  b = unit(b);
  if (dot(a, b) < 0) b = [-b[0], -b[1], -b[2]];
  const v = cross(a, b);
  const s = len(v);
  const c = dot(a, b);
  if (s < 1e-9) return I3;
  const K = [[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]];
  const K2 = matmul(K, K);
  const k = (1 - c) / (s * s);
  return I3.map((row, i) => row.map((e, j) => e + K[i][j] + k * K2[i][j]));
}

export function faceIndex(geo) {
  return new Map(geo.faces.map((f) => [Number(f.tag), f]));
}

/** Human-readable description of a face, for the panel. */
export function describeFace(geo, tag) {
  const f = faceIndex(geo).get(Number(tag));
  if (!f) return "—";
  const where = f.solids?.length
    ? ` on ${f.solids.map((s) => geo.solids.find((x) => x.tag === s)?.name || `solid ${s}`).join(" / ")}`
    : "";
  if (f.fit?.kind === "cylinder") {
    return `⌀${(f.fit.radius * 2).toFixed(2)} hole${where}`;
  }
  return `${f.fit?.kind === "plane" ? "planar" : "curved"} face, ${f.area.toFixed(0)} mm²${where}`;
}

/** The face a pattern should be measured from: the bolt's hole cylinder. */
export function defaultReferenceFace(geo, bolt) {
  const idx = faceIndex(geo);
  const all = [...(bolt.side_a_faces || []), ...(bolt.side_b_faces || [])];
  const cyl = all.find((t) => idx.get(Number(t))?.fit?.kind === "cylinder");
  return Number(cyl ?? all[0] ?? 0) || null;
}

/** Every face already claimed by some bolt — a hole must not get two bolts. */
export function claimedFaces(bolts) {
  const s = new Set();
  for (const b of bolts || []) {
    for (const t of [...(b.side_a_faces || []), ...(b.side_b_faces || [])]) s.add(Number(t));
  }
  return s;
}

/** Faces that are plausible pattern targets: unused hole cylinders of the same
 *  diameter, on the same part as the reference.
 *
 *  Restricting to the reference's own solid keeps the count honest — the
 *  mating hole in the other plate is not a separate joint, it is the far side
 *  of one, and it gets claimed automatically when its partner is picked. */
export function candidateTargets(geo, bolts, refTag) {
  const ref = faceIndex(geo).get(Number(refTag));
  const claimed = claimedFaces(bolts);
  const refSolids = new Set(ref?.solids || []);
  return geo.faces.filter((f) => {
    if (claimed.has(Number(f.tag))) return false;
    if (!ref || ref.fit?.kind !== "cylinder") return true;
    if (refSolids.size && !(f.solids || []).some((s) => refSolids.has(s))) return false;
    return f.fit?.kind === "cylinder"
      && Math.abs(f.fit.radius - ref.fit.radius) <= 0.25 * ref.fit.radius;
  });
}

/**
 * Map one bolt onto one target face.
 *
 * Returns the new face sets plus any warnings. `ok` is false only when the
 * reference itself cannot be placed; a bolt whose second side could not be
 * identified still comes back so the user can finish it by hand — with the
 * problem stated rather than a silently one-sided joint.
 */
export function mapBoltToTarget(geo, bolt, refTag, targetTag) {
  const idx = faceIndex(geo);
  const ref = idx.get(Number(refTag));
  const tgt = idx.get(Number(targetTag));
  const warnings = [];
  if (!ref || !tgt) return { ok: false, warnings: ["reference or target face not found"] };

  const R = (ref.fit?.kind === "cylinder" && tgt.fit?.kind === "cylinder")
    ? rotationBetween(ref.fit.axis, tgt.fit.axis)
    : I3;
  // Anchor on the face centre of mass, which OCC gives exactly. The fitted
  // cylinder `center` is recovered from the tessellation and carries a few
  // tenths of noise — enough to lose a nearest-face match on a fine pattern.
  const place = (com) => add(apply(R, sub(com, ref.com)), tgt.com);

  const tol = Math.max(0.02 * (geo.diag || 100),
                       0.6 * (ref.fit?.radius || Math.sqrt(ref.area) / 2));
  const used = new Set([Number(targetTag)]);

  const mapOne = (srcTag) => {
    if (Number(srcTag) === Number(refTag)) return Number(targetTag);
    const src = idx.get(Number(srcTag));
    if (!src) return null;
    const want = place(src.com);

    const scored = [];
    for (const f of geo.faces) {
      if (used.has(Number(f.tag))) continue;
      if (src.fit?.kind === "cylinder") {
        if (f.fit?.kind !== "cylinder") continue;
        if (Math.abs(f.fit.radius - src.fit.radius) > 0.2 * src.fit.radius) continue;
      } else if (src.fit?.kind === "plane" && f.fit?.kind !== "plane") continue;
      if (f.area < src.area / 4 || f.area > src.area * 4) continue;
      scored.push([len(sub(f.com, want)), f]);
    }
    scored.sort((a, b) => a[0] - b[0]);
    if (!scored.length) return null;
    const [d1, best] = scored[0];
    const d2 = scored[1]?.[0] ?? Infinity;
    // Ambiguity is the real danger on a regular pattern: two holes equidistant
    // from where the mate should be means the answer is a coin flip.
    if (d1 > tol) return null;
    if (d1 > 0.05 * tol && d2 < 2.5 * d1) return null;
    return Number(best.tag);
  };

  const out = { ok: true, side_a_faces: [], side_b_faces: [], warnings };
  for (const [key, list] of [["side_a_faces", bolt.side_a_faces || []],
                             ["side_b_faces", bolt.side_b_faces || []]]) {
    for (const t of list) {
      let m = mapOne(t);
      if (m == null) m = coaxialMate(geo, idx, tgt, t, used);
      if (m == null) {
        warnings.push(`no match for ${describeFace(geo, t)}`);
        continue;
      }
      used.add(m);
      out[key].push(m);
    }
  }
  // A bolt is a load path between two parts. If the template had two sides
  // and only one came across, the copy would be a beam anchored at one end —
  // it would mesh, solve, and be wrong. Refuse it instead.
  const twoSided = (bolt.side_a_faces || []).length && (bolt.side_b_faces || []).length;
  if (twoSided && !(out.side_a_faces.length && out.side_b_faces.length)) {
    out.ok = false;
    out.warnings.unshift("could not identify both sides of the joint here");
  }
  if (!out.side_a_faces.length && !out.side_b_faces.length) out.ok = false;
  return out;
}

/**
 * Last resort for the far side of a joint: the cylinder sharing this hole's
 * axis but belonging to a different part.
 *
 * This is what actually defines "the other side of the joint", and it holds
 * even when the plates are different thicknesses — the case where a pure
 * translation from the template lands nowhere near the mating hole.
 */
function coaxialMate(geo, idx, tgt, srcTag, used) {
  const src = idx.get(Number(srcTag));
  if (!src || src.fit?.kind !== "cylinder" || tgt.fit?.kind !== "cylinder") return null;
  const axis = unit(tgt.fit.axis);
  const tgtSolids = new Set(tgt.solids || []);
  let best = null;
  let bestD = Infinity;
  for (const f of geo.faces) {
    if (used.has(Number(f.tag))) continue;
    if (f.fit?.kind !== "cylinder") continue;
    if ((f.solids || []).some((s) => tgtSolids.has(s))) continue;   // must be the other part
    if (Math.abs(Math.abs(dot(unit(f.fit.axis), axis)) - 1) > 1e-3) continue;
    if (Math.abs(f.fit.radius - src.fit.radius) > 0.25 * src.fit.radius) continue;
    // perpendicular distance from the target's axis line
    const d = sub(f.com, tgt.com);
    const off = len(sub(d, axis.map((a) => a * dot(d, axis))));
    if (off > 0.3 * tgt.fit.radius) continue;
    const along = Math.abs(dot(d, axis));
    if (along < bestD) { bestD = along; best = f; }
  }
  return best ? Number(best.tag) : null;
}
