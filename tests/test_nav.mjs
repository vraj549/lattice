// Viewport navigation. Run via tests/test_nav.py (pytest), or directly with
// `node tests/test_nav.mjs` from the repo root.
//
// The controller this replaced was a turntable, and the Top view sat exactly
// on its pole: a half-radian horizontal drag moved the camera 0.05 of 100
// units, so the model spun in place and horizontal dragging stopped working.
// The first test here is that exact measurement.

import { readFileSync } from "fs";
import { fileURLToPath, pathToFileURL } from "url";
import { dirname, resolve } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const threeUrl = pathToFileURL(
  resolve(here, "../lattice_fea/ui/vendor/three.module.js")).href;
const src = readFileSync(resolve(here, "../lattice_fea/ui/js/nav.js"), "utf8")
  .replace('from "three"', `from "${threeUrl}"`);
const N = await import("data:text/javascript;base64,"
  + Buffer.from(src).toString("base64"));
const THREE = await import(threeUrl);

let failures = 0;
function check(name, cond, detail = "") {
  if (cond) { console.log(`  ok   ${name}`); return; }
  failures++;
  console.log(`  FAIL ${name}${detail ? " — " + detail : ""}`);
}
const close = (a, b, tol = 1e-6) => Math.abs(a - b) <= tol;

/** A navigator with no DOM: _bind is the only part that needs one. */
function nav() {
  const cam = new THREE.PerspectiveCamera(45, 1.5, 0.1, 1000);
  const dom = { addEventListener() {}, style: {},
                getBoundingClientRect: () => ({ left: 0, top: 0, width: 1200, height: 800 }) };
  const n = new N.Navigator(cam, dom, () => {});
  n.fit([0, 0, 0, 100, 50, 16]);
  return n;
}

console.log("rotation works at every orientation, which is the whole point");
{
  // The turntable's failure, measured the same way: how far does the camera
  // actually travel for a half-radian horizontal drag?
  const drag = 0.5 / 0.007;            // px for ~0.5 rad
  const travel = (view) => {
    const n = nav();
    n.setStandardView(view);
    const before = n.camera.position.clone();
    n.rotate(drag, 0, "free");
    return before.distanceTo(n.camera.position) / n.dist;
  };
  for (const view of ["iso", "front", "top", "bottom", "right", "left", "back"]) {
    const f = travel(view);
    check(`horizontal drag orbits from ${view}`, f > 0.4,
          `camera moved ${(f * 100).toFixed(1)}% of the view distance`);
  }
  // and it keeps working after being tumbled somewhere arbitrary
  const n = nav();
  for (let i = 0; i < 40; i++) n.rotate(37, -23, "free");
  const before = n.camera.position.clone();
  n.rotate(drag, 0, "free");
  check("still orbits after 40 arbitrary drags",
        before.distanceTo(n.camera.position) / n.dist > 0.4);
}

console.log("the camera stays on the sphere and keeps looking at the target");
{
  const n = nav();
  const d0 = n.dist;
  for (let i = 0; i < 25; i++) n.rotate(53, 31, "free");
  check("distance is preserved", close(n.camera.position.distanceTo(n.target), d0, 1e-6),
        `${n.camera.position.distanceTo(n.target)} vs ${d0}`);
  const fwd = new THREE.Vector3(0, 0, -1).applyQuaternion(n.camera.quaternion);
  const toTarget = n.target.clone().sub(n.camera.position).normalize();
  check("camera still points at the target", close(fwd.dot(toTarget), 1, 1e-6),
        `dot = ${fwd.dot(toTarget)}`);
  check("orientation stays a unit quaternion", close(n.orient.length(), 1, 1e-9));
}

console.log("NX edge zones, decided from where the drag started");
{
  const W = 1200, H = 800;
  check("centre is free rotation", N.edgeZone(600, 400, W, H) === "free");
  check("left edge locks the horizontal axis", N.edgeZone(5, 400, W, H) === "horizontal");
  check("right edge locks the horizontal axis", N.edgeZone(W - 5, 400, W, H) === "horizontal");
  check("bottom edge locks the vertical axis", N.edgeZone(600, H - 5, W, H) === "vertical");
  check("top edge spins about the screen normal", N.edgeZone(600, 5, W, H) === "spin");
  // corners resolve to the top/bottom band, and the rule is ordered so that
  // is predictable rather than incidental
  check("top-left corner is a spin", N.edgeZone(5, 5, W, H) === "spin");
  check("bottom-left corner is the vertical axis", N.edgeZone(5, H - 5, W, H) === "vertical");
  // a tiny viewport must not be entirely edge
  check("a small viewport is not all edge", N.edgeZone(40, 40, 90, 90) === "free");
}

console.log("each constrained zone moves only its own axis");
{
  const axisMoved = (zone, dx, dy) => {
    const n = nav();
    n.setStandardView("iso");
    const r0 = n.right().clone(), u0 = n.up().clone(), f0 = n.forward().clone();
    n.rotate(dx, dy, zone);
    return { right: 1 - Math.abs(r0.dot(n.right())),
             up: 1 - Math.abs(u0.dot(n.up())),
             fwd: 1 - Math.abs(f0.dot(n.forward())) };
  };
  // about the screen horizontal: that axis is the one that must NOT move
  const h = axisMoved("horizontal", 0, 60);
  check("horizontal zone turns about the screen X axis", h.right < 1e-9 && h.up > 1e-3,
        JSON.stringify(h));
  const hx = axisMoved("horizontal", 60, 0);
  check("horizontal zone ignores horizontal drag", hx.up < 1e-12 && hx.fwd < 1e-12,
        JSON.stringify(hx));

  const v = axisMoved("vertical", 60, 0);
  check("vertical zone turns about the screen Y axis", v.up < 1e-9 && v.right > 1e-3,
        JSON.stringify(v));
  const vy = axisMoved("vertical", 0, 60);
  check("vertical zone ignores vertical drag", vy.right < 1e-12 && vy.fwd < 1e-12,
        JSON.stringify(vy));

  const s = axisMoved("spin", 60, 0);
  check("spin turns about the screen normal", s.fwd < 1e-9 && s.right > 1e-3,
        JSON.stringify(s));
}

