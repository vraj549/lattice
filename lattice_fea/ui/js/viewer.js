import * as THREE from "three";
import { Orbit } from "./orbit.js";
import { decode } from "./b64.js";
import { makeTexture, contourStyle } from "./colormap.js";
import { AxisTriad } from "./axes.js";
import { buildGlyphs } from "./glyphs.js";

const SOLID_COLORS = [0x7fa8bd, 0xb99f7a, 0x8fae8a, 0xa48fb8, 0xbd9a8f, 0x8f9ebd];

/**
 * Empty a group and release its GPU memory.
 *
 * Group.clear() only detaches children — the BufferGeometry and Material of
 * every one keeps its VRAM until disposed. Every contour load, every project
 * open and (before the glyph signature check) every keystroke went through
 * one of these, so the leak grew without bound and eventually took the tab
 * with it. Nothing in this file may call .clear() directly.
 */
function disposeGroup(group) {
  const seen = new Set();
  group.traverse((o) => {
    if (o.geometry && !seen.has(o.geometry)) { seen.add(o.geometry); o.geometry.dispose(); }
    const mats = Array.isArray(o.material) ? o.material : (o.material ? [o.material] : []);
    for (const m of mats) {
      if (seen.has(m)) continue;
      seen.add(m);
      // textures are shared (the colour map lives for the session) — the
      // material owns none of them here, so only the material goes
      m.dispose();
    }
  });
  group.clear();
}
const HOVER = 0xe8b06a, SELECTED = 0xe89344, SUPPORT = 0x8d7dec, LOAD = 0xd97a28,
      BOLT = 0x4f88b0, SOLID_HL = 0x5fb3d4;

export class Viewer {
  constructor(canvas, callbacks = {}) {
    this.cb = callbacks;
    this.canvas = canvas;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.localClippingEnabled = true;
    // Manual clearing. render() clears colour by default, which inside the
    // triad's scissored corner painted an opaque black rectangle behind it.
    this.renderer.autoClear = false;
    this.scene = new THREE.Scene();
    this.applyTheme();
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);
    this.orbit = new Orbit(this.camera, canvas, () => this.requestRender());

