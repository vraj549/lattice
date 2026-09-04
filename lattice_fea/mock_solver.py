"""A stand-in for `run_aster` that exercises Lattice's whole pipeline.

    LATTICE_ASTER_MODE=native \
    LATTICE_ASTER_CMD="python /path/to/tools/mock_run_aster.py" \
    python -m lattice_fea

It does NOT solve anything. It reads the mesh Lattice generated, invents a
plausible field, and writes the exact artifacts code_aster would write:
result.med (mesh section produced by gmsh's real MED writer, so the HDF5
layout is authentic rather than my guess), plus the CSV tables.

That validates everything downstream of the solver — job control, log
streaming, MED reading, skin extraction, field packaging, contour rendering,
mode animation, FRF plotting, bolt tables — without code_aster installed.

What it cannot validate: whether code_aster accepts the generated .comm, and
whether real field/component names match what the reader expects. Those need
a genuine run.
"""
from __future__ import annotations

import math
import os
import re
import sys
import time

import h5py
import numpy as np


def log(m):
    print(m, flush=True)
    time.sleep(0.05)


# The one piece of physics the mock does contain, and it is here because
# without it the preload calibration loop cannot be tested at all: an imposed
# strain does not deliver the force it was derived from. The clamped parts are
# a spring in parallel with the bolt and take JOINT_SHARE of it back. A real
# joint's share depends on its stiffness; a fixed value is enough to make the
# calibration converge to something checkable.
JOINT_SHARE = 0.18


def mock_bolt_axial(comm: str) -> dict:
    """{bolt index: axial force} implied by the deck's imposed strains.

    F = |EPX| * E * A, less the share the clamped parts take back. Reads the
    beam radius from AFFE_CARA_ELEM and E from the bolt's own DEFI_MATERIAU,
    so a change to either shows up here rather than being silently ignored.
    """
    rad = {int(m.group(1)): float(m.group(2)) for m in re.finditer(
        r"GROUP_MA=\('BOLT(\d+)',\), SECTION='CERCLE', CARA='R', VALE=([-\d.eE+]+)",
        comm)}
    mod = {int(m.group(1)): float(m.group(2)) for m in re.finditer(
        r"matb(\d+) = DEFI_MATERIAU\(ELAS=_F\(E=([-\d.eE+]+)", comm)}
    out = {}
    for m in re.finditer(r"GROUP_MA=\('BOLT(\d+)',\), EPX=([-\d.eE+]+)", comm):
        k, eps = int(m.group(1)), float(m.group(2))
        r, E = rad.get(k), mod.get(k)
        if r and E:
            out[k] = round(abs(eps) * E * math.pi * r * r * (1.0 - JOINT_SHARE), 3)
    return out


def parse_export(path="run.export"):
    units = {}
    for line in open(path, errors="replace"):
        p = line.split()
        if len(p) >= 5 and p[0] == "F":
            units[int(p[4])] = p[2]
    return units


def analysis_kind(comm: str) -> str:
    if "DYNA_VIBRA" in comm:
        return "harmonic"
    if "CALC_MODES" in comm:
        return "modal"
    return "static"


def write_mesh_med(unv: str, med: str):
    """Convert the UNV to MED using gmsh's own MED writer, then read the node
    coordinates back *out of the MED*.

    gmsh's in-memory node order (sorted by tag) is not MED's storage order, so
    computing a field from gmsh's arrays lands values on the wrong nodes. Since
    the fields are appended to this same file, they must be indexed the way the
    file stores nodes — so read them from the file.
    """
    import gmsh
    try:
        gmsh.initialize(interruptible=False)
    except TypeError:
        gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.clear()
    gmsh.open(unv)
    gmsh.option.setNumber("Mesh.SaveAll", 1)
    gmsh.write(med)
    gmsh.clear()
    gmsh.finalize()

    try:                          # invoked as a plain script, not a module
        from .med_reader import MedFile
    except ImportError:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from lattice_fea.med_reader import MedFile
    m = MedFile(med)              # same reader the app uses: same node order
    xyz = np.array(m.nodes, dtype=np.float64)
    m.close()
    return xyz


