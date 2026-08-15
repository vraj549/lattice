import * as THREE from "three";

/* Boundary-condition and load symbols, following the conventions the
 * commercial tools use (Abaqus draws arrows per type; Ansys colours supports
 * blue and loads red; textbook FEA uses ground-triangles for encastre and
 * rollers for frictionless):
 *
 *   fixed support     violet cone into the face + ground pad   (encastre)
 *   frictionless      violet spheres sitting on the face       (rollers)
 *   displacement      violet arrow + tail bar
 *   force             amber arrow, tip ON the face, along the force vector
 *   pressure          amber arrows pressing INTO the face
 *   gravity           one large amber arrow at the model centre
 *   rotation          amber curved arrow about the spin axis
 *   remote force      amber arrow at the remote point + dashed spider legs
 *   bolt              steel-blue shank between the two face sets
 */

const C_SUP = 0x8d7dec, C_LOAD = 0xe89344, C_BOLT = 0x6fa8cc;

const mat = (color, opacity = 1) => new THREE.MeshBasicMaterial({
  color, transparent: opacity < 1, opacity, depthTest: true });

function arrow(group, tip, dir, len, color, headScale = 1) {
  // dir points the way the arrow travels; tip is where it lands
  const d = dir.clone().normalize();
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), d);
  const headLen = len * 0.32 * headScale;
  const r = len * 0.045 * headScale;

  const shaft = new THREE.Mesh(
    new THREE.CylinderGeometry(r, r, len - headLen, 8), mat(color));
  shaft.quaternion.copy(q);
  shaft.position.copy(tip).addScaledVector(d, -(len - headLen) / 2 - headLen);

  const head = new THREE.Mesh(
    new THREE.ConeGeometry(r * 3.2, headLen, 12), mat(color));
  head.quaternion.copy(q);
  head.position.copy(tip).addScaledVector(d, -headLen / 2);

  group.add(shaft, head);
}

/** Sample points spread *spatially* over a face. Striding the vertex array
 *  clusters glyphs wherever the tessellator happened to emit vertices, so
 *  instead lay a grid over the face's bounding box and take the nearest real
 *  vertex to each cell centre. */
function samplePoints(info, n) {
  const out = [];
  if (!info || !info.positions) return out;
  const p = info.positions;
  const count = p.length / 3;
  if (!count) return out;
  if (count <= n) {
    for (let i = 0; i < count; i++) out.push(new THREE.Vector3(p[i * 3], p[i * 3 + 1], p[i * 3 + 2]));
    return out;
  }

  const lo = new THREE.Vector3(Infinity, Infinity, Infinity);
  const hi = new THREE.Vector3(-Infinity, -Infinity, -Infinity);
  for (let i = 0; i < count; i++) {
    lo.x = Math.min(lo.x, p[i * 3]);     hi.x = Math.max(hi.x, p[i * 3]);
    lo.y = Math.min(lo.y, p[i * 3 + 1]); hi.y = Math.max(hi.y, p[i * 3 + 1]);
    lo.z = Math.min(lo.z, p[i * 3 + 2]); hi.z = Math.max(hi.z, p[i * 3 + 2]);
  }
  // the two widest axes span the face; grid over those
  const ext = [hi.x - lo.x, hi.y - lo.y, hi.z - lo.z];
  const order = [0, 1, 2].sort((a, b) => ext[b] - ext[a]);
  const [a0, a1] = order;
  const cols = Math.ceil(Math.sqrt(n)), rows = Math.ceil(n / cols);
  const comp = (i, k) => p[i * 3 + k];
  const used = new Set();

  for (let r = 0; r < rows && out.length < n; r++) {
    for (let c = 0; c < cols && out.length < n; c++) {
      const t0 = (c + 0.5) / cols, t1 = (r + 0.5) / rows;
      const g0 = lo.getComponent(a0) + ext[a0] * t0;
      const g1 = lo.getComponent(a1) + ext[a1] * t1;
      let best = -1, bd = Infinity;
      for (let i = 0; i < count; i++) {
        if (used.has(i)) continue;
        const d = (comp(i, a0) - g0) ** 2 + (comp(i, a1) - g1) ** 2;
        if (d < bd) { bd = d; best = i; }
      }
      if (best >= 0) {
        used.add(best);
        out.push(new THREE.Vector3(p[best * 3], p[best * 3 + 1], p[best * 3 + 2]));
      }
    }
  }
  return out;
}

