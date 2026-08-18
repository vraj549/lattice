"""FastAPI application: REST API + static UI."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (FileResponse, JSONResponse, Response,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles

import hashlib

from . import (__version__, ccx_writer, comm_writer, config, random_vib,
               results)
from .materials import LIBRARY
from .projects import ProjectStore
from .solver import (JobManager, popen_isolated, reap, run_ccx, run_solver)

UI_DIR = os.path.join(os.path.dirname(__file__), "ui")


def run_gmsh_worker(job, args: dict) -> None:
    """Run a gmsh operation in an isolated subprocess, streaming its output
    into the job log. Raises on failure."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(args, f)
        argfile = f.name
    try:
        proc = popen_isolated([sys.executable, "-m", "lattice_fea.gmsh_worker", argfile],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace", bufsize=1)
        job._proc = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                job.append(line)
            rc = proc.wait()
        finally:
            reap(proc)
        if rc != 0:
            raise RuntimeError(f"geometry worker failed (exit {rc}) — see log")
    finally:
        try:
            os.unlink(argfile)
        except OSError:
            pass


def solve_signature(analysis: dict, setup: dict, mesh_stats: dict) -> str:
    """Fingerprint of everything that changes the answer.

    Stored with the results and re-checked when they are served, so results
    that no longer correspond to the current model are reported as OUT OF
    DATE rather than silently presented as current. Presenting stale numbers
    as live is the one failure mode that produces wrong engineering
    conclusions, so it is detected rather than left to the user to remember.
    """
    payload = {
        "analysis": {k: analysis.get(k) for k in ("type", "config", "supports", "loads")},
        "materials": setup.get("materials"),
        "assignments": setup.get("assignments"),
        "bolts": setup.get("bolts"),
        "ties": setup.get("ties"),
        "probes": setup.get("probes"),
        "mesh": {"nodes": mesh_stats.get("nodes"),
                 "elements": mesh_stats.get("elements"),
                 "groups": mesh_stats.get("face_groups"),
                 "bolts": mesh_stats.get("bolts"),
                 "remotes": mesh_stats.get("remotes")},
    }
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def create_app(workspace: str = "workspace") -> FastAPI:
    os.makedirs(workspace, exist_ok=True)
    app = FastAPI(title="Lattice", version=__version__)
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    store = ProjectStore(workspace)
    jobs = JobManager()
    solver_cfg = config.detect(workspace)
    print(f"[lattice] solver: {solver_cfg.mode} — {solver_cfg.detail}")
    for n in solver_cfg.notes:
        print(f"[lattice]   note: {n}")

    # ---------------- config ----------------
    @app.get("/api/config")
    def get_config():
        return {"version": __version__, "solver": solver_cfg.as_dict(),
                "workspace": os.path.abspath(workspace)}

    @app.post("/api/config/recheck")
    def recheck_solver():
        """Re-run solver detection without restarting — for when code_aster is
        installed or configured while Lattice is already running."""
        nonlocal solver_cfg
        solver_cfg = config.detect(workspace)
        print(f"[lattice] solver recheck: {solver_cfg.mode} — {solver_cfg.detail}")
        return {"solver": solver_cfg.as_dict()}

    @app.get("/api/materials")
    def get_materials():
        return LIBRARY

    # ---------------- projects ----------------
    @app.get("/api/projects")
    def list_projects():
        return store.list()

    MAX_UPLOAD_BYTES = 512 * 1024 * 1024

    # Deliberately `def`, not `async def`: FastAPI then runs it in a worker
    # thread. Copying a large STEP with a synchronous read inside an async
    # endpoint blocks the event loop, freezing every other request — including
    # the job polling that shows the import progress.
    @app.post("/api/projects")
    def create_project(name: str = Form(...), step: UploadFile = File(...),
                       assembly: str = Form("bonded")):
        pid = store.create(name)
        step_path = store.path(pid, "geometry.step")
        filename = step.filename or "upload.step"
        written = 0
        try:
            with open(step_path, "wb") as f:
                while True:
                    chunk = step.file.read(1 << 20)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > MAX_UPLOAD_BYTES:
                        raise HTTPException(413, "STEP file exceeds 512 MB")
                    f.write(chunk)
        except HTTPException:
            shutil.rmtree(store.dir(pid), ignore_errors=True)
            raise
        if written == 0:
            shutil.rmtree(store.dir(pid), ignore_errors=True)
            raise HTTPException(400, "the uploaded file is empty")

        def work(job):
            job.append(f"Importing {filename} …")
            meta_out = store.path(pid, "geo_meta.json")
            run_gmsh_worker(job, {
                "op": "import", "step": step_path,
                "fragment": assembly != "contact",
                "brep": store.path(pid, "geometry.brep"),
                "tess": store.path(pid, "tess.json.gz"),
                "meta_out": meta_out,
            })
            with open(meta_out, encoding="utf-8") as f:
                meta = json.load(f)
            proj = store.load(pid)
            proj["geometry"] = meta
            store.save(pid, proj)
            return {"project": pid}

        job = jobs.submit("import", f"import {filename}", work)
        return {"id": pid, "job": job.id}

    def _project(pid: str) -> dict:
        try:
            return store.load(pid)
        except FileNotFoundError:
            raise HTTPException(404, "project not found")
        except ValueError:                      # rejected by the path guard
            raise HTTPException(404, "project not found")

    @app.get("/api/projects/{pid}")
    def get_project(pid: str):
        return _project(pid)

    @app.delete("/api/projects/{pid}")
    def delete_project(pid: str):
        d = store.dir(pid)
        if os.path.isdir(d):
            shutil.rmtree(d)
        return {"ok": True}

    @app.get("/api/projects/{pid}/tessellation")
    def get_tess(pid: str):
        _project(pid)
        if not store.exists(pid, "tess.json.gz"):
            raise HTTPException(409, "geometry not imported yet")
        return JSONResponse(store.read_json_gz(pid, "tess.json.gz"))

    @app.put("/api/projects/{pid}/setup")
    async def put_setup(pid: str, setup: dict):
        proj = _project(pid)
        proj["setup"] = setup
        store.save(pid, proj)
        return {"ok": True}

    # ---------------- meshing ----------------
    @app.post("/api/projects/{pid}/mesh")
    def mesh(pid: str):
        proj = _project(pid)
        if not proj.get("geometry"):
            raise HTTPException(409, "import geometry first")

        if jobs.is_running("mesh", pid):
            raise HTTPException(409, "this project is already meshing")

        def work(job):
            run_gmsh_worker(job, {
                "op": "mesh",
                "brep": store.path(pid, "geometry.brep"),
                "unv": store.path(pid, "mesh", "mesh.unv"),
                "meta": proj["geometry"], "setup": proj["setup"],
                "stats_out": store.path(pid, "mesh", "stats.json"),
                "skin_out": store.path(pid, "mesh", "skin.json.gz"),
            })
            return {"stats": store.read_json(pid, "mesh/stats.json")}

        job = jobs.submit("mesh", f"mesh {pid}", work, key=pid)
        return {"job": job.id}

    @app.get("/api/projects/{pid}/mesh")
    def get_mesh(pid: str):
        _project(pid)
        if not store.exists(pid, "mesh/stats.json"):
            raise HTTPException(409, "not meshed yet")
        return {"stats": store.read_json(pid, "mesh/stats.json"),
                "skin": store.read_json_gz(pid, "mesh/skin.json.gz")}

    # ---------------- solving ----------------
    @app.post("/api/projects/{pid}/solve/{aid}")
    def solve(pid: str, aid: str):
        proj = _project(pid)
        analysis = next((a for a in proj["setup"].get("analyses", []) if a["id"] == aid), None)
        if analysis is None:
            raise HTTPException(404, "analysis not found")
        if not store.exists(pid, "mesh/stats.json"):
            raise HTTPException(409, "mesh the model first")
        if not solver_cfg.engines():
            raise HTTPException(409, f"no solver configured: {solver_cfg.detail}")
        # Two solves writing one run directory interleave their output and
        # leave results that belong to neither. A double-click on Run is
        # enough to do it.
        if jobs.is_running("solve", f"{pid}/{aid}"):
            raise HTTPException(409, "this analysis is already running")

        mesh_stats = store.read_json(pid, "mesh/stats.json")
        run_dir = store.path(pid, "runs", aid)
        os.makedirs(run_dir, exist_ok=True)

        # Which engine runs this. The analysis may name one; otherwise take
        # whatever is installed, preferring code_aster because it covers every
        # analysis type.
        want = (analysis.get("config") or {}).get("engine") or ""
        engines = {e["id"] for e in solver_cfg.engines()}
        if want and want not in engines:
            raise HTTPException(409, f"solver '{want}' is not available here")
        engine = want or ("aster" if "aster" in engines else "ccx")

        if engine == "ccx":
            try:
                deck = ccx_writer.build_deck(analysis, proj["setup"],
                                             proj["geometry"], mesh_stats)
            except ValueError as e:
                raise HTTPException(422, str(e))
            if not store.exists(pid, "mesh/mesh.inp"):
                raise HTTPException(409, "re-mesh: this mesh predates CalculiX support")
            with open(os.path.join(run_dir, "job.inp"), "w", encoding="utf-8") as f:
                f.write(deck)
            shutil.copyfile(store.path(pid, "mesh", "mesh.inp"),
                            os.path.join(run_dir, "mesh.inp"))
        else:
            try:
                comm, export = comm_writer.build_run(analysis, proj["setup"],
                                                     proj["geometry"], mesh_stats,
                                                     solver_cfg)
            except ValueError as e:
                raise HTTPException(422, str(e))
            with open(os.path.join(run_dir, "run.comm"), "w", encoding="utf-8") as f:
                f.write(comm)
            with open(os.path.join(run_dir, "run.export"), "w", encoding="utf-8") as f:
                f.write(export)
            shutil.copyfile(store.path(pid, "mesh", "mesh.unv"),
                            os.path.join(run_dir, "mesh.unv"))
        # Every artifact of a previous run must go. Leaving even one behind
        # lets a FAILED re-run present the previous run's numbers as if they
        # were current — results parsing only checks whether files exist.
        for f in os.listdir(run_dir):
            if (f.endswith((".med", ".csv", ".json", ".frd", ".dat", ".sta", ".cvg"))
                    or f in ("tables.txt", "log.txt")):
                try:
                    os.remove(os.path.join(run_dir, f))
                except OSError:
                    pass

        geo = proj["geometry"]

        def work(job):
            if engine == "ccx":
                rc = run_ccx(solver_cfg, run_dir, job, "job")
                job.append("Parsing results …")
                meta = results.build_results_ccx(run_dir, "job")
            else:
                rc = run_solver(solver_cfg, run_dir, job)
                job.append("Parsing results …")
                meta = results.build_results(run_dir, geo["bbox"],
                                             mesh_stats.get("geo_volume"))
            meta["engine"] = engine
            meta["exit_code"] = rc
            meta["signature"] = solve_signature(analysis, proj["setup"], mesh_stats)
            store.write_json(pid, f"runs/{aid}/meta.json", meta)
            if rc != 0 and not meta["fields"] and not meta["tables"]:
                raise RuntimeError(f"solver failed (exit {rc}) — see log")
            if rc != 0:
                job.append("Solver exited non-zero but partial results were recovered.")
            for w in meta.get("warnings", []):
                job.append(f"warning: {w}")
            job.append("Done.")
            return {"analysis": aid}

        job = jobs.submit("solve", f"{analysis['type']} {pid}/{aid}", work,
                          key=f"{pid}/{aid}")
        return {"job": job.id}

    @app.get("/api/projects/{pid}/results/{aid}")
    def result_meta(pid: str, aid: str):
        proj = _project(pid)
        if not store.exists(pid, f"runs/{aid}/meta.json"):
            raise HTTPException(404, "no results for this analysis")
        meta = store.read_json(pid, f"runs/{aid}/meta.json")
        analysis = next((a for a in proj["setup"].get("analyses", [])
                         if a["id"] == aid), None)
        mesh_stats = (store.read_json(pid, "mesh/stats.json")
                      if store.exists(pid, "mesh/stats.json") else {})
        if analysis is not None:
            now = solve_signature(analysis, proj["setup"], mesh_stats)
            meta["stale"] = bool(meta.get("signature")) and meta["signature"] != now
            meta["no_signature"] = not meta.get("signature")
        return meta

    @app.get("/api/projects/{pid}/results-status")
    def results_status(pid: str):
        """Which analyses have results, and whether they still match the model.

        Deliberately tiny: the UI re-checks this after every edit so the tree
        badges stay honest, and re-reading full result payloads (an FRF is
        hundreds of kB) for that would be absurd.
        """
        proj = _project(pid)
        mesh_stats = (store.read_json(pid, "mesh/stats.json")
                      if store.exists(pid, "mesh/stats.json") else {})
        out = {}
        for a in proj["setup"].get("analyses", []):
            aid = a["id"]
            if not store.exists(pid, f"runs/{aid}/meta.json"):
                continue
            meta = store.read_json(pid, f"runs/{aid}/meta.json")
            sig = meta.get("signature")
            out[aid] = {
                "has_results": True,
                "no_signature": not sig,
                "stale": bool(sig) and sig != solve_signature(a, proj["setup"], mesh_stats),
            }
        return out

    @app.post("/api/projects/{pid}/results/{aid}/reparse")
    def reparse(pid: str, aid: str):
        """Rebuild meta.json from whatever the solver already left on disk.

        A solve can finish and write result.med while a later step fails —
        this recovers those results without paying for the run again."""
        proj = _project(pid)
        run_dir = store.path(pid, "runs", aid)
        if not os.path.isdir(run_dir):
            raise HTTPException(404, "no run directory for this analysis")
        produced = [f for f in ("result.med", "modes.csv", "tables.txt",
                                "bolt_forces.csv") if os.path.isfile(os.path.join(run_dir, f))]
        if not produced:
            raise HTTPException(409, "the run produced no result files to parse")
        mesh_stats = (store.read_json(pid, "mesh/stats.json")
                      if store.exists(pid, "mesh/stats.json") else {})
        meta = results.build_results(run_dir, proj["geometry"]["bbox"],
                                     mesh_stats.get("geo_volume"))
        meta["reparsed"] = True
        store.write_json(pid, f"runs/{aid}/meta.json", meta)
        return {"ok": True, "found": produced, "meta": meta}

    @app.get("/api/projects/{pid}/results/{aid}/export")
    def export_results(pid: str, aid: str, what: str = "all"):
        """Everything from a run as CSV, so numbers can leave the tool.

        `what`: tables | frf | random | nodes | all. Returns text/csv with
        blocks separated by blank lines when several are requested.
        """
        proj = _project(pid)
        if not store.exists(pid, f"runs/{aid}/meta.json"):
            raise HTTPException(404, "no results for this analysis")
        meta = store.read_json(pid, f"runs/{aid}/meta.json")
        analysis = next((a for a in proj["setup"].get("analyses", [])
                         if a["id"] == aid), None)
        name = (analysis or {}).get("name") or aid
        mesh_stats = (store.read_json(pid, "mesh/stats.json")
                      if store.exists(pid, "mesh/stats.json") else {})
        stale = False
        if analysis is not None and meta.get("signature"):
            stale = meta["signature"] != solve_signature(analysis, proj["setup"], mesh_stats)
        out = [f"# Lattice export — {proj.get('name')} / {name}",
               f"# analysis type: {(analysis or {}).get('type')}",
               f"# units: mm, N, MPa, Hz",
               # exported numbers outlive the session — say on the file itself
               # whether they still matched the model when it was written
               "# status: OUT OF DATE — the model changed after this run" if stale
               else "# status: current for the model as saved",
               ""]

        if what in ("all", "tables"):
            for key, blocks in (meta.get("tables") or {}).items():
                for bi, blk in enumerate(blocks):
                    out.append(f"# table: {key}" + (f" [{bi}]" if bi else ""))
                    out.append(",".join(str(c) for c in blk["columns"]))
                    for row in blk["rows"]:
                        out.append(",".join("" if v is None else str(v) for v in row))
                    out.append("")

        if what in ("all", "frf") and meta.get("frf"):
            probes = proj["setup"].get("probes", [])
            for c in meta["frf"]:
                pname = probes[c["probe"] - 1]["name"] if c["probe"] - 1 < len(probes) else f"P{c['probe']}"
                out.append(f"# FRF: {pname} {c['comp']}")
                out.append("freq_Hz,module,phase_rad")
                ph = c.get("phase") or []
                for i, f in enumerate(c["freq"]):
                    out.append(f"{f},{c['module'][i]},{ph[i] if i < len(ph) else ''}")
                out.append("")

        if what in ("all", "random") and analysis and analysis.get("type") == "random":
            cfg = analysis.get("config", {})
            spec = cfg.get("spec") or []
            if len(spec) >= 2 and meta.get("frf"):
                base_g = float(cfg.get("base_g", 1.0))
                out.append("# random response")
                out.append("probe,comp,grms,three_sigma")
                for c in meta["frf"]:
                    t = random_vib.transmissibility(
                        c["freq"], c["module"], c.get("phase") or [], base_g)
                    r = random_vib.response(c["freq"], t, spec, base_g)
                    out.append(f"{c['probe']},{c['comp']},{r['grms']:.6g},{r['three_sigma']:.6g}")
                out.append("")

        if what == "nodes":
            raise HTTPException(422, "per-node export: request a specific field via /field")

        body = "\n".join(out) + "\n"
        fn = f"{proj.get('name', 'lattice')}-{name}".replace(" ", "_")
        return Response(content=body, media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{fn}.csv"'})

    @app.get("/api/projects/{pid}/results/{aid}/random")
    def random_response(pid: str, aid: str):
        """Response PSD / RMS from the swept transmissibility and the input
        spectrum. Computed here rather than in the solver so the maths is
        unit-tested (see tests/test_random_vib.py)."""
        proj = _project(pid)
        analysis = next((a for a in proj["setup"].get("analyses", [])
                         if a["id"] == aid), None)
        if analysis is None:
            raise HTTPException(404, "analysis not found")
        if not store.exists(pid, f"runs/{aid}/meta.json"):
            raise HTTPException(404, "no results for this analysis")
        meta = store.read_json(pid, f"runs/{aid}/meta.json")
        cfg = analysis.get("config", {})
        spec = cfg.get("spec") or []
        if len(spec) < 2:
            raise HTTPException(422, "analysis has no PSD spectrum")
        base_g = float(cfg.get("base_g", 1.0))

        out = []
        for curve in meta.get("frf", []):
            t = random_vib.transmissibility(
                curve["freq"], curve["module"], curve.get("phase") or [], base_g)
            r = random_vib.response(curve["freq"], t, spec, base_g)
            r.update({"probe": curve["probe"], "comp": curve["comp"],
                      "trans": t})
            out.append(r)

        # Miles cross-check on the dominant peak of each curve
        modes = (meta.get("tables", {}).get("modes") or [{}])[0]
        fi = modes.get("columns", []).index("FREQ") if "FREQ" in modes.get("columns", []) else None
        zeta = float(cfg.get("damping", 0.02))
        checks = []
        if fi is not None:
            for row in modes.get("rows", [])[:6]:
                fn = row[fi]
                if isinstance(fn, (int, float)) and fn > 0:
                    checks.append(random_vib.miles(fn, 1.0 / (2 * zeta), spec))

        part = (meta.get("tables", {}).get("participation") or [None])[0]
        return {"curves": out, "miles": checks,
                "participation": random_vib.cumulative_participation(part),
                "grms_in": random_vib.grms_input(spec)}

    @app.get("/api/projects/{pid}/results/{aid}/field")
    def result_field(pid: str, aid: str, name: str, step: str, comp: str = "MAG"):
        proj = _project(pid)
        run_dir = store.path(pid, "runs", aid)
        mesh_stats = store.read_json(pid, "mesh/stats.json") if store.exists(pid, "mesh/stats.json") else {}
        try:
            return JSONResponse(results.field_payload(
                run_dir, name, step, comp,
                proj["geometry"]["bbox"], mesh_stats.get("geo_volume")))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(422, f"field read failed: {e}")

    @app.get("/api/projects/{pid}/results/{aid}/field.csv")
    def result_field_csv(pid: str, aid: str, name: str, step: str):
        """Every nodal value of one field/step, for post-processing elsewhere.

        All components are written, not just the one on screen — re-exporting
        per component to rebuild a tensor is not something anyone should have
        to do."""
        proj = _project(pid)
        run_dir = store.path(pid, "runs", aid)
        mesh_stats = (store.read_json(pid, "mesh/stats.json")
                      if store.exists(pid, "mesh/stats.json") else {})
        analysis = next((a for a in proj["setup"].get("analyses", [])
                         if a["id"] == aid), None)
        label = (analysis or {}).get("name") or aid

        def gen():
            yield f"# Lattice nodal export — {proj.get('name')} / {label}\n"
            yield f"# field: {name}  step: {step}\n"
            yield "# units: mm, MPa\n"
            try:
                for line in results.field_csv_rows(
                        run_dir, name, step, proj["geometry"]["bbox"],
                        mesh_stats.get("geo_volume")):
                    yield line
            except Exception as e:  # noqa: BLE001
                # the response has already started, so the failure has to be
                # reported inside the file rather than as a status code
                yield f"# EXPORT FAILED: {e}\n"

        fn = f"{proj.get('name', 'lattice')}-{label}-{name}".replace(" ", "_")
        return StreamingResponse(
            gen(), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fn}.csv"'})

    # ---------------- jobs ----------------
    @app.get("/api/jobs/{jid}")
    def job_status(jid: str, offset: int = 0):
        job = jobs.get(jid)
        if job is None:
            raise HTTPException(404, "job not found")
        lines, n = job.log_since(offset)
        return {"id": job.id, "kind": job.kind, "label": job.label,
                "status": job.status, "error": job.error,
                "log": lines, "log_offset": n}

    @app.post("/api/jobs/{jid}/cancel")
    def job_cancel(jid: str):
        job = jobs.get(jid)
        if job is None:
            raise HTTPException(404, "job not found")
        job.cancel()
        return {"ok": True}

    # ---------------- UI ----------------
    # The UI is served from local disk to one user; caching buys nothing and
    # costs correctness. ES module imports (./ui.js) carry no version query,
    # so a stale cached module can silently shadow a fixed one on disk —
    # that shipped once already. no-store makes it impossible.
    NO_CACHE = {"Cache-Control": "no-store, must-revalidate",
                "Pragma": "no-cache", "Expires": "0"}

    @app.get("/")
    def index():
        return FileResponse(os.path.join(UI_DIR, "index.html"), headers=NO_CACHE)

    class NoCacheStatic(StaticFiles):
        def is_not_modified(self, response_headers, request_headers) -> bool:
            return False  # never answer 304 for UI assets

        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            resp.headers.update(NO_CACHE)
            return resp

    app.mount("/ui", NoCacheStatic(directory=UI_DIR), name="ui")
    return app
