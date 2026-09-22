import * as THREE from "three";

/**
 * Viewport navigation, mapped to Siemens NX.
 *
 * The previous controller was a turntable: an azimuth about world Z and a
 * polar angle down from it. That has poles, and the standard Top view sits
 * exactly on one — at phi = 1e-3 a half-radian horizontal drag moved the
 * camera 0.05 of 100 units, so the model spun in place instead of orbiting
 * and horizontal dragging simply stopped working. The epsilon in the Top and
 * Bottom views existed to hide the same defect: looking straight down the
 * up-vector leaves roll undefined.
 *
 * NX has no such pole because NX is a free trackball: the drag axes are the
 * screen's own axes, taken from the camera's current orientation, so there is
 * nowhere the controls degrade and the model can be tumbled without limit.
 * Orientation is held as a quaternion for the same reason — Euler angles
 * would put the gimbal back.
 *
 * Mouse map (NX defaults):
 *
 *   MB2 drag ................ rotate
 *   Shift + MB2, MB2 + MB3 .. pan
 *   Ctrl + MB2, MB1 + MB2 ... zoom
 *   wheel ................... zoom
 *   MB1 ..................... selection; never navigation
 *
 * and the edge zones, which NX decides from where the cursor was when the
 * drag STARTED:
 *
 *   left or right edge ...... rotate about the view's horizontal axis only
 *   bottom edge ............. rotate about the view's vertical axis only
 *   top edge ................ spin about the axis normal to the screen
 *
 * Sources: Siemens NX help ("Rotate about a center of rotation") and the NX
 * community threads linked in docs/NAVIGATION.md.
 */

const ROT_PER_PX = 0.007;
const ZOOM_PER_PX = 0.006;

/** Fraction of the window each edge band occupies, and its pixel bounds. */
export const EDGE_FRACTION = 0.08;
export const EDGE_MIN_PX = 24;
export const EDGE_MAX_PX = 80;

/**
 * Which rotation a drag starting at (x, y) gets.
 *
 * Corners belong to the top/bottom band rather than the side band: the test
 * is ordered, and a rule that reads top-to-bottom is one a user can predict
 * from the cursor without being told.
 */
export function edgeZone(x, y, w, h, frac = EDGE_FRACTION) {
  const clamp = (v) => Math.min(Math.max(v, EDGE_MIN_PX), EDGE_MAX_PX);
  const bx = clamp(w * frac), by = clamp(h * frac);
  if (h > 3 * by) {
    if (y <= by) return "spin";        // top edge: about the screen normal
    if (y >= h - by) return "vertical"; // bottom edge: about the screen vertical
  }
  if (w > 3 * bx && (x <= bx || x >= w - bx)) return "horizontal";
  return "free";
}

/**
 * What a drag does, from the buttons held and the modifier keys.
 *
 * Reads the live `buttons` bitmask rather than the button that opened the
 * drag, so NX's chords work the way they do in NX: press MB3 while MB2 is
 * already down and the rotate becomes a pan without letting go.
 *
 * Returns null for "not navigation" — MB1 alone is selection and MB3 alone
 * opens a menu, and swallowing either would break picking.
 */
export function dragMode(buttons, { shift = false, ctrl = false,
                                    leftDragRotates = false } = {}) {
  const L = !!(buttons & 1), R = !!(buttons & 2), M = !!(buttons & 4);
  if (M && R) return "pan";
  if (M && L) return "zoom";
  if (M) return shift ? "pan" : (ctrl ? "zoom" : "rotate");
  // Laptops and trackpads have no middle button. Off by default: with it on,
  // MB1 can no longer be a plain selection drag.
  if (leftDragRotates && L && !R) return shift ? "pan" : "rotate";
  return null;
}

const CURSORS = { free: "grabbing", horizontal: "ns-resize",
                  vertical: "ew-resize", spin: "alias" };

export class Navigator {
  constructor(camera, dom, onChange) {
    this.camera = camera;
    this.dom = dom;
    this.onChange = onChange || (() => {});
    this.target = new THREE.Vector3();
    this.dist = 100;
    this.orient = new THREE.Quaternion();
    this._diag = 100;
    this._drag = null;
    this.leftDragRotates = false;
    this.setStandardView("iso");
    this._bind();
  }

  // ---- the screen's axes, in world space -------------------------------
  right() { return new THREE.Vector3(1, 0, 0).applyQuaternion(this.orient); }
  up() { return new THREE.Vector3(0, 1, 0).applyQuaternion(this.orient); }
  forward() { return new THREE.Vector3(0, 0, 1).applyQuaternion(this.orient); }

  /** Turn the camera about a world axis, keeping it aimed at the target. */
  _spin(axis, angle) {
    if (!angle) return;
    this.orient.premultiply(
      new THREE.Quaternion().setFromAxisAngle(axis, angle));
    this.orient.normalize();
  }

  /**
   * Rotate from a drag.
   *
   * The axes come from the camera's current orientation, which is what makes
   * this a trackball rather than a turntable: dragging horizontally always
   * turns the model about the axis that is vertical ON SCREEN, wherever the
   * camera happens to be, so there is no orientation in which the control
   * stops responding.
   */
  rotate(dx, dy, zone = "free") {
    if (zone === "horizontal") this._spin(this.right(), -dy * ROT_PER_PX);
    else if (zone === "vertical") this._spin(this.up(), -dx * ROT_PER_PX);
    else if (zone === "spin") this._spin(this.forward(), -dx * ROT_PER_PX);
    else {
      this._spin(this.right(), -dy * ROT_PER_PX);
      this._spin(this.up(), -dx * ROT_PER_PX);
    }
    this.update();
  }

