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
    """Convert the UNV to MED using gmsh's own MED writer."""
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
    node_tags, coords, _ = gmsh.model.mesh.getNodes()
    order = np.argsort(node_tags)
    xyz = np.asarray(coords).reshape(-1, 3)[order]
    gmsh.clear()
    gmsh.finalize()
    return xyz


def mesh_name(f):
    return list(f["ENS_MAA"].keys())[0]


def add_nodal_field(f, name: str, comps, values, steps):
    """values: (nstep, nnode, ncomp). Written planar (component-major) to
    deliberately exercise the reader's layout auto-detection."""
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
        planar = np.ascontiguousarray(values[i].T).ravel()   # component-major
        prof.create_dataset("CO", data=planar.astype(np.float64))


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
            for i, fq in enumerate(freqs):
                d = 0.62 / (i + 1)
                fh.write(f"{i + 1},{fq},1.0,{d:.4f},{d * 0.4:.4f},{d * 0.2:.4f}\n")

    with open("tables.txt", "w") as fh:
        fh.write("INTITULE,RESU,NOM_CHAM,DX,DY,DZ\n")
        fh.write("REACTIONS,1,REAC_NODA,0.0,0.0,2000.0\n")
        fh.write("\n")
        fh.write("LIEU,ENTITE,MASSE\n")
        fh.write("TOUT,TOUT,0.5551\n")

    if "EFGE_ELNO" in comm:
        with open("bolt_forces.csv", "w") as fh:
            fh.write("INTITULE,RESU,NOM_CHAM,NOEUD,N,VY,VZ,MT,MFY,MFZ\n")
            for i, bn in enumerate(re.findall(r"INTITULE='(BOLT\d+_[AB])'", comm)):
                fh.write(f"{bn},1,EFGE_ELNO,{i + 1},{8000 + 120 * i}.0,"
                         f"{45.0 + i},{12.0 + i},0.0,{300.0 + i},{80.0 + i}\n")
        log("IMPR_TABLE  bolt end forces -> unit 36")

    for path in sorted(f for f in os.listdir(".") if False):
        pass
    if kind == "harmonic":
        nprobe = len(re.findall(r"GROUP_NO='PROBE(\d+)'", comm)) or 0
        idx = 0
        for p in range(1, max(nprobe // 3, 1) + 1):
            for c in ("dx", "dy", "dz"):
                fq0, fq1 = 20.0, 2000.0
                with open(f"frf_p{p}_{c}.csv", "w") as fh:
                    for s in range(120):
                        fr = fq0 * (fq1 / fq0) ** (s / 119)
                        amp = 1e-4
                        for fq in freqs:
                            r = fr / fq
                            amp += 2e-3 / math.sqrt((1 - r * r) ** 2 + (2 * 0.02 * r) ** 2)
                        fh.write(f"{fr:.4f},{amp:.8e}\n")
                    for s in range(120):
                        fr = fq0 * (fq1 / fq0) ** (s / 119)
                        fh.write(f"{fr:.4f},{-math.atan2(1, 1 - (fr / freqs[0]) ** 2):.6f}\n")
                idx += 1
        log(f"IMPR_FONCTION  {idx} FRF curve file(s) written")

    log("EXECUTION_CODE_ASTER  <I>  MOCK RUN COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
