import { fmtVal } from "./colormap.js";

const SERIES = ["#e89344", "#8d7dec", "#4fbe8b", "#e0b93c", "#e5674c", "#7fb2d8"];

// FRF chart: log-x, log-y magnitude, multiple curves, hover readout.
export function frfChart(canvas, curves, { logx = true } = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 260, h = canvas.clientHeight || 130;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const x = canvas.getContext("2d");
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, w, h);
  const cs = getComputedStyle(document.documentElement);
  const cLine = cs.getPropertyValue("--line").trim();
  const cFaint = cs.getPropertyValue("--faint").trim();

  const pts = curves.filter((c) => c.freq && c.freq.length > 1);
  if (!pts.length) return;

  let fmin = Infinity, fmax = -Infinity, vmin = Infinity, vmax = -Infinity;
  for (const c of pts) {
    for (let i = 0; i < c.freq.length; i++) {
      const f = c.freq[i], v = c.module[i];
      if (f > 0) { fmin = Math.min(fmin, f); fmax = Math.max(fmax, f); }
      if (v > 0) { vmin = Math.min(vmin, v); vmax = Math.max(vmax, v); }
    }
  }
  if (!isFinite(fmin) || !isFinite(vmin)) return;
  const lf0 = Math.log10(fmin), lf1 = Math.log10(fmax);
  const lv0 = Math.log10(vmin), lv1 = Math.log10(vmax);
  const padB = 16, padT = 6;
  const X = (f) => (logx ? (Math.log10(f) - lf0) / (lf1 - lf0) : (f - fmin) / (fmax - fmin)) * w;
  const Y = (v) => h - padB - ((Math.log10(Math.max(v, vmin)) - lv0) / Math.max(lv1 - lv0, 1e-9)) * (h - padB - padT);

  x.strokeStyle = cLine; x.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const gy = padT + (h - padB - padT) * (i / 3);
    x.beginPath(); x.moveTo(0, gy + 0.5); x.lineTo(w, gy + 0.5); x.stroke();
  }

  pts.forEach((c, ci) => {
    const col = c.color || SERIES[ci % SERIES.length];
    x.beginPath();
    for (let i = 0; i < c.freq.length; i++) {
      const px = X(c.freq[i]), py = Y(c.module[i]);
      if (i === 0) x.moveTo(px, py); else x.lineTo(px, py);
    }
    x.strokeStyle = col; x.lineWidth = 1.5; x.stroke();
  });

  x.fillStyle = cFaint;
  x.font = "10px ui-monospace, monospace";
  x.textAlign = "left"; x.fillText(fmtVal(fmin) + " Hz", 0, h - 4);
  x.textAlign = "right"; x.fillText(fmtVal(fmax) + " Hz", w, h - 4);

  // hover crosshair
  canvas.onmousemove = (e) => {
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left;
    const f = logx ? Math.pow(10, lf0 + (mx / w) * (lf1 - lf0)) : fmin + (mx / w) * (fmax - fmin);
    let txt = `${fmtVal(f)} Hz`;
    for (const c of pts) {
      let best = 0, bd = Infinity;
      for (let i = 0; i < c.freq.length; i++) {
        const d = Math.abs(c.freq[i] - f);
        if (d < bd) { bd = d; best = i; }
      }
      txt += `  ${c.label}: ${fmtVal(c.module[best])}`;
    }
    canvas.title = txt;
  };
}

export function seriesColor(i) { return SERIES[i % SERIES.length]; }
