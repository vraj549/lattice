"""Post-run result packaging: MED fields -> viewer JSON, table/FRF parsing."""
from __future__ import annotations

import base64
import math
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
                meta["tables"][key] = parse_tableau(open(p, encoding="utf-8", errors="replace").read())
            except Exception as e:  # noqa: BLE001
                meta["warnings"].append(f"{fname}: {e}")

    for p in sorted(glob.glob(os.path.join(run_dir, "frf_p*_*.csv"))):
        m = re.match(r"frf_p(\d+)_(\w+)\.csv$", os.path.basename(p))
        if not m:
            continue
        try:
            curves = parse_fonction(open(p, encoding="utf-8", errors="replace").read())
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
        tri, used, _, edges = med.skin()
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
            "edges": _b64(edges.ravel()),
            "values": _b64(scal), "min": float(scal.min()), "max": float(scal.max()),
        }
        if disp is not None:
            out["disp"] = _b64(disp.ravel())
            out["disp_max"] = float(np.linalg.norm(disp, axis=1).max())
        return out
    finally:
        med.close()


def field_csv_rows(run_dir: str, field: str, step_key: str,
                   expect_bbox=None, expect_volume=None):
    """Yield CSV lines of every nodal value for one field/step.

    Streamed a line at a time rather than built as one string: a 300k-node
    stress field is ~30 MB of text, and the point of an export is that it
    works on the model you actually ran, not a small one.
    """
    med = MedFile(os.path.join(run_dir, "result.med"), expect_bbox, expect_volume)
    try:
        fields = {f["name"]: f for f in med.list_fields()}
        f = fields.get(field)
        if f is None:
            raise ValueError(f"field {field} not in MED")
        vals = med.read_field(field, step_key)
        comps = [c or f"C{i + 1}" for i, c in enumerate(f["comps"])]
        nodes = med.nodes
        yield ",".join(["node", "X_mm", "Y_mm", "Z_mm", *comps]) + "\n"
        for i in range(len(nodes)):
            x, y, z = nodes[i]
            row = ",".join(f"{v:.9g}" for v in vals[i])
            yield f"{i + 1},{x:.9g},{y:.9g},{z:.9g},{row}\n"
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


# --------------------------------------------------------------------------
# CalculiX
# --------------------------------------------------------------------------

