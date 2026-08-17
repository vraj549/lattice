import * as THREE from "three";

// Corner orientation triad. RGB = XYZ, the CAD convention.
// Drawn as a second scene into a scissored corner viewport so it always sits
// on top and never scales with the model.
// Slightly desaturated so the triad reads on both light and dark grounds
// without shouting louder than the result contours.
const AXIS = [
  { dir: [1, 0, 0], color: 0xcc3b32, label: "X" },
  { dir: [0, 1, 0], color: 0x2f9e4f, label: "Y" },
  { dir: [0, 0, 1], color: 0x2f6fc4, label: "Z" },
];

function labelSprite(text, color) {
  const S = 64;
  const c = document.createElement("canvas");
  c.width = c.height = S;
  const g = c.getContext("2d");
  g.font = "bold 40px ui-monospace, monospace";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillStyle = "#" + color.toString(16).padStart(6, "0");
  g.fillText(text, S / 2, S / 2);
  const tex = new THREE.CanvasTexture(c);
  tex.needsUpdate = true;
  const sp = new THREE.Sprite(new THREE.SpriteMaterial({
    map: tex, depthTest: false, transparent: true }));
  sp.scale.set(0.55, 0.55, 0.55);
  return sp;
}

export class AxisTriad {
  constructor(size = 78) {
    this.size = size;
    this.scene = new THREE.Scene();
    this.camera = new THREE.OrthographicCamera(-1.7, 1.7, 1.7, -1.7, 0.01, 20);

    const group = new THREE.Group();

    // A shaded cube at the origin. Three bare arrows meeting at a point are
    // ambiguous from a lot of viewpoints — which one is coming at you and
    // which is going away reads only from the labels. A solid body with
    // visibly lit faces settles the orientation at a glance, the way a CAD
    // view cube does.
    const cube = new THREE.Mesh(
      new THREE.BoxGeometry(0.62, 0.62, 0.62),
      new THREE.MeshLambertMaterial({ color: 0x9fb0bd, transparent: true, opacity: 0.92 }));
    group.add(cube);
    group.add(new THREE.LineSegments(
      new THREE.EdgesGeometry(cube.geometry),
      new THREE.LineBasicMaterial({ color: 0x3d4a55, transparent: true, opacity: 0.75 })));
    const lamp = new THREE.DirectionalLight(0xffffff, 0.85);
    lamp.position.set(1.4, 1.0, 1.8);
    this.scene.add(lamp);
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.55));

    for (const a of AXIS) {
      const dir = new THREE.Vector3(...a.dir);
      const shaft = new THREE.Mesh(
        new THREE.CylinderGeometry(0.055, 0.055, 0.69, 10),
        new THREE.MeshBasicMaterial({ color: a.color, depthTest: false }));
      // cylinders are built along +Y; rotate onto the axis direction
      shaft.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir);
      shaft.position.copy(dir).multiplyScalar(0.31 + 0.345);

      const head = new THREE.Mesh(
        new THREE.ConeGeometry(0.15, 0.34, 12),
        new THREE.MeshBasicMaterial({ color: a.color, depthTest: false }));
      head.quaternion.copy(shaft.quaternion);
      head.position.copy(dir).multiplyScalar(1.13);

      const lab = labelSprite(a.label, a.color);
      lab.position.copy(dir).multiplyScalar(1.55);

      group.add(shaft, head, lab);
    }
    const origin = new THREE.Mesh(
      new THREE.SphereGeometry(0.1, 12, 10),
      new THREE.MeshBasicMaterial({ color: 0x8593a0, depthTest: false }));
    group.add(origin);

    this.scene.add(group);
  }

  /** Render into the bottom-right corner, matching the main camera's view
   *  direction so the triad turns as the model turns. */
  render(renderer, mainCamera, target) {
    const s = this.size;
    const el = renderer.domElement;
    const w = el.clientWidth, h = el.clientHeight;
    if (w < s * 1.5 || h < s * 1.5) return;    // too small to be useful

    const offset = new THREE.Vector3()
      .subVectors(mainCamera.position, target).normalize().multiplyScalar(5);
    this.camera.position.copy(offset);
    this.camera.up.copy(mainCamera.up);
    this.camera.lookAt(0, 0, 0);
    this.camera.updateProjectionMatrix();

    const pad = 8;
    renderer.setViewport(w - s - pad, pad, s, s);
    renderer.setScissor(w - s - pad, pad, s, s);
    renderer.setScissorTest(true);
    renderer.clearDepth();          // depth only — the viewport shows through
    renderer.render(this.scene, this.camera);
    renderer.setScissorTest(false);
    renderer.setViewport(0, 0, w, h);
  }
}