function fixedGlyph(group, pt, normal, s) {
  // cone pointing into the surface + a small pad = classic encastre marker
  const n = normal.clone().normalize();
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), n);
  const cone = new THREE.Mesh(new THREE.ConeGeometry(s * 0.42, s, 4), mat(C_SUP));
  cone.quaternion.copy(q);
  cone.position.copy(pt).addScaledVector(n, s * 0.5);
  const pad = new THREE.Mesh(
    new THREE.BoxGeometry(s * 1.15, s * 0.13, s * 1.15), mat(C_SUP, 0.85));
  pad.quaternion.copy(q);
  pad.position.copy(pt).addScaledVector(n, s * 1.06);
  group.add(cone, pad);
}

function rollerGlyph(group, pt, normal, s) {
  const n = normal.clone().normalize();
  const ball = new THREE.Mesh(new THREE.SphereGeometry(s * 0.3, 12, 10), mat(C_SUP));
  ball.position.copy(pt).addScaledVector(n, s * 0.3);
  const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), n);
  const bar = new THREE.Mesh(
    new THREE.BoxGeometry(s * 1.1, s * 0.1, s * 1.1), mat(C_SUP, 0.85));
  bar.quaternion.copy(q);
  bar.position.copy(pt).addScaledVector(n, s * 0.68);
  group.add(ball, bar);
}

function curvedArrow(group, center, axis, radius, color) {
  const a = axis.clone().normalize();
  const curve = new THREE.Curve();
  const u = new THREE.Vector3(1, 0, 0);
  if (Math.abs(a.dot(u)) > 0.9) u.set(0, 1, 0);
  const e1 = new THREE.Vector3().crossVectors(a, u).normalize();
  const e2 = new THREE.Vector3().crossVectors(a, e1).normalize();
  curve.getPoint = (t) => {
    const ang = t * Math.PI * 1.5;
    return new THREE.Vector3()
      .addScaledVector(e1, Math.cos(ang) * radius)
      .addScaledVector(e2, Math.sin(ang) * radius)
      .add(center);
  };
  const tube = new THREE.Mesh(
    new THREE.TubeGeometry(curve, 32, radius * 0.055, 8, false), mat(color));
  group.add(tube);
  const end = curve.getPoint(1), prev = curve.getPoint(0.96);
  arrow(group, end, new THREE.Vector3().subVectors(end, prev), radius * 0.5, color);
}

/**
 * Build every BC/load symbol for the current setup.
 * faceInfo: Map<tag, {centroid: Vector3, normal: Vector3, positions: Float32Array}>
 */