    this._hemi = new THREE.HemisphereLight(0xcfd8e0, 0x4a5560, 0.9);
    this.scene.add(this._hemi);
    const key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(1, 0.6, 1.4);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0x88a0b8, 0.35);
    fill.position.set(-1, -0.5, -0.6);
    this.scene.add(fill);

    this.geoGroup = new THREE.Group();     // B-rep faces + edges
    this.meshGroup = new THREE.Group();    // mesh preview
    this.resultGroup = new THREE.Group();  // contour mesh
    this.glyphGroup = new THREE.Group();   // BC / load symbols
    this.scene.add(this.geoGroup, this.meshGroup, this.resultGroup, this.glyphGroup);

    this.triad = new AxisTriad();
    this.faceInfo = new Map();    // tag -> {centroid, normal, positions}
    this.faceMeshes = new Map();  // tag -> Mesh
    this.faceStates = new Map();  // tag -> 'support' | 'load'
    this.solidOfFace = new Map(); // tag -> [solidTags]
    this.hiddenSolids = new Set();
    this._hlSolid = null;
    this.pickSet = new Set();
    this.mode = "view";           // view | pickFaces | pickPoint
    this.hoverTag = null;
    this.bbox = [0, 0, 0, 1, 1, 1];
    this.clip = { axis: null, frac: 0.5, planes: [] };
    this.cmapTex = makeTexture(THREE);
    this.anim = null;             // {freq} for mode animation
    this.showResultMesh = true;   // element wireframe over contours
    this.probeMode = false;
    this._needsRender = true;

    this.ray = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();

    canvas.addEventListener("pointermove", (e) => this._onMove(e));
    canvas.addEventListener("pointerdown", (e) => { this._downAt = [e.clientX, e.clientY]; });
    canvas.addEventListener("pointerup", (e) => {
      const d = this._downAt ? Math.hypot(e.clientX - this._downAt[0], e.clientY - this._downAt[1]) : 99;
      if (d < 5 && e.button === 0) this._onClick(e);
    });
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
    this._loop();
  }

  /** Viewport ground follows the UI theme; light mode uses the pale
   *  background commercial pre/post-processors default to. */
  applyTheme() {
    const bg = this._dark() ? 0x11161a : 0xe7ebef;
    this.scene.background = new THREE.Color(bg);
    this.renderer.setClearColor(bg, 1);
    // model surfaces need a touch more ambient on a light ground
    if (this._hemi) this._hemi.intensity = this._dark() ? 0.9 : 1.15;
    if (this._edgeLines) {
      this._edgeLines.material.uniforms.uCol.value.setHex(this._edgeColor());
    }
    this.requestRender();
  }

  _dark() {
    const attr = document.documentElement.getAttribute("data-theme");
    return attr === "dark"
      || (!attr && window.matchMedia("(prefers-color-scheme: dark)").matches);
  }

  /** Mesh-overlay line colour. Dark ink on light contours, pale ink on dark —
   *  a fixed near-black vanished into the dark theme's viewport. */
  _edgeColor() { return this._dark() ? 0xc6d2dc : 0x1b2228; }

  // ---------------- render loop ----------------
  requestRender() { this._needsRender = true; }

  _loop() {
    requestAnimationFrame(() => this._loop());
    if (this.anim) {
      const t = performance.now() / 1000;
      const mat = this._resultMaterial;
      if (mat) {
        const d = this._defScale * Math.sin(2 * Math.PI * 1.2 * t);
        mat.uniforms.uDef.value = d;
        if (this._edgeLines) this._edgeLines.material.uniforms.uDef.value = d;
        this._needsRender = true;
      }
    }
    if (this._needsRender) {
      this._needsRender = false;
      this.renderer.clear();                       // full frame, once
      this.renderer.render(this.scene, this.camera);
      this.triad.render(this.renderer, this.camera, this.orbit.target);
    }
  }

  resize() {
    const w = this.canvas.parentElement.clientWidth;
    const h = this.canvas.parentElement.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.requestRender();
  }

  fit() { this.orbit.fit(this.bbox); }
  standardView(name) { this.orbit.setStandardView(name); }

  // ---------------- geometry ----------------
  setGeometry(tess, meta) {
    // Exact snap targets from the BREP, plus the edge polylines the wireframe
    // already carries — an edge snap needs a line to slide along, and this is
    // the same data that draws it.
    this.snaps = meta.snaps || null;
    this.snapEdges = tess.edges?.vtx
      ? { vtx: decode(tess.edges.vtx), seg: decode(tess.edges.seg) } : null;
    this.snapKinds = this.snapKinds
      || { vertex: true, centre: true, mid: true, edge: true };
    disposeGroup(this.geoGroup);
    this.faceMeshes.clear();
    this.faceInfo.clear();
    this.solidOfFace.clear();
    this._solidCentre = null;      // offsets belong to the old geometry
    this.explode = 0;
    this.bbox = meta.bbox;

    const solidColor = new Map();
    meta.solids.forEach((s, i) => solidColor.set(s.tag, SOLID_COLORS[i % SOLID_COLORS.length]));
    const faceMeta = new Map(meta.faces.map((f) => [f.tag, f]));

    for (const f of tess.faces) {
      const geom = new THREE.BufferGeometry();
      geom.setAttribute("position", new THREE.BufferAttribute(decode(f.vtx), 3));
      geom.setAttribute("normal", new THREE.BufferAttribute(decode(f.nrm), 3));
      geom.setIndex(new THREE.BufferAttribute(decode(f.tri), 1));
      const fm = faceMeta.get(f.tag);
      const solids = fm ? fm.solids : [];
      const base = solidColor.get(solids[0]) ?? 0x8899aa;
      const mat = new THREE.MeshPhongMaterial({
        color: base, side: THREE.DoubleSide, clippingPlanes: this.clip.planes,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
      });
      const mesh = new THREE.Mesh(geom, mat);
      mesh.userData = { tag: f.tag, base, solids, area: fm ? fm.area : 0 };
      mesh.matrixAutoUpdate = false;
      this.geoGroup.add(mesh);
      this.faceMeshes.set(f.tag, mesh);
      this.solidOfFace.set(f.tag, solids);

      // centroid + outward normal, for placing BC/load glyphs on this face
      const pos = geom.getAttribute("position").array;
      const nrm = geom.getAttribute("normal").array;
      const c = new THREE.Vector3();
      const n = new THREE.Vector3();
      const cnt = pos.length / 3;
      for (let i = 0; i < cnt; i++) {
        c.x += pos[i * 3]; c.y += pos[i * 3 + 1]; c.z += pos[i * 3 + 2];
        n.x += nrm[i * 3]; n.y += nrm[i * 3 + 1]; n.z += nrm[i * 3 + 2];
      }
      c.multiplyScalar(1 / Math.max(cnt, 1));
      if (n.lengthSq() < 1e-12) n.set(0, 0, 1);
      n.normalize();
      this.faceInfo.set(f.tag, { centroid: c, normal: n, positions: pos });
    }

    if (tess.edges && tess.edges.vtx) {
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(decode(tess.edges.vtx), 3));
      g.setIndex(new THREE.BufferAttribute(decode(tess.edges.seg), 1));
      const lines = new THREE.LineSegments(
        g, new THREE.LineBasicMaterial({ color: 0x9fb4c2, transparent: true, opacity: 0.55,
                                         clippingPlanes: this.clip.planes }));
      lines.userData.isEdges = true;
      this.geoGroup.add(lines);
    }
    this._applyFaceColors();
    this.fit();
    this.showGeometry();
  }

  setFaceStates(map) {
    this.faceStates = map;
    this._applyFaceColors();
  }

  /** Rebuild the BC/load symbol overlay from the current setup. */
  setGlyphs(setup, geometry) {
    disposeGroup(this.glyphGroup);
    if (!setup || !geometry || !this.faceInfo.size) { this.requestRender(); return; }
    try {
      this.glyphGroup.add(buildGlyphs(setup, geometry, this.faceInfo));
    } catch (e) {
      console.warn("glyph build failed:", e);   // never let symbols break the view
    }
    this.glyphGroup.visible = this.geoGroup.visible;
    this.requestRender();
  }

  /**
   * Push the parts apart so interior faces can be seen and picked.
   *
   * Each solid moves along the line from the assembly's centre to its own,
   * by `factor` times the model diagonal. That is the whole of the "auto":
   * there is no ordering or stacking direction to infer, and a radial push
   * separates a bolted stack, a bracket pair and a ring of parts alike.
   *
   * A face shared by two solids — which is what a bonded interface is after
   * fragmenting — gets the average of their offsets, so it stays between the
   * parts it joins instead of tearing away with one of them.
   */
  setExplode(factor) {
    this.explode = factor || 0;
    if (!this._solidCentre) this._buildExplodeOffsets();
    const diag = Math.hypot(this.bbox[3] - this.bbox[0],
                            this.bbox[4] - this.bbox[1],
                            this.bbox[5] - this.bbox[2]) || 1;
    for (const mesh of this.faceMeshes.values()) {
      const dir = mesh.userData.explodeDir;
      if (!dir) continue;
      mesh.position.set(dir.x * this.explode * diag,
                        dir.y * this.explode * diag,
                        dir.z * this.explode * diag);
      mesh.updateMatrix();
    }
    // Glyphs are placed on un-exploded face centroids, so they would float
    // free of the faces they belong to. Hiding them while apart is honest;
    // picking contact faces is what this mode is for.
    this.glyphGroup.visible = this.geoGroup.visible && this.explode === 0;
    this.requestRender();
  }

  _buildExplodeOffsets() {
    // area-weighted centre of each solid, from the faces that bound it
    const acc = new Map();
    for (const [tag, mesh] of this.faceMeshes) {
      const c = this.faceInfo.get(tag)?.centroid;
      if (!c) continue;
      const w = Math.max(mesh.userData.area || 0, 1e-9);
      for (const sd of mesh.userData.solids || []) {
        const e = acc.get(sd) || { x: 0, y: 0, z: 0, w: 0 };
        e.x += c.x * w; e.y += c.y * w; e.z += c.z * w; e.w += w;
        acc.set(sd, e);
      }
    }
    this._solidCentre = new Map();
    for (const [sd, e] of acc) {
      this._solidCentre.set(sd, { x: e.x / e.w, y: e.y / e.w, z: e.z / e.w });
    }
    let cx = 0, cy = 0, cz = 0;
    for (const c of this._solidCentre.values()) { cx += c.x; cy += c.y; cz += c.z; }
    const n = Math.max(this._solidCentre.size, 1);
    cx /= n; cy /= n; cz /= n;

    for (const mesh of this.faceMeshes.values()) {
      const solids = mesh.userData.solids || [];
      let dx = 0, dy = 0, dz = 0, k = 0;
      for (const sd of solids) {
        const c = this._solidCentre.get(sd);
        if (!c) continue;
        dx += c.x - cx; dy += c.y - cy; dz += c.z - cz; k++;
      }
      if (!k) { mesh.userData.explodeDir = null; continue; }
      dx /= k; dy /= k; dz /= k;
      const len = Math.hypot(dx, dy, dz);
      // A single part, or one sitting exactly on the assembly centre, has no
      // direction to move in. Leaving it put beats inventing one.
      mesh.userData.explodeDir = len > 1e-9
        ? { x: dx / len, y: dy / len, z: dz / len } : null;
    }
  }

  setHiddenSolids(set) {
    this.hiddenSolids = set;
    for (const mesh of this.faceMeshes.values()) {
      const solids = mesh.userData.solids;
      mesh.visible = !(solids.length && solids.every((s) => set.has(s)));
    }
    this.requestRender();
  }

  /** Highlight every face of one solid — what "selected" means for a part. */
  setHighlightSolid(tag) {
    const t = tag == null ? null : Number(tag);
    if (t === this._hlSolid) return;
    this._hlSolid = t;
    this._applyFaceColors();
  }

  _applyFaceColors() {
    for (const [tag, mesh] of this.faceMeshes) {
      let c = mesh.userData.base;
      const inSel = this._hlSolid != null
        && (mesh.userData.solids || []).includes(this._hlSolid);
      const st = this.faceStates.get(tag);
      if (st === "support") c = SUPPORT;
      else if (st === "load") c = LOAD;
      else if (st === "bolt") c = BOLT;
      if (inSel) c = SOLID_HL;
      if (this.pickSet.has(tag)) c = SELECTED;
      if (tag === this.hoverTag && (this.mode !== "view")) c = HOVER;
      mesh.material.color.setHex(c);
      // a lit rim as well as a tint, so the part reads as selected even
      // where a BC colour already owns the face
      if (mesh.material.emissive) {
        mesh.material.emissive.setHex(
          tag === this.hoverTag && this.mode !== "view" ? 0x332211
          : inSel ? 0x1d2a33 : 0x000000);
      }
    }
    this.requestRender();
  }

  // ---------------- picking ----------------
  startPickFaces(initial = []) {
    this.mode = "pickFaces";
    this.pickSet = new Set(initial.map(Number));
    this.showGeometry();
    this._applyFaceColors();
  }

  startPickPoint() {
    this.mode = "pickPoint";
    this.showGeometry();
  }

  endPick() {
    this.mode = "view";
    const out = [...this.pickSet];
    this.pickSet = new Set();
    this.hoverTag = null;
    this._applyFaceColors();
    return out;
  }

  _castFace(e) {
    const r = this.canvas.getBoundingClientRect();
    this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1,
                     -((e.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(this.pointer, this.camera);
    const hits = this.ray.intersectObjects(
      [...this.faceMeshes.values()].filter((m) => m.visible), false);
    return hits[0] || null;
  }

  _onMove(e) {
    if (this.probeMode && this.resultGroup.visible) {
      this.cb.onProbe?.(this.probeAt(e));
      return;
    }
    if (this.mode === "pickPoint") {
      const hit = this._castFace(e);
      const snap = hit ? this.bestSnap(e, hit) : null;
      this.cb.onSnapHover?.(snap, hit ? e : null);
      return;
    }
    if (this.mode === "view" || this.geoGroup.visible === false) return;
    const hit = this._castFace(e);
    const tag = hit ? hit.object.userData.tag : null;
    if (tag !== this.hoverTag) {
      this.hoverTag = tag;
      this._applyFaceColors();
      this.cb.onHover?.(tag ? { tag, area: hit.object.userData.area } : null);
    }
  }

  _onClick(e) {
    if (this.mode === "pickFaces") {
      const hit = this._castFace(e);
      if (hit) {
        const tag = hit.object.userData.tag;
        if (this.pickSet.has(tag)) this.pickSet.delete(tag);
        else this.pickSet.add(tag);
        this._applyFaceColors();
        this.cb.onPickChange?.([...this.pickSet]);
      }
    } else if (this.mode === "pickPoint") {
      const hit = this._castFace(e);
      if (!hit) return;
      const snap = this.bestSnap(e, hit);
      const p = snap ? snap.point : [hit.point.x, hit.point.y, hit.point.z];
      this.cb.onPickPoint?.({ x: p[0], y: p[1], z: p[2], snap: snap?.type || null });
    }
  }


  /* -------------------------------------------------------------- snapping
   *
   * A probe placed "on the corner" has to BE on the corner. Clicking a
   * tessellated surface gives a point near it, off by half a facet, and every
   * number the probe then reports is for somewhere you did not mean.
   *
   * Candidates are ranked by specificity, not distance: a vertex inside the
   * tolerance beats a circle centre, which beats an edge, which beats the
   * surface. Picking purely by proximity makes the snap flicker between two
   * kinds as the cursor moves a pixel, which is worse than no snap.
   */
  setSnapKinds(kinds) { this.snapKinds = { ...this.snapKinds, ...kinds }; }

  /**
   * Project a point to canvas pixels, taking the rect as an argument.
   *
   * Deliberately NOT called `_project`: there is already one of those taking
   * three scalars and reading the bounding rect itself. Sharing the name meant
   * the array arrived as `x`, every coordinate came out NaN, and snapping
   * simply never fired — no error, no marker, nothing to notice.
   *
   * The rect is passed in because this runs over every candidate on every
   * mouse move, and getBoundingClientRect() forces a layout each time.
   */
  _toScreen(p, rect) {
    const v = new THREE.Vector3(p[0], p[1], p[2]);
    v.project(this.camera);
    if (v.z > 1) return null;                 // behind the camera
    return [(v.x * 0.5 + 0.5) * rect.width, (-v.y * 0.5 + 0.5) * rect.height];
  }

  /** Best snap for a cursor position, given the raw surface hit under it. */
  bestSnap(e, hit) {
    if (!hit) return null;
    const rect = this.canvas.getBoundingClientRect();
    const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
    const TOL = 14;                            // px
    const near = this._diagOf() * 0.15;        // prune in 3D before projecting
    const h = hit.point;
    const within = (p) => (p[0] - h.x) ** 2 + (p[1] - h.y) ** 2
                        + (p[2] - h.z) ** 2 < near * near;

    const tryList = (list, type) => {
      if (!list || !this.snapKinds[type]) return null;
      let best = null;
      for (const p of list) {
        if (!within(p)) continue;
        const s = this._toScreen(p, rect);
        if (!s) continue;
        const d = Math.hypot(s[0] - cx, s[1] - cy);
        if (d <= TOL && (!best || d < best.d)) best = { d, point: p, type };
      }
      return best;
    };

    // specificity order
    const S_ = this.snaps || {};
    const found = tryList(S_.vertex, "vertex")
               || tryList(S_.centre, "centre")
               || tryList(S_.mid, "mid")
               || this._edgeSnap(cx, cy, rect, TOL, h, near);
    if (found) return { point: found.point, type: found.type };
    return null;
  }

  _edgeSnap(cx, cy, rect, TOL, h, near) {
    if (!this.snapEdges || !this.snapKinds.edge) return null;
    const { vtx, seg } = this.snapEdges;
    let best = null;
    for (let i = 0; i < seg.length; i += 2) {
      const a = seg[i] * 3, b = seg[i + 1] * 3;
      const ax = vtx[a], ay = vtx[a + 1], az = vtx[a + 2];
      const bx = vtx[b], by = vtx[b + 1], bz = vtx[b + 2];
      if ((ax - h.x) ** 2 + (ay - h.y) ** 2 + (az - h.z) ** 2 > near * near
       && (bx - h.x) ** 2 + (by - h.y) ** 2 + (bz - h.z) ** 2 > near * near) continue;
      const pa = this._toScreen([ax, ay, az], rect);
      const pb = this._toScreen([bx, by, bz], rect);
      if (!pa || !pb) continue;
      // nearest point on the segment in screen space, then the same parameter
      // back along the 3D segment — so the snapped point really is on the edge
      const vx = pb[0] - pa[0], vy = pb[1] - pa[1];
      const len2 = vx * vx + vy * vy;
      const t = len2 > 0
        ? Math.max(0, Math.min(1, ((cx - pa[0]) * vx + (cy - pa[1]) * vy) / len2))
        : 0;
      const d = Math.hypot(pa[0] + t * vx - cx, pa[1] + t * vy - cy);
      if (d <= TOL && (!best || d < best.d)) {
        best = { d, type: "edge",
                 point: [ax + t * (bx - ax), ay + t * (by - ay), az + t * (bz - az)] };
      }
    }
    return best;
  }

  _diagOf() {
    const b = this.bbox;
    return Math.hypot(b[3] - b[0], b[4] - b[1], b[5] - b[2]) || 1;
  }

  // ---------------- view switching ----------------
  showGeometry() {
    this.geoGroup.visible = true;
    this.glyphGroup.visible = true;
    this.meshGroup.visible = false;
    this.resultGroup.visible = false;
    this.anim = null;
    this.requestRender();
  }

  showMeshPreview(skin) {
    if (skin && skin.vtx) {
      disposeGroup(this.meshGroup);
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.BufferAttribute(decode(skin.vtx), 3));
      g.setIndex(new THREE.BufferAttribute(decode(skin.tri), 1));
      g.computeVertexNormals();
      const solid = new THREE.Mesh(g, new THREE.MeshPhongMaterial({
        color: 0x51707f, side: THREE.DoubleSide, flatShading: true,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
        clippingPlanes: this.clip.planes,
      }));
      const wire = new THREE.Mesh(g, new THREE.MeshBasicMaterial({
        color: 0x9fc4d8, wireframe: true, transparent: true, opacity: 0.28,
        clippingPlanes: this.clip.planes,
      }));
      this.meshGroup.add(solid, wire);
    }
    this.geoGroup.visible = false;
    this.glyphGroup.visible = false;
    this.meshGroup.visible = true;
    this.resultGroup.visible = false;
    this.anim = null;
    this.requestRender();
  }

  showResult(payload, { defScale = 0, animate = false } = {}) {
    disposeGroup(this.resultGroup);
    const vtx = decode(payload.vtx);
    const tri = decode(payload.tri);
    const scal = decode(payload.values);
    const disp = payload.disp ? decode(payload.disp) : new Float32Array(vtx.length);

    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(vtx, 3));
    g.setAttribute("aDisp", new THREE.BufferAttribute(disp, 3));
    g.setAttribute("aScalar", new THREE.BufferAttribute(scal, 1));
    g.setIndex(new THREE.BufferAttribute(tri, 1));
    g.computeVertexNormals();

    const mat = new THREE.ShaderMaterial({
      uniforms: {
        uDef: { value: defScale },
        uMin: { value: payload.min }, uMax: { value: payload.max },
        uBands: { value: contourStyle.bands },
        tMap: { value: this.cmapTex },
      },
      vertexShader: `
        attribute vec3 aDisp; attribute float aScalar;
        uniform float uDef;
        varying float vS; varying vec3 vN;
        void main() {
          vS = aScalar;
          vN = normalMatrix * normal;
          vec3 p = position + aDisp * uDef;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
        }`,
      fragmentShader: `
        uniform sampler2D tMap; uniform float uMin, uMax, uBands;
        varying float vS; varying vec3 vN;
        void main() {
          float t = clamp((vS - uMin) / max(uMax - uMin, 1e-30), 0.0, 1.0);
          // uBands > 0: snap to the centre of a discrete band, which is what
          // gives commercial post-processors their hard contour boundaries.
          if (uBands > 0.5) {
            t = (floor(min(t * uBands, uBands - 1.0)) + 0.5) / uBands;
          }
          vec3 c = texture2D(tMap, vec2(t, 0.5)).rgb;

          // Shading is deliberately almost flat. The skin of a tet mesh shares
          // one averaged normal per node, so across a crease — every part edge,
          // every fillet run-out — neighbouring facets get normals that differ
          // by tens of degrees. At the old 0.28 amplitude that painted visible
          // blotches inside what should read as one flat contour band, and a
          // contour plot is read by COLOUR: any shading that competes with the
          // band colour is actively misleading. abs() made it worse by folding
          // brightness back up past the silhouette. Now: a headlight term, a
          // normal flipped to face the camera, and 12 % of range.
          vec3 n = normalize(vN);
          if (!gl_FrontFacing) n = -n;
          float ndl = clamp(dot(n, normalize(vec3(0.22, 0.33, 1.0))), 0.0, 1.0);
          float l = 0.88 + 0.12 * ndl;
          gl_FragColor = vec4(c * l, 1.0);
        }`,
      side: THREE.DoubleSide,
      clipping: true,
      clippingPlanes: this.clip.planes,
      polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
    });
    const mesh = new THREE.Mesh(g, mat);
    this.resultGroup.add(mesh);
    this._resultMaterial = mat;
    this._defScale = defScale;
    this._resultData = { vtx, disp, scal, tri, payload };

    // Element-face wireframe over the contours, deformed by the same amount.
    // Uses the element outlines from the solver, not the display
    // sub-triangulation, so it shows real element boundaries.
    if (payload.edges) {
      const eg = new THREE.BufferGeometry();
      eg.setAttribute("position", new THREE.BufferAttribute(vtx, 3));
      eg.setAttribute("aDisp", new THREE.BufferAttribute(disp, 3));
      eg.setIndex(new THREE.BufferAttribute(decode(payload.edges), 1));
      const emat = new THREE.ShaderMaterial({
        uniforms: { uDef: { value: defScale },
                    uCol: { value: new THREE.Color(this._edgeColor()) } },
        vertexShader: `
          attribute vec3 aDisp; uniform float uDef;
          void main() {
            vec3 p = position + aDisp * uDef;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
          }`,
        fragmentShader: `
          uniform vec3 uCol;
          void main() { gl_FragColor = vec4(uCol, 0.22); }`,
        transparent: true, depthWrite: false,
        clipping: true, clippingPlanes: this.clip.planes,
      });
      this._edgeLines = new THREE.LineSegments(eg, emat);
      this._edgeLines.visible = this.showResultMesh;
      this.resultGroup.add(this._edgeLines);
    } else {
      this._edgeLines = null;
    }

    // Invisible twin carrying DEFORMED positions, so hover picking hits what
    // is actually drawn. The shader deforms on the GPU; a raycast cannot see
    // that, and against undeformed geometry the readout would be offset.
    const pg = new THREE.BufferGeometry();
    pg.setAttribute("position", new THREE.BufferAttribute(vtx.slice(), 3));
    pg.setIndex(new THREE.BufferAttribute(tri, 1));
    this._pickMesh = new THREE.Mesh(pg, new THREE.MeshBasicMaterial({ visible: false }));
    this._pickMesh.frustumCulled = false;
    this.resultGroup.add(this._pickMesh);
    this._syncPickGeometry();

    this.geoGroup.visible = false;
    this.glyphGroup.visible = false;
    this.meshGroup.visible = false;
    this.resultGroup.visible = true;
    this.anim = animate ? {} : null;
    if (!animate) mat.uniforms.uDef.value = defScale;
    this.requestRender();
  }

  /** Push the current deformed positions into the pick geometry. */
  _syncPickGeometry() {
    if (!this._pickMesh || !this._resultData) return;
    const { vtx, disp } = this._resultData;
    const pos = this._pickMesh.geometry.getAttribute("position");
    const k = this._defScale || 0;
    for (let i = 0; i < vtx.length; i++) pos.array[i] = vtx[i] + disp[i] * k;
    pos.needsUpdate = true;
    this._pickMesh.geometry.computeBoundingSphere();
  }

  setResultMesh(on) {
    this.showResultMesh = on;
    if (this._edgeLines) this._edgeLines.visible = on;
    this.requestRender();
  }

  /** Nearest node to the cursor, with its value — the Ansys "probe" readout. */
  probeAt(e) {
    if (!this._pickMesh || !this._resultData) return null;
    const r = this.canvas.getBoundingClientRect();
    this.pointer.set(((e.clientX - r.left) / r.width) * 2 - 1,
                     -((e.clientY - r.top) / r.height) * 2 + 1);
    this.ray.setFromCamera(this.pointer, this.camera);
    const hit = this.ray.intersectObject(this._pickMesh, false)[0];
    if (!hit || !hit.face) return null;

    const pos = this._pickMesh.geometry.getAttribute("position");
    const { vtx, scal } = this._resultData;
    let best = -1, bd = Infinity;
    for (const idx of [hit.face.a, hit.face.b, hit.face.c]) {
      const dx = pos.array[idx * 3] - hit.point.x;
      const dy = pos.array[idx * 3 + 1] - hit.point.y;
      const dz = pos.array[idx * 3 + 2] - hit.point.z;
      const d = dx * dx + dy * dy + dz * dz;
      if (d < bd) { bd = d; best = idx; }
    }
    if (best < 0) return null;
    return {
      value: scal[best],
      // report the ORIGINAL coordinates: where the node is on the part
      xyz: [vtx[best * 3], vtx[best * 3 + 1], vtx[best * 3 + 2]],
      screen: this._project(pos.array[best * 3], pos.array[best * 3 + 1],
                            pos.array[best * 3 + 2]),
    };
  }

  _project(x, y, z) {
    const v = new THREE.Vector3(x, y, z).project(this.camera);
    const r = this.canvas.getBoundingClientRect();
    return [(v.x + 1) / 2 * r.width, (1 - v.y) / 2 * r.height];
  }

  setDeform(scale) {
    this._defScale = scale;
    if (this._resultMaterial && !this.anim) {
      this._resultMaterial.uniforms.uDef.value = scale;
      if (this._edgeLines) this._edgeLines.material.uniforms.uDef.value = scale;
      this._syncPickGeometry();
      this.requestRender();
    }
  }

  setAnimate(on) {
    this.anim = on ? {} : null;
    if (!on && this._resultMaterial) {
      this._resultMaterial.uniforms.uDef.value = this._defScale;
    }
    this.requestRender();
  }

  // ---------------- clipping ----------------
  setClip(axis, frac) {
    this.clip.axis = axis;
    this.clip.frac = frac;
    this.clip.planes.length = 0;
    if (axis) {
      const i = { x: 0, y: 1, z: 2 }[axis];
      const lo = this.bbox[i], hi = this.bbox[i + 3];
      const pos = lo + (hi - lo) * frac;
      const n = new THREE.Vector3();
      n.setComponent(i, -1);
      this.clip.planes.push(new THREE.Plane(n, pos));
    }
    // material.clippingPlanes holds a reference to the shared array — force update
    this.scene.traverse((o) => { if (o.material) o.material.needsUpdate = true; });
    this.requestRender();
  }
}
