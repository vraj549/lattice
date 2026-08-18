"""Solver invocation (native / WSL / docker) and background job management."""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import threading
import time
import traceback
import uuid

from .config import SolverConfig, win_to_wsl_path

# Every live child (gmsh worker, solver). Tracked so shutdown can reap them:
# on Windows a wsl.exe child sharing the console process group receives the
# same Ctrl+C and can leave the server unkillable.
# Keep the tail of a job's output, not all of it.
MAX_LOG = 4000
# Finished jobs stay queryable for a while, then go — the UI only polls the
# one it just started, and an unbounded dict is a slow leak in a long session.
MAX_JOBS = 60

LIVE_PROCS: "set[subprocess.Popen]" = set()
_PROC_LOCK = threading.Lock()


def popen_isolated(argv, **kw) -> subprocess.Popen:
    """Spawn a child that does NOT share the parent's Ctrl+C.

    Windows: CREATE_NEW_PROCESS_GROUP keeps the console Ctrl+C from reaching
    it. POSIX: start_new_session does the same for SIGINT.
    """
    if sys.platform == "win32":
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    proc = subprocess.Popen(argv, **kw)
    with _PROC_LOCK:
        LIVE_PROCS.add(proc)
    return proc


def reap(proc: subprocess.Popen) -> None:
    with _PROC_LOCK:
        LIVE_PROCS.discard(proc)


def kill_all_children(timeout: float = 3.0) -> int:
    """Terminate every tracked child. Returns how many were still running."""
    with _PROC_LOCK:
        procs = list(LIVE_PROCS)
    n = 0
    for p in procs:
        if p.poll() is None:
            n += 1
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
    deadline = time.time() + timeout
    for p in procs:
        try:
            p.wait(timeout=max(0.0, deadline - time.time()))
        except Exception:  # noqa: BLE001
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
    return n


class Job:
    def __init__(self, kind: str, label: str, key: str = ""):
        self.id = uuid.uuid4().hex[:10]
        self.kind = kind            # import | mesh | solve
        self.label = label
        self.key = key              # what this job owns, for exclusion
        self.status = "running"     # running | done | failed | cancelled
        self.log: list[str] = []
        self.dropped = 0            # lines trimmed off the front
        self.error = ""
        self.result = None
        self.started = time.time()
        self.finished = None
        self._proc = None
        self._lock = threading.Lock()

    def append(self, line: str):
        """Record one output line, keeping only the most recent MAX_LOG.

        A code_aster run on a large model emits hundreds of thousands of
        lines. Keeping all of them, for every job, for the life of the
        process, is tens of megabytes that are never read again. `dropped`
        keeps the client's offsets meaningful after trimming.
        """
        with self._lock:
            self.log.append(line.rstrip("\n"))
            excess = len(self.log) - MAX_LOG
            if excess > 0:
                del self.log[:excess]
                self.dropped += excess

    def log_since(self, offset: int):
        """Lines after an absolute offset, plus the new absolute end."""
        with self._lock:
            start = max(0, offset - self.dropped)
            return self.log[start:], self.dropped + len(self.log)

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

    def is_running(self, kind: str, key: str) -> bool:
        """Is a job of this kind already running for this target?"""
        with self._lock:
            return any(j.status == "running" and j.kind == kind and j.key == key
                       for j in self.jobs.values())

    def submit(self, kind: str, label: str, fn, key: str = "") -> Job:
        job = Job(kind, label, key)
        with self._lock:
            self.jobs[job.id] = job
            if len(self.jobs) > MAX_JOBS:
                done = [j for j in self.jobs.values() if j.finished]
                done.sort(key=lambda j: j.finished)
                for j in done[:len(self.jobs) - MAX_JOBS]:
                    self.jobs.pop(j.id, None)

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
    rc = -1
    with open(logfile, "w", encoding="utf-8", errors="replace") as lf:
        proc = popen_isolated(argv, cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              errors="replace", bufsize=1)
        job._proc = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                # Never let a logging problem destroy a finished solve: the
                # physics is already done by the time output is streaming.
                try:
                    lf.write(line)
                except Exception:  # noqa: BLE001
                    pass
                job.append(line)
            rc = proc.wait()
        finally:
            reap(proc)
    job.append(f"[exit code {rc}]")
    return rc


def run_ccx(cfg: SolverConfig, jobdir: str, job: Job, jobname: str = "job") -> int:
    """Run CalculiX in `jobdir`. ccx takes the deck name without .inp."""
    argv = shlex.split(cfg.ccx_cmd) + ["-i", jobname]
    job.append(f"$ {' '.join(argv)}  (in {jobdir})")
    env = dict(os.environ)
    # ccx reads its thread count from the environment, not a flag
    env["OMP_NUM_THREADS"] = str(max(1, int(cfg.ncpus)))
    env["CCX_NPROC_STIFFNESS"] = env["OMP_NUM_THREADS"]
    logfile = os.path.join(jobdir, "log.txt")
    rc = -1
    with open(logfile, "w", encoding="utf-8", errors="replace") as lf:
        proc = popen_isolated(argv, cwd=jobdir, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              errors="replace", bufsize=1, env=env)
        job._proc = proc
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    lf.write(line)
                except Exception:  # noqa: BLE001
                    pass
                job.append(line)
            rc = proc.wait()
        finally:
            reap(proc)
    job.append(f"[exit code {rc}]")
    return rc
