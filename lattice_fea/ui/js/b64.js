// base64 typed-array transport (matches lattice_fea _b64())
export function decode(obj) {
  if (!obj) return null;
  const bin = atob(obj.b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  switch (obj.dtype) {
    case "float32": return new Float32Array(bytes.buffer);
    case "float64": return new Float64Array(bytes.buffer);
    case "uint32":  return new Uint32Array(bytes.buffer);
    case "int32":   return new Int32Array(bytes.buffer);
    case "int64": { // downcast — indices fit in 32 bits for our meshes
      const n = bytes.length / 8, out = new Uint32Array(n);
      const dv = new DataView(bytes.buffer);
      for (let i = 0; i < n; i++) out[i] = Number(dv.getBigInt64(i * 8, true));
      return out;
    }
    default: throw new Error(`unsupported dtype ${obj.dtype}`);
  }
}
