import * as THREE from "three";
import { Orbit } from "./orbit.js";
import { decode } from "./b64.js";
import { makeTexture, contourStyle } from "./colormap.js";
import { AxisTriad } from "./axes.js";
import { buildGlyphs } from "./glyphs.js";

const SOLID_COLORS = [0x7fa8bd, 0xb99f7a, 0x8fae8a, 0xa48fb8, 0xbd9a8f, 0x8f9ebd];
const HOVER = 0xe8b06a, SELECTED = 0xe89344, SUPPORT = 0x8d7dec, LOAD = 0xd97a28,
      BOLT = 0x4f88b0;

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
    // Read the resolved token rather than guessing, so the viewport ground
    // always matches the chrome it sits inside.
    const cs = getComputedStyle(document.documentElement);
    const dark = document.documentElement.getAttribute("data-theme") === "dark"
      || (!document.documentElement.getAttribute("data-theme")
          && window.matchMedia("(prefers-color-scheme: dark)").matches);
    const bg = dark ? 0x11161a : 0xe7ebef;
    this.scene.background = new THREE.Color(bg);
    this.renderer.setClearColor(bg, 1);
    // model surfaces need a touch more ambient on a light ground
    if (this._hemi) this._hemi.intensity = dark ? 0.9 : 1.15;
    this.requestRender();
  }

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

  // ---------------- geometry ----------------
  setGeometry(tess, meta) {
    this.geoGroup.clear();
    this.faceMeshes.clear();
    this.faceInfo.clear();
    this.solidOfFace.clear();
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
    this.glyphGroup.clear();
    if (!setup || !geometry || !this.faceInfo.size) { this.requestRender(); return; }
    try {
      this.glyphGroup.add(buildGlyphs(setup, geometry, this.faceInfo));
    } catch (e) {
      console.warn("glyph build failed:", e);   // never let symbols break the view
    }
    this.glyphGroup.visible = this.geoGroup.visible;
    this.requestRender();
  }

  setHiddenSolids(set) {
    this.hiddenSolids = set;
    for (const mesh of this.faceMeshes.values()) {
      const solids = mesh.userData.solids;
      mesh.visible = !(solids.length && solids.every((s) => set.has(s)));
    }
    this.requestRender();
  }

  _applyFaceColors() {
    for (const [tag, mesh] of this.faceMeshes) {
      let c = mesh.userData.base;
      const st = this.faceStates.get(tag);
      if (st === "support") c = SUPPORT;
      else if (st === "load") c = LOAD;
      else if (st === "bolt") c = BOLT;
      if (this.pickSet.has(tag)) c = SELECTED;
      if (tag === this.hoverTag && (this.mode !== "view")) c = HOVER;
      mesh.material.color.setHex(c);
      mesh.material.emissive?.setHex(tag === this.hoverTag && this.mode !== "view" ? 0x332211 : 0x000000);
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
      if (hit) this.cb.onPickPoint?.({ x: hit.point.x, y: hit.point.y, z: hit.point.z });
    }
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
      this.meshGroup.clear();
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
    this.resultGroup.clear();
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
          float l = 0.72 + 0.28 * abs(normalize(vN).z);
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
        uniforms: { uDef: { value: defScale }, uCol: { value: new THREE.Color(0x1b2228) } },
        vertexShader: `
          attribute vec3 aDisp; uniform float uDef;
          void main() {
            vec3 p = position + aDisp * uDef;
            gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
          }`,
        fragmentShader: `
          uniform vec3 uCol;
          void main() { gl_FragColor = vec4(uCol, 0.30); }`,
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
