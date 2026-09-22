/**
 * 3Dconnexion SpaceMouse over WebHID.
 *
 * Talks to the device directly rather than through 3DxWare, because Lattice
 * is self-hosted and a browser page cannot see a vendor driver's events. The
 * cost is that WebHID is Chromium-only (Chrome, Edge, Brave, Opera) and needs
 * a secure context — localhost counts, so a normal Lattice session qualifies.
 * Firefox and Safari have not shipped it; `available()` says so rather than
 * letting the button fail silently.
 *
 * The device is a VELOCITY controller: every report says how far the cap is
 * being pushed, not how far anything moved, and it keeps reporting while it
 * is held. So the values are rates, and the viewport integrates them against
 * real frame time — see Navigator.applyMotion.
 *
 * Report layout (3Dconnexion HID, all little-endian signed 16-bit):
 *
 *   id 1   tx ty tz            translation      (6 bytes)
 *   id 1   tx ty tz rx ry rz   both, newer devices only (12 bytes)
 *   id 2   rx ry rz            rotation         (6 bytes)
 *   id 3   button bitmask
 *
 * Device axes are right-handed with +X right, +Y away from the user and +Z
 * up, which is why ty drives zoom and tz drives vertical pan.
 */

// 0x046d is Logitech, who owned 3Dconnexion when the older devices shipped;
// 0x256f is 3Dconnexion's own. Both are still in the field.
export const VENDOR_IDS = [0x046d, 0x256f];

/** Full-scale deflection. Devices differ; this is the common one and the
 *  normalise step clamps anything louder. */
const FULL_SCALE = 350;

/** Below this the cap is at rest. A SpaceMouse does not return to exactly
 *  zero — without a deadzone the model drifts whenever nobody is touching
 *  it, which reads as a bug in the viewport rather than in the device. */
export const DEADZONE = 0.06;

export function available() {
  return typeof navigator !== "undefined" && !!navigator.hid;
}

/** One axis, from raw counts to a -1..1 rate with the deadzone removed.
 *
 *  The deadzone is subtracted and the remainder rescaled, rather than simply
 *  zeroed below the threshold: zeroing leaves a step at the edge of the
 *  deadzone, so the model jumps the moment it starts moving. */
export function normalizeAxis(raw, scale = FULL_SCALE, dead = DEADZONE) {
  let v = Math.max(-1, Math.min(1, raw / scale));
  const s = Math.sign(v);
  v = Math.abs(v);
  if (v <= dead) return 0;
  return s * (v - dead) / (1 - dead);
}

/** Decode one HID report into a motion delta, or null if it carries none. */
export function decodeReport(reportId, view) {
  const i16 = (o) => (o + 1 < view.byteLength ? view.getInt16(o, true) : 0);
  if (reportId === 1) {
    const m = { tx: i16(0), ty: i16(2), tz: i16(4) };
    // Newer devices pack all six axes into report 1; older ones send
    // rotation separately as report 2.
    if (view.byteLength >= 12) {
      m.rx = i16(6); m.ry = i16(8); m.rz = i16(10);
    }
    return m;
  }
  if (reportId === 2) return { rx: i16(0), ry: i16(2), rz: i16(4) };
  return null;
}

export class SpaceMouse {
  /** @param {(motion: object) => void} onMotion  rates in -1..1 */
  constructor(onMotion, onState = () => {}) {
    this.onMotion = onMotion;
    this.onState = onState;
    this.device = null;
    this.buttons = 0;
    this._raw = { tx: 0, ty: 0, tz: 0, rx: 0, ry: 0, rz: 0 };
    this._idle = null;
  }

  get connected() { return !!(this.device && this.device.opened); }

  /**
   * Ask the user to pick a device. Must be called from a click: WebHID
   * requires a user gesture, and there is no way around that by design.
   */
  async connect() {
    if (!available()) {
      throw new Error("This browser has no WebHID. A SpaceMouse needs "
                    + "Chrome, Edge or another Chromium browser.");
    }
    const [dev] = await navigator.hid.requestDevice({
      filters: VENDOR_IDS.map((vendorId) => ({ vendorId })) });
    if (!dev) return null;                  // the picker was dismissed
    await this._attach(dev);
    return dev;
  }

  /** Reopen a device the user has already granted, with no prompt. */
  async reconnect() {
    if (!available()) return null;
    const devs = await navigator.hid.getDevices();
    const dev = devs.find((d) => VENDOR_IDS.includes(d.vendorId));
    if (!dev) return null;
    await this._attach(dev);
    return dev;
  }

  async _attach(dev) {
    if (!dev.opened) await dev.open();
    this.device = dev;
    dev.addEventListener("inputreport", (e) => this._onReport(e));
    this.onState({ connected: true, name: dev.productName || "SpaceMouse" });
  }

  async disconnect() {
    const dev = this.device;
    this.device = null;
    this._stopIdle();
    if (dev && dev.opened) { try { await dev.close(); } catch { /* already gone */ } }
    this.onState({ connected: false });
  }

  _onReport(e) {
    if (e.reportId === 3) {
      this.buttons = e.data.byteLength ? e.data.getUint8(0) : 0;
      return;
    }
    const raw = decodeReport(e.reportId, e.data);
    if (!raw) return;
    Object.assign(this._raw, raw);
    const m = {};
    let any = false;
    for (const k of ["tx", "ty", "tz", "rx", "ry", "rz"]) {
      m[k] = normalizeAxis(this._raw[k] || 0);
      if (m[k]) any = true;
    }
    this.onMotion(m);
    // The device stops reporting when the cap is released, so the last
    // non-zero report would otherwise be held forever. A short watchdog
    // zeroes it rather than trusting a final all-zero report to arrive.
    this._stopIdle();
    if (any) this._idle = setTimeout(() => {
      this._raw = { tx: 0, ty: 0, tz: 0, rx: 0, ry: 0, rz: 0 };
      this.onMotion({ tx: 0, ty: 0, tz: 0, rx: 0, ry: 0, rz: 0 });
    }, 120);
  }

  _stopIdle() {
    if (this._idle) { clearTimeout(this._idle); this._idle = null; }
  }
}
