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
        this.phi = Math.min(Math.PI - 0.02, Math.max(0.02, this.phi - dy * 0.006));
      }
      this.update();
    });
    const end = (e) => { this._drag = null; };
    dom.addEventListener("pointerup", end);
    dom.addEventListener("pointercancel", end);
    dom.addEventListener("contextmenu", (e) => e.preventDefault());
    dom.addEventListener("wheel", (e) => {
      e.preventDefault();
      this.dist *= Math.exp(e.deltaY * 0.0012);
      this.update();
    }, { passive: false });
  }

  update() {
    // Z-up, the FEA convention
    const sp = Math.sin(this.phi), cp = Math.cos(this.phi);
    const st = Math.sin(this.theta), ct = Math.cos(this.theta);
    this.camera.position.set(
      this.target.x + this.dist * sp * ct,
      this.target.y + this.dist * sp * st,
      this.target.z + this.dist * cp);
    this.camera.up.set(0, 0, 1);
    this.camera.lookAt(this.target);
    this.onChange();
  }

  fit(bbox) {
    const [x0, y0, z0, x1, y1, z1] = bbox;
    this.target.set((x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2);
    const diag = Math.hypot(x1 - x0, y1 - y0, z1 - z0) || 1;
    this.dist = diag * 1.35;
    this.camera.near = diag / 500;
    this.camera.far = diag * 30;
    this.camera.updateProjectionMatrix();
    this.update();
  }
}
