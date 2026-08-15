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

/**
 * Locate resonant peaks and estimate Q by the half-power (-3 dB) bandwidth.
 * Q = f_n / (f2 - f1) where f1,f2 are the frequencies either side of the peak
 * at 1/sqrt(2) of peak amplitude. Q is the amplification at resonance, and
 * Q ~ 1/(2*zeta) — the number that carries into a random-vibration estimate.
 */
export function findPeaks(freq, mag, { minRatio = 0.08, maxPeaks = 12 } = {}) {
  const peaks = [];
  const gmax = Math.max(...mag);
  for (let i = 1; i < mag.length - 1; i++) {
    if (mag[i] <= mag[i - 1] || mag[i] < mag[i + 1]) continue;
    if (mag[i] < gmax * minRatio) continue;

    // parabolic refinement on the three samples around the peak
    const a = mag[i - 1], b = mag[i], c = mag[i + 1];
    const denom = a - 2 * b + c;
    const shift = denom !== 0 ? 0.5 * (a - c) / denom : 0;
    const lf = Math.log(freq[i]);
    const step = Math.log(freq[Math.min(i + 1, freq.length - 1)] / freq[Math.max(i - 1, 0)]) / 2;
    const fpk = Math.exp(lf + shift * step);

    // half-power points
    const half = b / Math.SQRT2;
    let lo = null, hi = null;
    for (let j = i; j > 0; j--) {
      if (mag[j] <= half) {
        const t = (half - mag[j]) / Math.max(mag[j + 1] - mag[j], 1e-30);
        lo = freq[j] + t * (freq[j + 1] - freq[j]);
        break;
      }
    }
    for (let j = i; j < mag.length - 1; j++) {
      if (mag[j] <= half) {
        const t = (half - mag[j]) / Math.max(mag[j - 1] - mag[j], 1e-30);
        hi = freq[j] + t * (freq[j - 1] - freq[j]);
        break;
      }
    }
    const bw = lo != null && hi != null ? Math.abs(hi - lo) : null;
    peaks.push({ f: fpk, amp: b, q: bw && bw > 0 ? fpk / bw : null });
  }
  peaks.sort((p, r) => r.amp - p.amp);
  return peaks.slice(0, maxPeaks).sort((p, r) => p.f - r.f);
}

/** Large FRF plot: log-log, peak markers, per-curve legend. */
export function frfPlot(canvas, curves, opts = {}) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 520, h = canvas.clientHeight || 300;
  canvas.width = w * dpr; canvas.height = h * dpr;
  const x = canvas.getContext("2d");
  x.setTransform(dpr, 0, 0, dpr, 0, 0);
  x.clearRect(0, 0, w, h);
  const cs = getComputedStyle(document.documentElement);
  const cLine = cs.getPropertyValue("--line").trim();
  const cDim = cs.getPropertyValue("--dim").trim();
  const cText = cs.getPropertyValue("--text").trim();

  const pts = curves.filter((c) => c.freq && c.freq.length > 1);
  if (!pts.length) return [];

  const L = 52, R = 12, T = 14, B = 34;                 // margins
  let fmin = Infinity, fmax = -Infinity, vmin = Infinity, vmax = -Infinity;
  for (const c of pts) {
    for (let i = 0; i < c.freq.length; i++) {
      const f = c.freq[i], v = c.module[i];
      if (f > 0) { fmin = Math.min(fmin, f); fmax = Math.max(fmax, f); }
      if (v > 0) { vmin = Math.min(vmin, v); vmax = Math.max(vmax, v); }
    }
  }
  if (!isFinite(fmin) || !isFinite(vmin)) return [];
  vmin = Math.max(vmin, vmax * 1e-5);                   // clamp the decade span
  const lf0 = Math.log10(fmin), lf1 = Math.log10(fmax);
  const lv0 = Math.log10(vmin), lv1 = Math.log10(vmax * 1.6);
  const X = (f) => L + ((Math.log10(f) - lf0) / (lf1 - lf0)) * (w - L - R);
  const Y = (v) => h - B - ((Math.log10(Math.max(v, vmin)) - lv0) / (lv1 - lv0)) * (h - T - B);

  // decade gridlines
  x.strokeStyle = cLine; x.lineWidth = 1;
  x.font = "10px ui-monospace, monospace"; x.fillStyle = cDim;
  for (let d = Math.floor(lf0); d <= Math.ceil(lf1); d++) {
    for (let m = 1; m < 10; m++) {
      const f = m * 10 ** d;
      if (f < fmin || f > fmax) continue;
      const px = X(f);
      x.globalAlpha = m === 1 ? 1 : 0.35;
      x.beginPath(); x.moveTo(px, T); x.lineTo(px, h - B); x.stroke();
      if (m === 1) {
        x.globalAlpha = 1; x.textAlign = "center";
        x.fillText(fmtVal(f), px, h - B + 13);
      }
    }
  }
  x.globalAlpha = 1;
  for (let d = Math.floor(lv0); d <= Math.ceil(lv1); d++) {
    const py = Y(10 ** d);
    if (py < T || py > h - B) continue;
    x.beginPath(); x.moveTo(L, py); x.lineTo(w - R, py); x.stroke();
    x.textAlign = "right";
    x.fillText(`1e${d}`, L - 5, py + 3);
  }
  x.textAlign = "center";
  x.fillText("Frequency (Hz)", (L + w - R) / 2, h - 4);

  // curves + peaks
  const allPeaks = [];
  pts.forEach((c, ci) => {
    const col = c.color || SERIES[ci % SERIES.length];
    x.beginPath();
    for (let i = 0; i < c.freq.length; i++) {
      const px = X(c.freq[i]), py = Y(c.module[i]);
      if (i === 0) x.moveTo(px, py); else x.lineTo(px, py);
    }
    x.strokeStyle = col; x.lineWidth = 1.6; x.stroke();

    if (opts.annotate !== false) {
      const pk = findPeaks(c.freq, c.module);
      for (const p of pk) {
        const px = X(p.f), py = Y(p.amp);
        x.beginPath(); x.arc(px, py, 3, 0, Math.PI * 2);
        x.fillStyle = col; x.fill();
        x.strokeStyle = cText; x.lineWidth = 0.8; x.stroke();
        allPeaks.push({ ...p, label: c.label, color: col });
      }
      // label the biggest few so the plot stays readable
      pk.slice().sort((a, b) => b.amp - a.amp).slice(0, 3).forEach((p) => {
        const px = X(p.f), py = Y(p.amp);
        const txt = p.q ? `${fmtVal(p.f)} Hz  Q≈${p.q.toFixed(1)}` : `${fmtVal(p.f)} Hz`;
        x.font = "10px ui-monospace, monospace";
        const tw = x.measureText(txt).width;
        const tx = Math.min(Math.max(px - tw / 2, L + 2), w - R - tw - 2);
        x.fillStyle = cs.getPropertyValue("--panel").trim();
        x.globalAlpha = 0.85;
        x.fillRect(tx - 3, py - 20, tw + 6, 13);
        x.globalAlpha = 1;
        x.fillStyle = cText;
        x.textAlign = "left";
        x.fillText(txt, tx, py - 10);
      });
    }
  });
  return allPeaks;
}
