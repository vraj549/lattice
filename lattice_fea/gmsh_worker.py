"""gmsh subprocess worker.

gmsh is native code with global state; running it inside the server process
means one bad STEP file (or a thread-affinity quirk) can take the whole app
down — it did, during development. So every gmsh operation runs here, in a
short-lived subprocess whose stdout streams into the job log:

    python -m lattice_fea.gmsh_worker <argfile.json>

argfile: {"op": "import"|"mesh", ...op-specific paths/data...}
Outputs are written to the paths given in the argfile; exit code != 0 means
failure with the reason on stdout.
"""
from __future__ import annotations

import gzip
import json
import sys


def log(msg: str) -> None:
    print(msg, flush=True)


def op_import(a: dict) -> None:
    from . import geometry
    log("Importing STEP …")
    meta = geometry.import_step(a["step"], a["brep"])
    log(f"  {len(meta['solids'])} solid(s), {len(meta['faces'])} faces, "
        f"{len(meta['interfaces'])} bonded interface(s)")
    log("Tessellating for display …")
    tess = geometry.tessellate(a["brep"], meta["diag"])
    # surface classification (plane/cylinder + radius/axis) is computed from
    # the tessellation — merge it into the geometry metadata for the UI
    fits = {f["tag"]: f.pop("fit", None) for f in tess["faces"]}
    ncyl = 0
    for f in meta["faces"]:
        fit = fits.get(f["tag"])
        if fit:
            f["fit"] = fit
            ncyl += fit.get("kind") == "cylinder"
    if ncyl:
        log(f"  {ncyl} cylindrical face(s) detected (bolt-hole candidates)")
    with gzip.open(a["tess"], "wt", encoding="utf-8") as f:
        json.dump(tess, f)
    with open(a["meta_out"], "w", encoding="utf-8") as f:
        json.dump(meta, f)
    log("Import complete.")


def op_mesh(a: dict) -> None:
    from . import meshing
    out = meshing.mesh_project(a["brep"], a["unv"], a["meta"], a["setup"], progress=log)
    with open(a["stats_out"], "w", encoding="utf-8") as f:
        json.dump(out["stats"], f)
    with gzip.open(a["skin_out"], "wt", encoding="utf-8") as f:
        json.dump(out["skin"], f)
    s = out["stats"]
    log(f"Mesh: {s['nodes']:,} nodes / {s['elements']:,} elements "
        f"(order {s['order']}), {s['dof']:,} DOF")
    if s.get("quality_min") is not None:
        log(f"Quality (minSICN): min {s['quality_min']:.3f}, avg {s['quality_avg']:.3f}")
    log(f"Estimated factorization memory ~ {s['mem_gb_est']} GB")
    if s.get("islands", 1) > 1:
        log(f"WARNING: mesh has {s['islands']} disconnected groups — "
            "parts are not joined; check that solids actually touch.")


def main() -> int:
    with open(sys.argv[1], encoding="utf-8") as f:
        a = json.load(f)
    try:
        {"import": op_import, "mesh": op_mesh}[a["op"]](a)
        return 0
    except Exception as e:  # noqa: BLE001
        log(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
