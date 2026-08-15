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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, comm_writer, config, results
from .materials import LIBRARY
from .projects import ProjectStore
from .solver import JobManager, run_solver

UI_DIR = os.path.join(os.path.dirname(__file__), "ui")


def run_gmsh_worker(job, args: dict) -> None:
    """Run a gmsh operation in an isolated subprocess, streaming its output
    into the job log. Raises on failure."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(args, f)
        argfile = f.name
    try:
        proc = subprocess.Popen([sys.executable, "-m", "lattice_fea.gmsh_worker", argfile],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace", bufsize=1)
        job._proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            job.append(line)
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"geometry worker failed (exit {rc}) — see log")
    finally:
        try:
            os.unlink(argfile)
        except OSError:
            pass


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

    @app.post("/api/projects")
    async def create_project(name: str = Form(...), step: UploadFile = File(...)):
        pid = store.create(name)
        step_path = store.path(pid, "geometry.step")
        with open(step_path, "wb") as f:
            shutil.copyfileobj(step.file, f)

        def work(job):
            job.append(f"Importing {step.filename} …")
            meta_out = store.path(pid, "geo_meta.json")
            run_gmsh_worker(job, {
                "op": "import", "step": step_path,
                "brep": store.path(pid, "geometry.brep"),
                "tess": store.path(pid, "tess.json.gz"),
                "meta_out": meta_out,
            })
            with open(meta_out) as f:
                meta = json.load(f)
            proj = store.load(pid)
            proj["geometry"] = meta
            store.save(pid, proj)
            return {"project": pid}

        job = jobs.submit("import", f"import {step.filename}", work)
        return {"id": pid, "job": job.id}

    def _project(pid: str) -> dict:
        try:
            return store.load(pid)
        except FileNotFoundError:
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

        job = jobs.submit("mesh", f"mesh {pid}", work)
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
        if not solver_cfg.available():
            raise HTTPException(409, f"no solver configured: {solver_cfg.detail}")

        mesh_stats = store.read_json(pid, "mesh/stats.json")
        run_dir = store.path(pid, "runs", aid)
        os.makedirs(run_dir, exist_ok=True)

        try:
            comm, export = comm_writer.build_run(analysis, proj["setup"],
                                                 proj["geometry"], mesh_stats, solver_cfg)
        except ValueError as e:
            raise HTTPException(422, str(e))

        with open(os.path.join(run_dir, "run.comm"), "w") as f:
            f.write(comm)
        with open(os.path.join(run_dir, "run.export"), "w") as f:
            f.write(export)
        shutil.copyfile(store.path(pid, "mesh", "mesh.unv"),
                        os.path.join(run_dir, "mesh.unv"))
        # stale results from a previous run must not survive
        for stale in ("result.med", "meta.json"):
            p = os.path.join(run_dir, stale)
            if os.path.isfile(p):
                os.remove(p)

        geo = proj["geometry"]

        def work(job):
            rc = run_solver(solver_cfg, run_dir, job)
            job.append("Parsing results …")
            meta = results.build_results(run_dir, geo["bbox"],
                                         mesh_stats.get("geo_volume"))
            meta["exit_code"] = rc
            store.write_json(pid, f"runs/{aid}/meta.json", meta)
            if rc != 0 and not meta["fields"] and not meta["tables"]:
                raise RuntimeError(f"solver failed (exit {rc}) — see log")
            if rc != 0:
                job.append("Solver exited non-zero but partial results were recovered.")
            for w in meta.get("warnings", []):
                job.append(f"warning: {w}")
            job.append("Done.")
            return {"analysis": aid}

        job = jobs.submit("solve", f"{analysis['type']} {pid}/{aid}", work)
        return {"job": job.id}

    @app.get("/api/projects/{pid}/results/{aid}")
    def result_meta(pid: str, aid: str):
        _project(pid)
        if not store.exists(pid, f"runs/{aid}/meta.json"):
            raise HTTPException(404, "no results for this analysis")
        return store.read_json(pid, f"runs/{aid}/meta.json")

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
    @app.get("/")
    def index():
        return FileResponse(os.path.join(UI_DIR, "index.html"))

    app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")
    return app