def build_results_ccx(run_dir: str, jobname: str = "job",
                      applied=None, support_frames=None,
                      model_diag=None) -> dict:
    """Package a CalculiX run the same way build_results does for code_aster.

    Presenting both solvers through one shape is what lets the whole results
    UI — contours, probes, exports — work regardless of which one ran.
    """
    from .frd_reader import FrdFile

    frd = os.path.join(run_dir, f"{jobname}.frd")
    meta = {"fields": [], "tables": {}, "frf": [], "warnings": [], "engine": "ccx"}
    if not os.path.isfile(frd):
        meta["warnings"].append("CalculiX wrote no .frd file — see the log.")
        return meta

    f = FrdFile(frd)
    if not len(f.nodes):
        meta["warnings"].append("The .frd file contains no nodes.")
        return meta

    disp_blocks = [b for b in f.blocks if b["name"].upper().startswith("DISP")]
    stress_blocks = [b for b in f.blocks if b["name"].upper().startswith("STRESS")]

    if disp_blocks:
        meta["fields"].append({
            "name": "DISP", "label": "Displacement", "kind": "DEPL", "part": "",
            "comps": ["DX", "DY", "DZ"],
            "steps": [{"key": str(i), "ndt": i + 1, "value": b["step"]}
                      for i, b in enumerate(disp_blocks)],
        })
    if stress_blocks:
        steps = [{"key": str(i), "ndt": i + 1, "value": b["step"]}
                 for i, b in enumerate(stress_blocks)]
        # Two entries from one block, so the field/component menus offer the
        # same choices they do for code_aster. Equivalent stresses are derived
        # from the tensor when the field is read; CalculiX writes only the six
        # components, but a results panel that omits von Mises is not the same
        # tool depending on which solver ran.
        meta["fields"].append({
            "name": "SIEQ", "label": "Equivalent stress", "kind": "SIEQ",
            "part": "", "source": "STRESS",
            "comps": ["VMIS", "TRESCA", "PRIN_1", "PRIN_2", "PRIN_3"],
            "steps": steps,
        })
        meta["fields"].append({
            "name": "STRESS", "label": "Stress tensor", "kind": "SIGM", "part": "",
            "comps": [c or f"S{i}" for i, c in enumerate(stress_blocks[0]["comps"])],
            "steps": steps,
        })

    # ---- equilibrium check ----
    #
    # A static solution must balance: the reactions at the supports have to
    # add up to minus the applied load. It is cheap, needs no reference
    # solution, and catches the one failure mode that has no other symptom —
    # a factorization that quietly returns a corrupted answer and exits 0.
    #
    # Summed over the SUPPORTED nodes specifically. CalculiX's FORC field is
    # the total nodal force, external included, so over the whole model it is
    # zero by construction and would prove nothing.
    if applied is not None and support_frames and any(abs(v) > 0 for v in applied):
        forc = next((b for b in f.blocks if b["name"].upper().startswith("FORC")), None)
        if forc and forc["values"]:
            react = [0.0, 0.0, 0.0]
            counted = set()
            for grp in support_frames:
                frame = grp.get("frame")
                for tag in grp["nodes"]:
                    if int(tag) in counted:      # a node on a shared edge
                        continue                 # belongs to one sum only
                    counted.add(int(tag))
                    vals = forc["values"].get(int(tag))
                    if not vals:
                        continue
                    v = [vals[k] if k < len(vals) else 0.0 for k in range(3)]
                    if frame:
                        # local -> global: sum of each local component along
                        # its own axis expressed in global coordinates
                        v = [sum(v[a] * frame[a][k] for a in range(3))
                             for k in range(3)]
                    for k in range(3):
                        react[k] += v[k]
            scale = max(abs(v) for v in applied) or 1.0
            resid = [react[k] + applied[k] for k in range(3)]
            rel = max(abs(r) for r in resid) / scale
            meta["equilibrium"] = {"applied": applied, "reaction": react,
                                   "residual_rel": rel}
            if rel > 0.02:
                meta["warnings"].append(
                    f"EQUILIBRIUM CHECK FAILED: support reactions do not balance "
                    f"the applied load ({rel * 100:.1f}% residual). Applied "
                    f"{_vec(applied)} N, reactions {_vec(react)} N. Do not use "
                    f"these results. The usual cause is a multithreaded "
                    f"factorization returning a corrupted solution — set "
                    f"LATTICE_CCX_THREADS=1 and run again.")

    # A second, independent check: a node held by a fixed support must not
    # have moved. Equilibrium alone does not catch everything — a corrupted
    # solve can add a rigid-body component, which balances perfectly and is
    # still wrong. Between them these cover the cases seen in testing, but
    # neither is a proof: the real defence is running the solver in a
    # configuration that does not corrupt solutions in the first place.
    # Only genuinely FIXED nodes. A frictionless support slides in-plane by
    # definition and a prescribed displacement moves on purpose; including
    # either would make this fire on correct models.
    support_nodes = [n for g in (support_frames or [])
                     if g.get("type", "fixed") == "fixed" for n in g["nodes"]]
    if support_nodes and disp_blocks:
        comps, u = f.field("DISP") or ([], None)
        if u is not None and len(u):
            want = {int(n) for n in support_nodes}
            idx = [f.tag_index[t] for t in want if t in f.tag_index]
            if idx:
                held = float(np.nanmax(np.abs(u[idx])))
                moved = float(np.nanmax(np.abs(u)))
                meta["fixed_dof_drift"] = held
                if moved > 0 and held > 1e-3 * moved:
                    meta["warnings"].append(
                        f"FIXED SUPPORTS MOVED: a node that is held to zero "
                        f"displaced {held:.3g} mm, against {moved:.3g} mm peak "
                        f"in the model. The solution does not satisfy its own "
                        f"boundary conditions. Do not use these results; set "
                        f"LATTICE_CCX_THREADS=1 and run again.")

    # Linear small-displacement theory quietly stops applying long before it
    # stops producing numbers. A load ten times too large returns a deflection
    # ten times too large and looks entirely normal, so the scale of the
    # answer is compared against the size of the part.
    if model_diag and disp_blocks:
        got = f.field("DISP")
        if got and got[1] is not None and len(got[1]):
            peak = float(np.nanmax(np.abs(got[1])))
            meta["peak_disp"] = peak
            if peak > 0.1 * model_diag:
                meta["warnings"].append(
                    f"Peak displacement is {peak:.4g} mm on a model {model_diag:.4g} mm "
                    f"across ({100 * peak / model_diag:.0f} % of its size). This is a "
                    f"LINEAR small-displacement analysis: it assumes the shape barely "
                    f"changes, so at this magnitude the result is arithmetic, not "
                    f"physics. Check the load magnitude and units.")

    eq = meta.get("equilibrium")
    if eq:
        # the same block shape build_results() produces, so the Reactions
        # panel and the CSV export do not care which solver ran
        meta["tables"]["tables"] = [{
            "columns": ["INTITULE", "RESU", "NOM_CHAM", "DX", "DY", "DZ"],
            "rows": [["REACTIONS", 1.0, "REAC_NODA", *eq["reaction"]]],
        }]

    freqs = [b["step"] for b in disp_blocks if b["step"]]
    if len(freqs) > 1:
        meta["tables"]["modes"] = [{
            "columns": ["NUME_MODE", "FREQ"],
            "rows": [[i + 1, fr] for i, fr in enumerate(freqs)],
        }]
    return meta