  pan(dx, dy) {
    const k = this.dist * 0.0016;
    this.target.addScaledVector(this.right(), -dx * k);
    this.target.addScaledVector(this.up(), dy * k);
    this.update();
  }

  zoom(factor) {
    this.dist = this._clampDist(this.dist * factor);
    this.update();
  }

  // ---- input -----------------------------------------------------------
  _bind() {
    const dom = this.dom;
    dom.addEventListener("pointerdown", (e) => {
      const mode = dragMode(e.buttons, {
        shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey,
        leftDragRotates: this.leftDragRotates });
      if (!mode) return;
      // NX decides the constrained axis from where the cursor was when the
      // drag started, not from where it wanders to.
      const r = dom.getBoundingClientRect();
      const zone = edgeZone(e.clientX - r.left, e.clientY - r.top, r.width, r.height);
      dom.setPointerCapture(e.pointerId);
      this._drag = { x: e.clientX, y: e.clientY, zone };
      dom.style.cursor = mode === "rotate" ? (CURSORS[zone] || "grabbing")
                       : mode === "pan" ? "move" : "ns-resize";
      e.preventDefault();
    });

    dom.addEventListener("pointermove", (e) => {
      if (!this._drag) return;
      const dx = e.clientX - this._drag.x, dy = e.clientY - this._drag.y;
      this._drag.x = e.clientX; this._drag.y = e.clientY;
      // Re-read every move: chording a second button mid-drag switches mode,
      // which is how MB2+MB3 pan works without releasing MB2.
      const mode = dragMode(e.buttons, {
        shift: e.shiftKey, ctrl: e.ctrlKey || e.metaKey,
        leftDragRotates: this.leftDragRotates });
      if (mode === "rotate") this.rotate(dx, dy, this._drag.zone);
      else if (mode === "pan") this.pan(dx, dy);
      // Up zooms in, matching the wheel. NX ties the drag-zoom gesture to the
      // same direction preference as the wheel, and having the two disagree
      // inside one viewport is worse than either direction on its own.
      else if (mode === "zoom") this.zoom(Math.exp(dy * ZOOM_PER_PX));
    });

    const end = () => { this._drag = null; dom.style.cursor = ""; };
    dom.addEventListener("pointerup", end);
    dom.addEventListener("pointercancel", end);
    dom.addEventListener("lostpointercapture", end);
    dom.addEventListener("contextmenu", (e) => e.preventDefault());

    dom.addEventListener("wheel", (e) => {
      e.preventDefault();
      const k = Math.exp(e.deltaY * 0.0012);
      const before = this._cursorRay(e);
      this.dist = this._clampDist(this.dist * k);
      this.update();
      const after = this._cursorRay(e);
      if (before && after) {
        // Hold the point under the cursor still, the way every CAD viewport
        // does. Zooming to the screen centre means re-panning constantly to
        // inspect a corner of the model.
        this.target.add(before.sub(after));
        this.update();
      }
    }, { passive: false });
  }

  /**
   * Apply one frame of 6-DOF motion, in device units already scaled to -1..1.
   *
   * A SpaceMouse is a velocity device: it reports how far the cap is pushed,
   * not how far anything moved, so the deltas are per-second rates and have
   * to be multiplied by the frame time or the speed depends on the frame
   * rate. Translation scales with viewing distance for the same reason zoom
   * does — a millimetre of cap travel should cover the same fraction of the
   * screen whatever the model's size.
   */
  applyMotion({ tx = 0, ty = 0, tz = 0, rx = 0, ry = 0, rz = 0 }, dt = 1 / 60) {
    const moved = tx || ty || tz || rx || ry || rz;
    if (!moved) return false;
    const t = this.dist * 1.2 * dt;
    this.target.addScaledVector(this.right(), tx * t);
    this.target.addScaledVector(this.up(), tz * t);
    if (ty) this.dist = this._clampDist(this.dist * Math.exp(ty * 1.2 * dt));
    const a = 1.8 * dt;
    this._spin(this.right(), -rx * a);
    this._spin(this.up(), -rz * a);
    this._spin(this.forward(), ry * a);
    this.update();
    return true;
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
    const n = this.forward().negate();
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
    this.camera.quaternion.copy(this.orient);
    this.camera.up.copy(this.up());
    this.camera.position.copy(this.target).addScaledVector(this.forward(), this.dist);
    this.camera.near = Math.max(this.dist * 1e-3, this._diag * 1e-4 || 1e-4);
    this.camera.far = this.dist + (this._diag || 1) * 10;
    this.camera.updateProjectionMatrix();
    this.camera.updateMatrixWorld(true);
    this.onChange();
  }

  /**
   * Snap to a named view.
   *
   * Z-up, the FEA convention. Top and Bottom can be exactly vertical now:
   * the roll is fixed by the up-vector chosen here rather than left
   * undefined by a polar angle, so the nudge off the pole the old controller
   * needed — and the dead horizontal drag that came with it — is gone.
   */
  setStandardView(name) {
    const v = {
      iso:    [[0.612, -0.612, 0.5], [0, 0, 1]],
      top:    [[0, 0, 1], [0, 1, 0]],
      bottom: [[0, 0, -1], [0, -1, 0]],
      front:  [[0, -1, 0], [0, 0, 1]],
      back:   [[0, 1, 0], [0, 0, 1]],
      right:  [[1, 0, 0], [0, 0, 1]],
      left:   [[-1, 0, 0], [0, 0, 1]],
    }[name];
    if (!v) return;
    const [dir, up] = v;
    const m = new THREE.Matrix4().lookAt(
      new THREE.Vector3(...dir), new THREE.Vector3(0, 0, 0),
      new THREE.Vector3(...up));
    this.orient.setFromRotationMatrix(m).normalize();
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
