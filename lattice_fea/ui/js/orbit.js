import * as THREE from "three";

// Minimal CAD-style orbit controls: LMB rotate, RMB/shift pan, wheel zoom.
export class Orbit {
  constructor(camera, dom, onChange) {
    this.camera = camera;
    this.dom = dom;
    this.onChange = onChange || (() => {});
    this.target = new THREE.Vector3();
    this.theta = Math.PI / 4;
    this.phi = Math.PI / 3;
    this.dist = 100;
    this._diag = 100;
    this._drag = null;

    dom.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 && e.button !== 1 && e.button !== 2) return;
      dom.setPointerCapture(e.pointerId);
      const pan = e.button !== 0 || e.shiftKey;
      this._drag = { x: e.clientX, y: e.clientY, pan };
    });
    dom.addEventListener("pointermove", (e) => {
      if (!this._drag) return;
      const dx = e.clientX - this._drag.x, dy = e.clientY - this._drag.y;
      this._drag.x = e.clientX; this._drag.y = e.clientY;
      if (this._drag.pan) {
        const scale = this.dist * 0.0016;
        const right = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 0);
        const up = new THREE.Vector3().setFromMatrixColumn(this.camera.matrix, 1);
        this.target.addScaledVector(right, -dx * scale);
        this.target.addScaledVector(up, dy * scale);
      } else {
        this.theta -= dx * 0.006;
        // Free rotation: phi used to clamp just short of the poles, so
        // dragging vertically hit an invisible wall and stopped. Wrapping
        // through the pole and flipping the up-vector lets you keep going.
        this.phi -= dy * 0.006;
        const TWO_PI = Math.PI * 2;
        this.phi = ((this.phi % TWO_PI) + TWO_PI) % TWO_PI;
      }
      this.update();
    });
    const end = (e) => { this._drag = null; };
    dom.addEventListener("pointerup", end);
    dom.addEventListener("pointercancel", end);
    dom.addEventListener("contextmenu", (e) => e.preventDefault());
    // Zoom toward the cursor, the way every CAD viewport does: the point
    // under the pointer stays put while everything else scales around it.
    // Zooming to the screen centre means constantly re-panning to inspect a
    // corner of the model.
    dom.addEventListener("wheel", (e) => {
      e.preventDefault();
      const k = Math.exp(e.deltaY * 0.0012);
      const before = this._cursorRay(e);
      this.dist = this._clampDist(this.dist * k);
      this.update();
      const after = this._cursorRay(e);
      if (before && after) {
        // shift the target so the ray through the cursor still passes
        // through the same world point it did before the zoom
        this.target.add(before.sub(after));
        this.update();
      }
    }, { passive: false });
  }

  /** World point where the cursor ray meets the plane through the target
   *  facing the camera — the anchor that zoom-to-cursor holds fixed. */
  _cursorRay(e) {
    const r = this.dom.getBoundingClientRect();
    if (!r.width || !r.height) return null;
    const ndc = new THREE.Vector3(
      ((e.clientX - r.left) / r.width) * 2 - 1,
      -((e.clientY - r.top) / r.height) * 2 + 1,
      0.5);
    ndc.unproject(this.camera);
    const dir = ndc.sub(this.camera.position).normalize();
    const n = new THREE.Vector3().subVectors(this.target, this.camera.position).normalize();
    const denom = dir.dot(n);
    if (Math.abs(denom) < 1e-6) return null;
    const t = new THREE.Vector3().subVectors(this.target, this.camera.position).dot(n) / denom;
    return this.camera.position.clone().addScaledVector(dir, t);
  }

  _clampDist(d) {
    // Unbounded zoom walked the camera inside the model (dist -> 0, lookAt
    // degenerate) or out past the far plane, with no way back but Fit.
    const lo = this._diag * 1e-3 || 1e-3;
    const hi = this._diag * 200 || 1e6;
    return Math.min(hi, Math.max(lo, d));
  }

  update() {
    // Z-up, the FEA convention. phi runs the full circle, so past the pole
    // the up-vector flips to keep the view continuous rather than snapping.
    const sp = Math.sin(this.phi), cp = Math.cos(this.phi);
    const st = Math.sin(this.theta), ct = Math.cos(this.theta);
    this.camera.position.set(
      this.target.x + this.dist * sp * ct,
      this.target.y + this.dist * sp * st,
      this.target.z + this.dist * cp);
    this.camera.up.set(0, 0, sp >= 0 ? 1 : -1);
    this.camera.lookAt(this.target);
    this.camera.near = Math.max(this.dist * 1e-3, this._diag * 1e-4 || 1e-4);
    this.camera.far = this.dist + (this._diag || 1) * 10;
    this.camera.updateProjectionMatrix();
    this.onChange();
  }

  /**
   * Snap to a named view.
   *
   * Camera sits at target + dist(sin phi cos theta, sin phi sin theta, cos phi)
   * with Z up, so the six orthographic views are two angles each. Top and
   * bottom are nudged a thousandth off the pole because looking straight down
   * the up-vector leaves the camera's roll undefined and the view rolls at
   * random as it lands.
   */
  setStandardView(name) {
    const E = 1e-3;
    const v = {
      iso:    [Math.PI / 4, Math.PI / 3],
      top:    [0, E],
      bottom: [0, Math.PI - E],
      front:  [-Math.PI / 2, Math.PI / 2],
      back:   [Math.PI / 2, Math.PI / 2],
      right:  [0, Math.PI / 2],
      left:   [Math.PI, Math.PI / 2],
    }[name];
    if (!v) return;
    [this.theta, this.phi] = v;
    this.update();
  }

  fit(bbox) {
    const [x0, y0, z0, x1, y1, z1] = bbox;
    this.target.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
    const diag = Math.hypot(x1 - x0, y1 - y0, z1 - z0) || 1;
    this._diag = diag;
    this.dist = diag * 1.35;
    this.update();
  }
}
