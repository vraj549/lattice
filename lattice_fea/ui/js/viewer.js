import * as THREE from "three";
import { Orbit } from "./orbit.js";
import { decode } from "./b64.js";
import { makeTexture } from "./colormap.js";

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
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x0c1216);
    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 10000);
    this.orbit = new Orbit(this.camera, canvas, () => this.requestRender());

    this.scene.add(new THREE.HemisphereLight(0xcfd8e0, 0x2a3238, 0.9));
    const key = new THREE.DirectionalLight(0xffffff, 1.1);
    key.position.set(1, 0.6, 1.4);
    this.scene.add(key);
    const fill = new THREE.DirectionalLight(0x88a0b8, 0.35);
    fill.position.set(-1, -0.5, -0.6);
    this.scene.add(fill);

    this.geoGroup = new THREE.Group();     // B-rep faces + edges
    this.meshGroup = new THREE.Group();    // mesh preview
    this.resultGroup = new THREE.Group();  // contour mesh
    this.scene.add(this.geoGroup, this.meshGroup, this.resultGroup);

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

  // ---------------- render loop ----------------
  requestRender() { this._needsRender = true; }

  _loop() {
    requestAnimationFrame(() => this._loop());
    if (this.anim) {
      const t = performance.now() / 1000;
      const mat = this._resultMaterial;
      if (mat) {
        mat.uniforms.uDef.value = this._defScale * Math.sin(2 * Math.PI * 1.2 * t);
        this._needsRender = true;
      }
    }
    if (this._needsRender) {
      this._needsRender = false;
      this.renderer.render(this.scene, this.camera);
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
        uniform sampler2D tMap; uniform float uMin, uMax;
        varying float vS; varying vec3 vN;
        void main() {
          float t = clamp((vS - uMin) / max(uMax - uMin, 1e-30), 0.0, 1.0);
          vec3 c = texture2D(tMap, vec2(t, 0.5)).rgb;
          float l = 0.72 + 0.28 * abs(normalize(vN).z);
          gl_FragColor = vec4(c * l, 1.0);
        }`,
      side: THREE.DoubleSide,
      clipping: true,
      clippingPlanes: this.clip.planes,
    });
    const mesh = new THREE.Mesh(g, mat);
    this.resultGroup.add(mesh);
    this._resultMaterial = mat;
    this._defScale = defScale;

    this.geoGroup.visible = false;
    this.meshGroup.visible = false;
    this.resultGroup.visible = true;
    this.anim = animate ? {} : null;
    if (!animate) mat.uniforms.uDef.value = defScale;
    this.requestRender();
  }

  setDeform(scale) {
    this._defScale = scale;
    if (this._resultMaterial && !this.anim) {
      this._resultMaterial.uniforms.uDef.value = scale;
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
