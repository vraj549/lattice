// SpaceMouse decoding. Run via tests/test_nav.py (pytest), or directly with
// `node tests/test_spacemouse.mjs`.
//
// The device itself cannot be in CI, so what is tested is everything between
// the HID report and the viewport: report layouts, axis normalisation and the
// deadzone. Those are where a wrong answer looks like a broken viewport.

import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(resolve(here, "../lattice_fea/ui/js/spacemouse.js"), "utf8");
const S = await import("data:text/javascript;base64,"
  + Buffer.from(src).toString("base64"));

let failures = 0;
function check(name, cond, detail = "") {
  if (cond) { console.log(`  ok   ${name}`); return; }
  failures++;
  console.log(`  FAIL ${name}${detail ? " — " + detail : ""}`);
}

/** A DataView over int16 little-endian values, as the device sends them. */
function report(...vals) {
  const b = new DataView(new ArrayBuffer(vals.length * 2));
  vals.forEach((v, i) => b.setInt16(i * 2, v, true));
  return b;
}

console.log("report layouts");
{
  const t = S.decodeReport(1, report(100, -200, 300));
  check("report 1 is translation", t.tx === 100 && t.ty === -200 && t.tz === 300,
        JSON.stringify(t));
  check("a 6-byte report 1 carries no rotation", t.rx === undefined);

  const r = S.decodeReport(2, report(-50, 60, -70));
  check("report 2 is rotation", r.rx === -50 && r.ry === 60 && r.rz === -70,
        JSON.stringify(r));

  // newer devices pack all six axes into report 1
  const both = S.decodeReport(1, report(1, 2, 3, 4, 5, 6));
  check("a 12-byte report 1 carries all six axes",
        both.tx === 1 && both.tz === 3 && both.rx === 4 && both.rz === 6,
        JSON.stringify(both));

  check("an unknown report id is ignored", S.decodeReport(9, report(1, 2, 3)) === null);
  // a truncated report must not throw: a dropped USB packet is not a crash
  check("a short report reads as zeros, not an exception",
        S.decodeReport(1, report(5)).ty === 0);
}

console.log("axis normalisation");
{
  const n = (raw) => S.normalizeAxis(raw);
  check("rest is exactly zero", n(0) === 0);
  check("full deflection is 1", Math.abs(n(350) - 1) < 1e-9);
  check("full negative deflection is -1", Math.abs(n(-350) + 1) < 1e-9);
  check("beyond full scale clamps", n(9999) === 1 && n(-9999) === -1);
  check("sign is preserved", n(-175) < 0);

  // The deadzone is what stops the model drifting while nobody is touching
  // the device: a SpaceMouse does not return to exactly zero at rest.
  check("inside the deadzone is silence", n(350 * S.DEADZONE * 0.9) === 0);
  check("just outside it is small, not a jump",
        n(350 * (S.DEADZONE + 0.01)) > 0 && n(350 * (S.DEADZONE + 0.01)) < 0.05,
        String(n(350 * (S.DEADZONE + 0.01))));
  // subtracting the deadzone rather than zeroing below it is what avoids the
  // step; check the response is continuous across the threshold
  const eps = 1e-4;
  const below = n(350 * (S.DEADZONE - eps)), above = n(350 * (S.DEADZONE + eps));
  check("the response is continuous at the deadzone edge",
        Math.abs(above - below) < 1e-3, `${below} -> ${above}`);
}

console.log("availability is reported, not assumed");
{
  // node 22 defines globalThis.navigator with a getter only
  const orig = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  const set = (v) => Object.defineProperty(globalThis, "navigator",
    { value: v, configurable: true, writable: true });
  set({});
  check("no WebHID is detected", S.available() === false);
  set({ hid: {} });
  check("WebHID is detected", S.available() === true);
  if (orig) Object.defineProperty(globalThis, "navigator", orig);
  else delete globalThis.navigator;
}

console.log("vendor ids");
{
  check("both 3Dconnexion vendor ids are accepted",
        S.VENDOR_IDS.includes(0x046d) && S.VENDOR_IDS.includes(0x256f));
}

console.log(failures ? `\n${failures} failure(s)` : "\nall passed");
process.exit(failures ? 1 : 0);
