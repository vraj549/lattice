// Turbo colormap — perceptually decent, standard for FEA contours
const STOPS = [
  [0.00, 48, 18, 59], [0.13, 65, 69, 171], [0.26, 70, 117, 237],
  [0.39, 31, 201, 221], [0.52, 69, 241, 165], [0.65, 164, 252, 60],
  [0.78, 229, 214, 47], [0.89, 251, 128, 34], [1.00, 180, 12, 0],
];

export function cmap(t) {
  t = Math.min(1, Math.max(0, t));
  for (let i = 1; i < STOPS.length; i++) {
    if (t <= STOPS[i][0]) {
      const a = STOPS[i - 1], b = STOPS[i];
      const f = (t - a[0]) / (b[0] - a[0]);
      return [a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f];
    }
  }
  return [180, 12, 0];
}

export function makeTexture(THREE) {
  const n = 256, data = new Uint8Array(n * 4);
  for (let i = 0; i < n; i++) {
    const c = cmap(i / (n - 1));
    data[i * 4] = c[0]; data[i * 4 + 1] = c[1]; data[i * 4 + 2] = c[2]; data[i * 4 + 3] = 255;
  }
  const tex = new THREE.DataTexture(data, n, 1, THREE.RGBAFormat);
  tex.needsUpdate = true;
  tex.minFilter = tex.magFilter = THREE.LinearFilter;
  return tex;
}

export function renderLegend(min, max, caption) {
  const box = document.getElementById("legendBox");
  const strip = document.getElementById("legendStrip");
  const ticks = document.getElementById("legendTicks");
  const cap = document.getElementById("legendCap");
  if (min == null) { box.hidden = true; return; }
  box.hidden = false;
  cap.textContent = caption || "";
  const stops = [];
  for (let s = 0; s <= 10; s++) {
    const c = cmap(1 - s / 10);
    stops.push(`rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`);
  }
  strip.style.background = `linear-gradient(to bottom,${stops.join(",")})`;
  let html = "";
  for (let i = 0; i <= 4; i++) {
    const v = max - (max - min) * (i / 4);
    html += `<span>${fmtVal(v)}</span>`;
  }
  ticks.innerHTML = html;
}

export function fmtVal(v) {
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e5 || a < 1e-3) return v.toExponential(2);
  if (a >= 100) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(4);
}
