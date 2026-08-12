"""Post-run result packaging: MED fields -> viewer JSON, table/FRF parsing."""
from __future__ import annotations

import base64
import glob
import os
import re

import numpy as np

from .med_reader import MedFile


def _b64(arr) -> dict:
    a = np.ascontiguousarray(arr)
    return {"dtype": str(a.dtype), "shape": list(a.shape),
            "b64": base64.b64encode(a.tobytes()).decode("ascii")}


# Friendly labels for aster field-name suffixes
FIELD_LABELS = [
    ("DEPL", "Displacement"),
    ("SIEQ_NOEU", "Equivalent stress"),
    ("SIGM_NOEU", "Stress tensor"),
]
COMP_LABELS = {
    "DX": "UX", "DY": "UY", "DZ": "UZ",
    "VMIS": "von Mises", "VMIS_SG": "signed von Mises", "TRESCA": "Tresca",
    "PRIN_1": "min principal", "PRIN_2": "mid principal", "PRIN_3": "max principal",
    "SIXX": "σxx", "SIYY": "σyy", "SIZZ": "σzz",
    "SIXY": "σxy", "SIXZ": "σxz", "SIYZ": "σyz",
}


def classify_field(raw: str) -> "tuple[str, str, str]":
    """aster MED field names look like '<result8><CHAM>'; classify by suffix.
    Returns (kind, label, part) where part is 'R'/'I' for complex halves."""
    up = raw.upper()
    part = ""
    if re.search(r"[_.]R$", up) or up.endswith("REEL"):
        part = "R"
    if re.search(r"[_.]I$", up) or up.endswith("IMAG"):
        part = "I"
    for suffix, label in FIELD_LABELS:
        if suffix in up:
            return suffix, label, part
    return raw, raw, part


def build_results(run_dir: str, expect_bbox=None, expect_volume=None) -> dict:
    """Parse everything in a finished run directory into meta.json-able data.
    Field values themselves stay in the MED; served on demand."""
    meta = {"fields": [], "tables": {}, "frf": [], "warnings": []}

    med_path = os.path.join(run_dir, "result.med")
    if os.path.isfile(med_path):
        try:
            med = MedFile(med_path, expect_bbox, expect_volume)
            raw_fields = med.list_fields()
            med.close()
            for f in raw_fields:
                kind, label, part = classify_field(f["name"])
                meta["fields"].append({
                    "name": f["name"], "kind": kind, "label": label, "part": part,
                    "comps": f["comps"], "steps": f["steps"]})
        except Exception as e:  # noqa: BLE001
            meta["warnings"].append(f"MED parse failed: {e}")

    for fname, key in [("modes.csv", "modes"), ("participation.csv", "participation"),
                       ("tables.txt", "tables"), ("bolt_forces.csv", "bolt_forces")]:
        p = os.path.join(run_dir, fname)
        if os.path.isfile(p):
            try:
                meta["tables"][key] = parse_tableau(open(p, errors="replace").read())
            except Exception as e:  # noqa: BLE001
                meta["warnings"].append(f"{fname}: {e}")

    for p in sorted(glob.glob(os.path.join(run_dir, "frf_p*_*.csv"))):
        m = re.match(r"frf_p(\d+)_(\w+)\.csv$", os.path.basename(p))
        if not m:
            continue
        try:
            curves = parse_fonction(open(p, errors="replace").read())
            meta["frf"].append({"probe": int(m.group(1)), "comp": m.group(2).upper(),
                                **curves})
        except Exception as e:  # noqa: BLE001
            meta["warnings"].append(f"{os.path.basename(p)}: {e}")

    return meta