export function buildGlyphs(setup, geometry, faceInfo, meshStats) {
  const group = new THREE.Group();
  const diag = geometry.diag || 100;
  const s = diag * 0.022;                       // base glyph size
  const bbox = geometry.bbox;
  const center = new THREE.Vector3(
    (bbox[0] + bbox[3]) / 2, (bbox[1] + bbox[4]) / 2, (bbox[2] + bbox[5]) / 2);

  const info = (t) => faceInfo.get(Number(t));

  // ---- supports ----
  for (const sup of setup.supports || []) {
    for (const t of sup.faces || []) {
      const fi = info(t);
      if (!fi) continue;
      const pts = samplePoints(fi, 5);
      if (!pts.length) pts.push(fi.centroid);
      for (const p of pts) {
        if (sup.type === "frictionless") rollerGlyph(group, p, fi.normal, s);
        else if (sup.type === "disp") {
          arrow(group, p.clone().addScaledVector(fi.normal, s * 0.2),
                fi.normal.clone().negate(), s * 2.4, C_SUP);
        } else fixedGlyph(group, p, fi.normal, s);
      }
    }
  }

  // ---- loads ----
  for (const l of setup.loads || []) {
    if (l.type === "gravity") {
      const g = new THREE.Vector3(l.g?.[0] ?? 0, l.g?.[1] ?? 0, l.g?.[2] ?? -1);
      if (g.lengthSq() < 1e-12) g.set(0, 0, -1);
      arrow(group, center.clone().addScaledVector(g.clone().normalize(), diag * 0.30),
            g, diag * 0.26, C_LOAD, 1.5);
      continue;
    }
    if (l.type === "rotation") {
      const ax = new THREE.Vector3(l.axis?.[0] ?? 0, l.axis?.[1] ?? 0, l.axis?.[2] ?? 1);
      if (ax.lengthSq() < 1e-12) ax.set(0, 0, 1);
      const c = new THREE.Vector3(l.center?.[0] ?? center.x,
                                  l.center?.[1] ?? center.y,
                                  l.center?.[2] ?? center.z);
      curvedArrow(group, c, ax, diag * 0.2, C_LOAD);
      continue;
    }
    if (l.type === "remote") {
      const rp = new THREE.Vector3(l.x || 0, l.y || 0, l.z || 0);
      const f = new THREE.Vector3(l.fx || 0, l.fy || 0, l.fz || 0);
      if (f.lengthSq() > 1e-20) {
        arrow(group, rp, f, diag * 0.18, C_LOAD, 1.2);
      }
      const m = new THREE.Vector3(l.mx || 0, l.my || 0, l.mz || 0);
      if (m.lengthSq() > 1e-20) curvedArrow(group, rp, m, diag * 0.09, C_LOAD);
      // dashed spider legs to the driven faces
      const legs = [];
      for (const t of l.faces || []) {
        const fi = info(t);
        if (!fi) continue;
        for (const p of samplePoints(fi, 8)) legs.push(rp.x, rp.y, rp.z, p.x, p.y, p.z);
      }
      if (legs.length) {
        const g2 = new THREE.BufferGeometry();
        g2.setAttribute("position", new THREE.Float32BufferAttribute(legs, 3));
        const line = new THREE.LineSegments(g2, new THREE.LineDashedMaterial({
          color: C_LOAD, dashSize: diag * 0.012, gapSize: diag * 0.012,
          transparent: true, opacity: 0.75 }));
        line.computeLineDistances();
        group.add(line);
      }
      const dot = new THREE.Mesh(new THREE.SphereGeometry(s * 0.35, 12, 10), mat(C_LOAD));
      dot.position.copy(rp);
      group.add(dot);
      continue;
    }
    // force / pressure act on faces
    for (const t of l.faces || []) {
      const fi = info(t);
      if (!fi) continue;
      const pts = samplePoints(fi, l.type === "pressure" ? 7 : 5);
      if (!pts.length) pts.push(fi.centroid);
      let dir;
      if (l.type === "pressure") {
        const sign = (l.pressure || 0) >= 0 ? -1 : 1;   // positive presses in
        dir = fi.normal.clone().multiplyScalar(sign);
      } else {
        dir = new THREE.Vector3(l.fx || 0, l.fy || 0, l.fz || 0);
        if (dir.lengthSq() < 1e-20) dir = fi.normal.clone().negate();
      }
      const len = s * 2.6;
      const dn = dir.dot(fi.normal);
      for (const p of pts) {
        const base = p.clone().addScaledVector(fi.normal, s * 0.12);
        // Keep the whole arrow outside the material. Pushing into the face:
        // tip on the surface, shaft trails outward. Pulling away: tail on the
        // surface, tip further out. Otherwise the shaft buries itself.
        const tip = dn >= 0
          ? base.addScaledVector(dir.clone().normalize(), len)
          : base;
        arrow(group, tip, dir, len, C_LOAD);
      }
    }
  }

  // ---- bolts (shank between the two face-set centroids) ----
  for (const b of setup.bolts || []) {
    const ca = avgCentroid(b.side_a_faces, info);
    const cb = avgCentroid(b.side_b_faces, info);
    if (!ca || !cb) continue;
    const d = new THREE.Vector3().subVectors(cb, ca);
    const len = d.length();
    if (len < 1e-9) continue;
    const r = Math.max((b.d_mm || 8) / 2, diag * 0.004);
    const shank = new THREE.Mesh(
      new THREE.CylinderGeometry(r, r, len, 14), mat(C_BOLT, 0.95));
    shank.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), d.clone().normalize());
    shank.position.copy(ca).addScaledVector(d, 0.5);
    const head = new THREE.Mesh(new THREE.CylinderGeometry(r * 1.7, r * 1.7, r * 1.1, 6), mat(C_BOLT));
    head.quaternion.copy(shank.quaternion);
    head.position.copy(ca);
    const nut = head.clone();
    nut.position.copy(cb);
    group.add(shank, head, nut);
  }

  return group;
}

function avgCentroid(tags, info) {
  const acc = new THREE.Vector3();
  let n = 0;
  for (const t of tags || []) {
    const fi = info(t);
    if (fi) { acc.add(fi.centroid); n++; }
  }
  return n ? acc.multiplyScalar(1 / n) : null;
}