def stress_invariants(tensor: "np.ndarray", comp: str) -> "np.ndarray":
    """Equivalent stresses from the six Cauchy components (xx,yy,zz,xy,yz,zx).

    CalculiX reports the tensor only. Deriving these here keeps one definition
    of von Mises in the project rather than one per solver.
    """
    sxx, syy, szz, sxy, syz, szx = (tensor[:, i] for i in range(6))
    if comp == "VMIS":
        return np.sqrt(0.5 * ((sxx - syy) ** 2 + (syy - szz) ** 2 + (szz - sxx) ** 2)
                       + 3.0 * (sxy ** 2 + syz ** 2 + szx ** 2))
    n = len(sxx)
    m = np.empty((n, 3, 3))
    m[:, 0, 0], m[:, 1, 1], m[:, 2, 2] = sxx, syy, szz
    m[:, 0, 1] = m[:, 1, 0] = sxy
    m[:, 1, 2] = m[:, 2, 1] = syz
    m[:, 0, 2] = m[:, 2, 0] = szx
    ev = np.linalg.eigvalsh(np.nan_to_num(m))      # ascending
    if comp == "TRESCA":
        return ev[:, 2] - ev[:, 0]
    return ev[:, {"PRIN_1": 0, "PRIN_2": 1, "PRIN_3": 2}.get(comp, 2)]


def field_payload_ccx(run_dir: str, field: str, step_key: str, comp: str,
                      jobname: str = "job") -> dict:
    """Skin + scalar values for a CalculiX result, matching field_payload()."""
    from .frd_reader import FrdFile

    f = FrdFile(os.path.join(run_dir, f"{jobname}.frd"))
    src = "STRESS" if field in ("SIEQ", "STRESS") else "DISP"
    got = f.field(src, int(step_key or 0))
    if got is None:
        raise ValueError(f"field {field} not in the CalculiX results")
    comps, arr = got

    if field == "SIEQ":
        scal = stress_invariants(arr, comp)
    elif field == "STRESS":
        scal = arr[:, comps.index(comp)] if comp in comps else arr[:, 0]
    elif comp == "MAG":
        scal = np.linalg.norm(arr, axis=1)
    else:
        idx = {"DX": 0, "DY": 1, "DZ": 2}.get(comp, 0)
        scal = arr[:, idx]

    dgot = f.field("DISP", int(step_key or 0))
    disp = dgot[1] if dgot else None
    tri, used = f.skin()
    vtx = f.nodes[used].astype(np.float32)
    scal = np.nan_to_num(scal[used]).astype(np.float32)
    out = {"vtx": _b64(vtx.ravel()), "tri": _b64(tri.ravel()),
           "values": _b64(scal), "min": float(scal.min()), "max": float(scal.max())}
    if disp is not None:
        d = np.nan_to_num(disp[used]).astype(np.float32)
        out["disp"] = _b64(d.ravel())
        out["disp_max"] = float(np.linalg.norm(d, axis=1).max())
    return out


def _vec(v) -> str:
    return "(" + ", ".join(f"{x:.4g}" for x in v) + ")"


def bolt_loads(meta: dict) -> dict:
    """{bolt index: {N, V, M}} — worst end of each bolt.

    Both beam ends are reported; the governing one is the larger axial force,
    which is the end that decides the joint.
    """
    out = {}
    for blk in (meta.get("tables") or {}).get("bolt_forces", []) or []:
        cols = blk.get("columns", [])
        ii = cols.index("INTITULE") if "INTITULE" in cols else -1
        idx = {c: cols.index(c) for c in ("N", "VY", "VZ", "MFY", "MFZ")
               if c in cols}
        if "N" not in idx:
            continue
        for row in blk.get("rows", []):
            label = str(row[ii]) if ii >= 0 else ""
            m = re.search(r"BOLT(\d+)", label)
            if not m:
                continue
            k = int(m.group(1))
            num = lambda c: (row[idx[c]] if c in idx
                             and isinstance(row[idx[c]], (int, float)) else 0.0)
            rec = {"N": num("N"),
                   "V": math.hypot(num("VY"), num("VZ")),
                   "M": math.hypot(num("MFY"), num("MFZ"))}
            if k not in out or abs(rec["N"]) > abs(out[k]["N"]):
                out[k] = rec
    return out