def field_payload(run_dir: str, field: str, step_key: str, comp: str,
                  expect_bbox=None, expect_volume=None) -> dict:
    """Skin mesh + scalar values (+ displacement vectors when available)."""
    med = MedFile(os.path.join(run_dir, "result.med"), expect_bbox, expect_volume)
    try:
        tri, used, _ = med.skin()
        vtx = med.nodes[used].astype(np.float32)

        fields = {f["name"]: f for f in med.list_fields()}
        f = fields.get(field)
        if f is None:
            raise ValueError(f"field {field} not in MED")
        vals = med.read_field(field, step_key)

        comps = f["comps"]
        if comp == "MAG":
            take = [i for i, c in enumerate(comps) if c in ("DX", "DY", "DZ")][:3]
            scal = np.linalg.norm(vals[:, take], axis=1) if take else np.abs(vals[:, 0])
        elif comp in comps:
            scal = vals[:, comps.index(comp)]
        else:
            scal = vals[:, 0]
        scal = scal[used].astype(np.float32)

        # displacement vectors for deformation: same field if DEPL, else look
        # for a DEPL field sharing the step key
        disp = None
        dep_name = field if "DEPL" in field.upper() else None
        if dep_name is None:
            for name, ff in fields.items():
                if "DEPL" in name.upper() and any(s["key"] == step_key for s in ff["steps"]):
                    dep_name = name
                    break
        if dep_name:
            dv = med.read_field(dep_name, step_key)
            dcomps = fields[dep_name]["comps"]
            take = [dcomps.index(c) for c in ("DX", "DY", "DZ") if c in dcomps]
            if len(take) == 3:
                disp = dv[:, take][used].astype(np.float32)

        out = {
            "vtx": _b64(vtx.ravel()), "tri": _b64(tri.ravel()),
            "values": _b64(scal), "min": float(scal.min()), "max": float(scal.max()),
        }
        if disp is not None:
            out["disp"] = _b64(disp.ravel())
            out["disp_max"] = float(np.linalg.norm(disp, axis=1).max())
        return out
    finally:
        med.close()


# --------------------------------------------------------------------------
# text parsers
# --------------------------------------------------------------------------

def parse_tableau(text: str) -> list:
    """Parse IMPR_TABLE FORMAT='TABLEAU' SEPARATEUR=',' output.
    Returns a list of {columns, rows} blocks (a unit can hold several tables)."""
    blocks, cur_cols, cur_rows = [], None, []

    def flush():
        nonlocal cur_cols, cur_rows
        if cur_cols and cur_rows:
            blocks.append({"columns": cur_cols, "rows": cur_rows})
        cur_cols, cur_rows = None, []

    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            if not line:
                flush()
            continue
        if "," not in line:
            flush()
            continue
        cells = [c.strip() for c in line.split(",")]
        parsed, n_numeric = [], 0
        for c in cells:
            if c in ("", "-"):
                parsed.append(None)
                continue
            try:
                parsed.append(float(c))
                n_numeric += 1
            except ValueError:
                parsed.append(c)  # string cell (e.g. INTITULE) stays as-is
        # data row = at least one numeric cell under known columns;
        # an all-string row starts a new block header
        if n_numeric > 0 and cur_cols:
            cur_rows.append(parsed)
        else:
            flush()
            cur_cols = cells
    flush()
    return blocks


def parse_fonction(text: str) -> dict:
    """Parse IMPR_FONCTION TABLEAU output with two curves (module, phase).
    Layout in practice: header block(s) then rows 'freq,value'; the two curves
    are printed one after the other over the same abscissa."""
    xs, ys = [], []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cells = [c.strip() for c in line.split(",")]
        try:
            row = [float(c) for c in cells if c != ""]
        except ValueError:
            continue
        if len(row) >= 2:
            xs.append(row[0])
            ys.append(row[1:])
    if not xs:
        raise ValueError("no numeric rows")
    xs = np.asarray(xs)
    ys = np.asarray([r + [None] * (max(len(r) for r in ys) - len(r)) for r in ys],
                    dtype=float) if ys else np.zeros((0, 1))

    # If the file contains the two curves stacked (freq restarts), split them.
    if len(xs) >= 4:
        restarts = np.where(np.diff(xs) < 0)[0]
        if restarts.size == 1 and ys.shape[1] == 1:
            k = restarts[0] + 1
            return {"freq": xs[:k].tolist(),
                    "module": ys[:k, 0].tolist(),
                    "phase": ys[k:, 0].tolist()}
    if ys.shape[1] >= 2:
        return {"freq": xs.tolist(), "module": ys[:, 0].tolist(),
                "phase": ys[:, 1].tolist()}
    return {"freq": xs.tolist(), "module": ys[:, 0].tolist(), "phase": []}
