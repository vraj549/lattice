// Base-excitation arithmetic shared by the result panels.
//
// Kept free of the DOM so the tests can load it under node and hold it to
// the server's copy in random_vib.py; the two must give the same curve.

const G_MM = 9810;   // 1 g in mm/s²

/**
 * Absolute-acceleration transmissibility from a RELATIVE displacement FRF,
 * T(f) = |1 + a_rel(f) / a_base|, with a_rel = -(2πf)² u_rel. The phase is
 * kept: near resonance the two terms are far from in phase.
 */
export function transmissibility(freq, module, phase, baseG = 1) {
  const aBase = baseG * G_MM;
  if (!aBase) return freq.map(() => 0);
  return freq.map((f, i) => {
    const w = 2 * Math.PI * f;
    const mag = module[i] * w * w;
    const ph = phase?.[i] ?? 0;
    return Math.hypot(1 - (mag * Math.cos(ph)) / aBase, (-mag * Math.sin(ph)) / aBase);
  });
}
