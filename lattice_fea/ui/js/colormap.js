// Palettes. "rainbow" is the classic structural-FEA scale (blue→cyan→green→
// yellow→red) that Ansys/Nastran post-processors default to; "turbo" is the
// perceptually-uniform modern alternative.
const PALETTES = {
  rainbow: [
    [0.00, 0, 0, 220], [0.25, 0, 200, 235], [0.50, 20, 200, 60],
    [0.75, 245, 225, 40], [1.00, 210, 20, 20],
  ],
  turbo: [
    [0.00, 48, 18, 59], [0.13, 65, 69, 171], [0.26, 70, 117, 237],
    [0.39, 31, 201, 221], [0.52, 69, 241, 165], [0.65, 164, 252, 60],
    [0.78, 229, 214, 47], [0.89, 251, 128, 34], [1.00, 180, 12, 0],
  ],
};

// Display state, shared by the viewport shader and the legend so the two can
// never disagree about what a colour means.
export const contourStyle = { palette: "rainbow", bands: 9 };   // bands 0 = smooth

export function cmap(t, paletteName) {
  const STOPS = PALETTES[paletteName || contourStyle.palette] || PALETTES.rainbow;
  t = Math.min(1, Math.max(0, t));
  for (let i = 1; i < STOPS.length; i++) {
    if (t <= STOPS[i][0]) {
      const a = STOPS[i - 1], b = STOPS[i];
      const f = (t - a[0]) / (b[0] - a[0]);
      return [a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f, a[3] + (b[3] - a[3]) * f];
    }
  }
  const last = STOPS[STOPS.length - 1];
  return [last[1], last[2], last[3]];
}

/** Colour of band i of n — sampled at the band centre, matching the shader. */
export function bandColor(i, n) {
  return cmap(n > 0 ? (i + 0.5) / n : 0);
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

  const n = contourStyle.bands | 0;
  if (n > 0) {
    // Banded: one solid block per band, values printed at the boundaries —
    // the reading convention of a commercial post-processor legend.
    const rgb = (c) => `rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`;
    const stops = [];
    for (let i = n - 1; i >= 0; i--) {                 // top = max
      const lo = ((n - 1 - i) / n) * 100, hi = ((n - i) / n) * 100;
      stops.push(`${rgb(bandColor(i, n))} ${lo}% ${hi}%`);
    }
    strip.style.background = `linear-gradient(to bottom,${stops.join(",")})`;
    let html = "";
    for (let i = n; i >= 0; i--) {
      html += `<span>${fmtVal(min + (max - min) * (i / n))}</span>`;
    }
    ticks.innerHTML = html;
    ticks.style.fontSize = n > 12 ? "8.5px" : "10px";
  } else {
    const stops = [];
    for (let s = 0; s <= 32; s++) {
      const c = cmap(1 - s / 32);
      stops.push(`rgb(${c[0] | 0},${c[1] | 0},${c[2] | 0})`);
    }
    strip.style.background = `linear-gradient(to bottom,${stops.join(",")})`;
    let html = "";
    for (let i = 0; i <= 4; i++) {
      html += `<span>${fmtVal(max - (max - min) * (i / 4))}</span>`;
    }
    ticks.innerHTML = html;
    ticks.style.fontSize = "10px";
  }
}

export function fmtVal(v) {
  if (v === 0) return "0";
  const a = Math.abs(v);
  if (a >= 1e5 || a < 1e-3) return v.toExponential(2);
  if (a >= 100) return v.toFixed(1);
  if (a >= 1) return v.toFixed(2);
  return v.toFixed(4);
}