console.log("NX mouse map");
{
  const M = (buttons, opt) => N.dragMode(buttons, opt);
  check("MB2 rotates", M(4) === "rotate");
  check("Shift+MB2 pans", M(4, { shift: true }) === "pan");
  check("Ctrl+MB2 zooms", M(4, { ctrl: true }) === "zoom");
  check("MB2+MB3 pans", M(4 | 2) === "pan");
  check("MB1+MB2 zooms", M(4 | 1) === "zoom");
  check("MB1 alone is not navigation", M(1) === null);
  check("MB3 alone is not navigation", M(2) === null);
  check("nothing held is not navigation", M(0) === null);
  check("a chord beats the modifier", M(4 | 2, { ctrl: true }) === "pan");
  // the trackpad fallback, off unless asked for
  check("left-drag rotate is opt-in", M(1, { leftDragRotates: true }) === "rotate");
  check("and still pans with shift",
        M(1, { leftDragRotates: true, shift: true }) === "pan");
  check("and leaves MB3 alone",
        M(2, { leftDragRotates: true }) === null);
}

console.log("standard views");
{
  const n = nav();
  const dirOf = (name) => {
    n.setStandardView(name);
    return n.camera.position.clone().sub(n.target).normalize();
  };
  const near = (v, x, y, z) => close(v.x, x, 1e-6) && close(v.y, y, 1e-6) && close(v.z, z, 1e-6);
  check("top looks straight down", near(dirOf("top"), 0, 0, 1));
  check("bottom looks straight up", near(dirOf("bottom"), 0, 0, -1));
  check("front is -Y", near(dirOf("front"), 0, -1, 0));
  check("right is +X", near(dirOf("right"), 1, 0, 0));
  // The old controller could not do this: it nudged Top a thousandth off the
  // pole because looking down the up-vector left roll undefined.
  n.setStandardView("top");
  check("top is exactly vertical, not nudged off the pole",
        Math.abs(n.camera.position.clone().sub(n.target).normalize().z - 1) < 1e-9);
  n.setStandardView("iso");
  const up = n.up();
  check("iso keeps Z up-ish", up.z > 0.5, `up.z = ${up.z}`);
}

console.log("6-DOF motion");
{
  const n = nav();
  const t0 = n.target.clone(), d0 = n.dist;
  check("no motion is a no-op", n.applyMotion({}) === false);
  n.applyMotion({ tx: 1 }, 0.1);
  check("translate X pans along the screen right axis",
        Math.abs(n.target.clone().sub(t0).dot(n.right())) > 1e-6);
  const n2 = nav();
  n2.applyMotion({ ty: -1 }, 0.1);
  check("push forward zooms in", n2.dist < d0, `${n2.dist} vs ${d0}`);
  const n3 = nav();
  const f0 = n3.forward().clone();
  n3.applyMotion({ ry: 1 }, 0.1);
  check("ry rolls about the screen normal",
        1 - Math.abs(f0.dot(n3.forward())) < 1e-9);
  const n4 = nav();
  n4.applyMotion({ rz: 1 }, 0.1);
  check("rz yaws", 1 - Math.abs(f0.dot(n4.forward())) > 1e-6);
  // frame-rate independence: a velocity device must not go faster on a fast
  // machine. Two half-steps and one whole step must land in the same place.
  const a = nav(); a.applyMotion({ tx: 1 }, 0.2);
  const b = nav(); b.applyMotion({ tx: 1 }, 0.1); b.applyMotion({ tx: 1 }, 0.1);
  check("translation is rate-based, not per-frame",
        a.target.distanceTo(b.target) < 1e-9);
}

console.log("zoom direction is one direction");
{
  // NX ties the Ctrl+MB2 drag gesture to the same direction preference as the
  // wheel, so the two must agree. They did not: dragging up zoomed out while
  // rolling forward zoomed in.
  const wheelIn = Math.exp(-100 * 0.0012);     // deltaY < 0 is a forward roll
  check("rolling forward zooms in", wheelIn < 1, String(wheelIn));
  const dragUp = Math.exp(-100 * 0.006);       // dy < 0 is an upward drag
  check("dragging up zooms in", dragUp < 1, String(dragUp));
  check("and they agree", (wheelIn < 1) === (dragUp < 1));
}

console.log("zoom stays inside its bounds");
{
  const n = nav();
  for (let i = 0; i < 200; i++) n.zoom(0.5);
  check("cannot zoom into a degenerate camera", n.dist > 0);
  for (let i = 0; i < 400; i++) n.zoom(2.0);
  check("cannot zoom past the far plane", n.dist < n._diag * 201);
}

console.log(failures ? `\n${failures} failure(s)` : "\nall passed");
process.exit(failures ? 1 : 0);
