"""Solver invocation (native / WSL / docker) and background job management."""
from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
import traceback
import uuid

from .config import SolverConfig, win_to_wsl_path


class Job:
    def __init__(self, kind: str, label: str):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind            # mesh | solve
        self.label = label
        self.status = "running"     # running | done | failed | cancelled
        self.log: list[str] = []
        self.error = ""
        self.result = None
        self.started = time.time()
        self.finished = None
        self._proc = None
        self._lock = threading.Lock()

    def append(self, line: str):
        with self._lock:
            self.log.append(line.rstrip("\n"))

    def log_since(self, offset: int):
        with self._lock:
            return self.log[offset:], len(self.log)

    def cancel(self):
        p = self._proc
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
        self.status = "cancelled"


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, kind: str, label: str, fn) -> Job:
        job = Job(kind, label)
        with self._lock:
            self.jobs[job.id] = job

        def run():
            try:
                job.result = fn(job)
                if job.status == "running":
                    job.status = "done"
            except Exception as e:  # noqa: BLE001
                job.error = str(e)
                job.append(f"ERROR: {e}")
                for ln in traceback.format_exc().splitlines()[-6:]:
                    job.append(ln)
                if job.status == "running":
                    job.status = "failed"
            finally:
                job.finished = time.time()

        threading.Thread(target=run, daemon=True).start()
        return job

    def get(self, jid: str) -> "Job|None":
        return self.jobs.get(jid)


# --------------------------------------------------------------------------

def solver_command(cfg: SolverConfig, jobdir: str) -> list:
    """Build the subprocess argv that runs `run_aster run.export` inside jobdir."""
    if cfg.mode == "native":
        return shlex.split(cfg.cmd) + ["run.export"]
    if cfg.mode == "wsl":
        wsl_dir = win_to_wsl_path(jobdir)
        inner = f"cd {shlex.quote(wsl_dir)} && {cfg.cmd} run.export"
        return ["wsl.exe", "-d", cfg.wsl_distro, "--", "bash", "-lc", inner]
    if cfg.mode == "docker":
        return ["docker", "run", "--rm",
                "-v", f"{jobdir}:/job", "-w", "/job",
                cfg.docker_image] + shlex.split(cfg.cmd) + ["run.export"]
    raise RuntimeError("No code_aster solver configured (demo mode). See README → Solver setup.")


def run_solver(cfg: SolverConfig, jobdir: str, job: Job) -> int:
    argv = solver_command(cfg, jobdir)
    job.append(f"$ {' '.join(argv)}")
    logfile = os.path.join(jobdir, "log.txt")
    cwd = jobdir if cfg.mode == "native" else None
    with open(logfile, "w") as lf:
        proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                errors="replace", bufsize=1)
        job._proc = proc
        assert proc.stdout is not None
        for line in proc.stdout:
            lf.write(line)
            job.append(line)
        rc = proc.wait()
    job.append(f"[exit code {rc}]")
    return rc