def mesh_name(f):
    return list(f["ENS_MAA"].keys())[0]


def add_nodal_field(f, name: str, comps, values, steps):
    """values: (nstep, nnode, ncomp).

    Written component-major (MED_NO_INTERLACE) to match how gmsh's MED writer
    stores this file's coordinates. The reader infers interlace once, from the
    coordinates, and applies it to fields; real code_aster writes both, so they
    always agree. Any mismatch here scrambles values across nodes and shows up
    as a spiky deformed shape.
    """
    cha = f.require_group("CHA")
    g = cha.create_group(name)
    g.attrs["NCO"] = np.int32(len(comps))
    nom = "".join(c.ljust(16)[:16] for c in comps)
    g.attrs["NOM"] = np.bytes_(nom)
    g.attrs["NOM_MAA"] = np.bytes_(mesh_name(f).ljust(64)[:64])
    g.attrs["TYP"] = np.int32(6)
    for i, (ndt, pdt) in enumerate(steps):
        sg = g.create_group(f"{ndt:012d}{0:012d}")
        sg.attrs["NDT"] = np.int32(ndt)
        sg.attrs["PDT"] = np.float64(pdt)
        sg.attrs["NOR"] = np.int32(0)
        noe = sg.create_group("NOE")
        prof = noe.create_group("MED_NO_PROFILE_INTERNAL")
        prof.attrs["NBR"] = np.int32(values.shape[1])
        prof.attrs["NGA"] = np.int32(1)
        flat = np.ascontiguousarray(values[i].T).ravel()     # component-major
        prof.create_dataset("CO", data=flat.astype(np.float64))


def synth_fields(xyz: np.ndarray, kind: str, nmodes: int, freqs):
    """Plausible cantilever-ish response so contours look like something."""
    lo, hi = xyz.min(0), xyz.max(0)
    span = np.maximum(hi - lo, 1e-9)
    t = (xyz - lo) / span                       # 0..1 per axis
    reach = t[:, 0] ** 2 + t[:, 1] ** 2         # grows away from the origin

    if kind == "static":
        disp = np.stack([0.02 * reach, 0.01 * reach, -0.35 * reach], 1)
        vm = 15.0 + 165.0 * (1.0 - t[:, 2]) * (0.3 + 0.7 * reach / max(reach.max(), 1e-9))
        return [(disp, vm)], [(1, 0.0)]

    out, steps = [], []
    for m in range(nmodes):
        k = (m + 1) * math.pi / 2.0
        shape = np.sin(k * t[:, 0]) * np.cos(m * math.pi * t[:, 1])
        disp = np.stack([0.1 * shape * t[:, 0],
                         0.1 * shape * t[:, 1],
                         shape], 1)
        disp /= max(np.abs(disp).max(), 1e-12)
        vm = 20.0 + 120.0 * np.abs(shape)
        out.append((disp, vm))
        steps.append((m + 1, freqs[m]))
    return out, steps


def main():
    units = parse_export()
    comm = open("run.comm", errors="replace").read()
    kind = analysis_kind(comm)

    log("  Code_Aster (MOCK stand-in — no physics was computed)")
    log(f"  analysis detected from run.comm: {kind}")
    log("LIRE_MAILLAGE  reading IDEAS mesh from unit 19")

    xyz = write_mesh_med("mesh.unv", "result.med")
    n = len(xyz)
    log(f"  mesh: {n} nodes")

    nmodes = 10
    m = re.search(r"NMAX_FREQ=(\d+)", comm)
    if m:
        nmodes = int(m.group(1))
    freqs = [round(120.0 * (i + 1) ** 1.7, 1) for i in range(nmodes)]

    if kind == "static":
        log("MECA_STATIQUE  MUMPS  ... converged")
    else:
        log(f"CALC_MODES  SORENSEN  extracting {nmodes} modes")
        for i, fq in enumerate(freqs):
            log(f"   mode {i + 1:2d}   f = {fq} Hz")

    fields, steps = synth_fields(xyz, kind, nmodes, freqs)
    disp = np.stack([f[0] for f in fields])
    vm = np.stack([f[1][:, None] for f in fields])

    with h5py.File("result.med", "r+") as f:
        add_nodal_field(f, "MOCK____DEPL", ["DX", "DY", "DZ"], disp, steps)
        if kind == "static":
            add_nodal_field(f, "MOCK____SIEQ_NOEU", ["VMIS"], vm, steps)
    log("IMPR_RESU  MED written to unit 80")

    # ---- tables ----
    if 38 in units or kind != "static":
        with open("modes.csv", "w") as fh:
            fh.write("NUME_ORDRE,FREQ\n")
            for i, fq in enumerate(freqs):
                fh.write(f"{i + 1},{fq}\n")
        with open("participation.csv", "w") as fh:
            fh.write("NUME_ORDRE,FREQ,MASS_GENE,MASS_EFFE_UN_DX,MASS_EFFE_UN_DY,MASS_EFFE_UN_DZ\n")
            # MASS_EFFE_UN_* is UNITARY: a fraction of the model's mass, so
            # the column has to sum to at most 1. A decaying geometric series
            # summing to ~0.92 leaves a visible missing-mass term, which is
            # what a real truncated basis looks like.
            for i, fq in enumerate(freqs):
                fh.write(f"{i + 1},{fq},1.0,{0.55 * 0.4 ** i:.5f},"
                         f"{0.30 * 0.5 ** i:.5f},{0.62 * 0.35 ** i:.5f}\n")

    with open("tables.txt", "w") as fh:
        fh.write("INTITULE,RESU,NOM_CHAM,DX,DY,DZ\n")
        fh.write("REACTIONS,1,REAC_NODA,0.0,0.0,2000.0\n")
        fh.write("\n")
        fh.write("LIEU,ENTITE,MASSE\n")
        fh.write("TOUT,TOUT,0.5551\n")

    # Per-mode extractions for a shock run. Deterministic but mode-dependent,
    # so a combination rule that ignored a mode would show up.
    if units.get(33) == "participation_factors.csv":
        with open("participation_factors.csv", "w") as fh:
            fh.write("NUME_MODE,FACT_PARTICI_DX,FACT_PARTICI_DY,FACT_PARTICI_DZ\n")
            for i, _fq in enumerate(freqs):
                # alternating signs, because that is what makes an algebraic
                # sum different from taking magnitudes
                sg = 1.0 if i % 2 == 0 else -1.0
                fh.write(f"{i + 1},{sg * 0.74 * 0.63 ** i:.6f},"
                         f"{sg * 0.55 * 0.71 ** i:.6f},"
                         f"{sg * 0.79 * 0.59 ** i:.6f}\n")
        log("IMPR_TABLE  participation factors -> unit 33")

    if units.get(36) == "mode_probes.csv":
        with open("mode_probes.csv", "w") as fh:
            fh.write("INTITULE,RESU,NOM_CHAM,NUME_ORDRE,NOEUD,DX,DY,DZ\n")
            for pn in sorted(set(re.findall(r"INTITULE='(PROBE\d+)'", comm))):
                for i, _fq in enumerate(freqs):
                    a = 1.0 / (i + 1)
                    fh.write(f"{pn},1,DEPL,{i + 1},N1,{0.3 * a},{0.2 * a},{a}\n")
        log("IMPR_TABLE  mode shapes at probes -> unit 36")

    if units.get(35) == "mode_bolts.csv":
        with open("mode_bolts.csv", "w") as fh:
            fh.write("INTITULE,RESU,NOM_CHAM,NUME_ORDRE,NOEUD,N,VY,VZ,MT,MFY,MFZ\n")
            for bn in sorted(set(re.findall(r"INTITULE='(BOLT\d+_[AB])'", comm))):
                end = 1.0 if bn.endswith("A") else 0.8
                for i, _fq in enumerate(freqs):
                    a = end / (i + 1)
                    fh.write(f"{bn},1,EFGE_ELNO,{i + 1},N1,{900 * a},{40 * a},"
                             f"{15 * a},0.0,{120 * a},{60 * a}\n")
        log("IMPR_TABLE  bolt forces per mode -> unit 35")

    # Interface stress for the slip check. Clamped hard with a modest shear,
    # which is what a preloaded joint that does NOT slip looks like — so the
    # demo exercises the passing branch rather than only the alarm.
    if units.get(34) == "contact_check.csv":
        with open("contact_check.csv", "w") as fh:
            fh.write("INTITULE,RESU,NOM_CHAM,NOEUD,SIXX,SIYY,SIZZ,SIXY,SIXZ,SIYZ\n")
            for cn in sorted(set(re.findall(r"INTITULE='(CONTACT\d+)'", comm))):
                for i in range(24):
                    p = -40.0 - 20.0 * math.sin(i)
                    fh.write(f"{cn},1,SIGM_NOEU,N{i + 1},5.0,-3.0,{p},1.0,"
                             f"{1.5 + 0.4 * i},0.5\n")
        log("IMPR_TABLE  interface stress -> unit 34")

    if units.get(36) == "bolt_forces.csv":
        axial = mock_bolt_axial(comm)
        with open("bolt_forces.csv", "w") as fh:
            fh.write("INTITULE,RESU,NOM_CHAM,NOEUD,N,VY,VZ,MT,MFY,MFZ\n")
            for i, bn in enumerate(re.findall(r"INTITULE='(BOLT\d+_[AB])'", comm)):
                k = int(re.match(r"BOLT(\d+)", bn).group(1))
                N = float(axial.get(k, 8000 + 120 * i))
                fh.write(f"{bn},1,EFGE_ELNO,{i + 1},{N},"
                         f"{45.0 + i},{12.0 + i},0.0,{300.0 + i},{80.0 + i}\n")
        log("IMPR_TABLE  bolt end forces -> unit 36")

    for path in sorted(f for f in os.listdir(".") if False):
        pass
    if kind == "harmonic":
        nprobe = len(re.findall(r"GROUP_NO='PROBE(\d+)'", comm))
        base = "CALC_CHAR_SEISME" in comm
        m = re.search(r"AMOR_REDUIT=\(([\d.eE+-]+)", comm)
        zeta = float(m.group(1)) if m else 0.02
        fl = re.search(r"lfr = DEFI_LIST_REEL\(VALE=\(([^)]*)\)", comm)
        sweep = [float(v) for v in fl.group(1).split(",") if v.strip()] if fl else []
        if not sweep:
            sweep = [20.0 * (2000.0 / 20.0) ** (i / 119) for i in range(120)]

        idx = 0
        for p in range(1, max(nprobe // 3, 1) + 1):
            for c in ("dx", "dy", "dz"):
                rows = []
                for fr in sweep:
                    w = 2 * math.pi * fr
                    re_s, im_s = 0.0, 0.0
                    for k, fn in enumerate(freqs):
                        wn = 2 * math.pi * fn
                        # modal participation falls off with mode number
                        gamma = 1.0 / (k + 1.5)
                        den = complex(wn * wn - w * w, 2 * zeta * wn * w)
                        # base excitation: relative displacement for 1 g input.
                        # force drive: arbitrary unit modal force.
                        num = -(9810.0) * gamma if base else 1.0e6 * gamma
                        h = num / den
                        re_s += h.real
                        im_s += h.imag
                    rows.append((fr, math.hypot(re_s, im_s), math.atan2(im_s, re_s)))
                with open(f"frf_p{p}_{c}.csv", "w", encoding="utf-8") as fh:
                    for fr, mag, _ in rows:
                        fh.write(f"{fr:.6f},{mag:.8e}\n")
                    for fr, _, ph in rows:
                        fh.write(f"{fr:.6f},{ph:.6f}\n")
                idx += 1
        log(f"IMPR_FONCTION  {idx} FRF curve file(s) written"
            + ("  [base excitation, relative displacement]" if base else ""))

    log("EXECUTION_CODE_ASTER  <I>  MOCK RUN COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
