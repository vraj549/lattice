"""Solver invocation (native / WSL / docker) and background job management."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid

from .config import SolverConfig, win_to_wsl_path

# Keep the tail of a job's output, not all of it.
MAX_LOG = 4000
# Finished jobs stay queryable for a while, then go — the UI only polls the
# one it just started, and an unbounded dict is a slow leak in a long session.
MAX_JOBS = 60

# Every live child (gmsh worker, solver), with the commands that stop what it
# started somewhere this process cannot signal. Tracked so cancel and shutdown
# can reap them: on Windows a wsl.exe child sharing the console process group
# receives the same Ctrl+C and can leave the server unkillable.
LIVE_PROCS: "dict[subprocess.Popen, list]" = {}
_PROC_LOCK = threading.Lock()

# Stops a process and everything below it, children first so nothing is
# orphaned part-way. Run inside WSL against the pid the launch recorded.
_KILL_TREE = ('k() {{ for c in $(pgrep -P "$1"); do k "$c"; done; '
              'kill -TERM "$1" 2>/dev/null; }}; '
              'p=$(cat {pidfile} 2>/dev/null) && [ -n "$p" ] && k "$p"')


def popen_isolated(argv, cleanup=None, **kw) -> subprocess.Popen:
    """Spawn a child that does NOT share the parent's Ctrl+C.

    Windows: CREATE_NEW_PROCESS_GROUP keeps the console Ctrl+C from reaching
    it. POSIX: start_new_session does the same for SIGINT, and makes the child
    the leader of its own process group, so the whole tree can be signalled.

    `cleanup` is a list of argv to run when the child is stopped early, for
    work it started that no local signal reaches (a WSL process, a container).
    """
    if sys.platform == "win32":
        kw["creationflags"] = kw.get("creationflags", 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, **kw)
    except FileNotFoundError:
        raise RuntimeError(
            f"Could not start `{argv[0]}`: it is not installed or not on PATH. "
            "Check the solver settings (LATTICE_ASTER_CMD, LATTICE_CCX_CMD or "
            "lattice.toml).") from None
    with _PROC_LOCK:
        LIVE_PROCS[proc] = list(cleanup or [])
    return proc


def reap(proc: subprocess.Popen) -> None:
    with _PROC_LOCK:
        LIVE_PROCS.pop(proc, None)


def terminate_tree(proc: subprocess.Popen, grace: float = 3.0) -> None:
    """Stop a child and everything it started.

    terminate() alone signals only the direct child. That is the solver for
    CalculiX, but for code_aster it is run_aster, whose solver process kept
    running after a cancel — still writing into the run directory the next
    run would use. Under WSL or docker the solver is not even a local process.
    """
    if proc.poll() is not None:
        return          # finished: nothing to stop, and its pid may be reused
    with _PROC_LOCK:
        cleanup = LIVE_PROCS.get(proc, [])
    for argv in cleanup:
        try:
            subprocess.run(argv, capture_output=True, timeout=20)
        except Exception:  # noqa: BLE001 — best effort; the local kill follows
            pass
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            os.killpg(proc.pid, signal.SIGTERM)
    except Exception:  # noqa: BLE001
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    try:
        proc.wait(timeout=grace)
    except Exception:  # noqa: BLE001
        try:
            if sys.platform == "win32":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except Exception:  # noqa: BLE001
            pass


def kill_all_children(timeout: float = 3.0) -> int:
    """Stop every tracked child and its tree. Returns how many were running."""
    with _PROC_LOCK:
        procs = list(LIVE_PROCS)
    n = sum(1 for p in procs if p.poll() is None)
    for p in procs:
        terminate_tree(p, grace=timeout)
    return n


class Cancelled(BaseException):
    """The job was cancelled: stop here, and do not report it as a failure.

    A BaseException, like KeyboardInterrupt, so the many broad `except
    Exception` fallbacks in the solve path cannot swallow it. When one did,
    a cancel during preload calibration read as "calibration failed" and the
    job went on to launch the main solve.
    """


def check_cancelled(job) -> None:
    if job.cancel_requested:
        raise Cancelled()


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
        # Asked to stop. The status stays "running" until the job's thread
        # has actually stopped: marked cancelled at once, it released the
        # run directory and the mesh to the next job while the solver it had
        # started was still being killed and still writing.
        self.cancel_requested = False
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
        if self.status != "running":
            return      # a finished job stays what it was
        self.cancel_requested = True
        self.append("Cancelling…")
        p = self._proc
        if p is not None:
            # off the request thread: stopping a WSL or docker run means
            # running a command there, which can take a few seconds
            threading.Thread(target=terminate_tree, args=(p,), daemon=True).start()


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def is_running(self, kind: str, key: str) -> bool:
        """Is a job of this kind already running for this target?"""
        with self._lock:
            return any(j.status == "running" and j.kind == kind and j.key == key
                       for j in self.jobs.values())

    def owner_of_mesh(self, pid: str) -> "str|None":
        """Label of a running job that is reading or writing this project's
        mesh, or None.

        The mesh is one set of files shared by every analysis, and a solve
        reads it for its whole life — the preload calibration re-copies
        mesh.unv part-way through. So a re-mesh during a solve produces a
        preload calibrated on one discretisation and applied to another, and
        exits 0. The two must exclude each other at the project level; solves
        of different analyses need not, and should not, exclude each other.
        """
        with self._lock:
            for j in self.jobs.values():
                if j.status != "running":
                    continue
                if j.kind == "mesh" and j.key == pid:
                    return j.label
                if j.kind == "solve" and j.key.split("/", 1)[0] == pid:
                    return j.label
        return None

    def meshing(self, pid: str) -> bool:
        with self._lock:
            return any(j.status == "running" and j.kind == "mesh" and j.key == pid
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
                # a cancel that arrived after the last check came too late:
                # the work is complete and saved, so it is done
                job.status = "done"
            except Cancelled:
                job.append("Cancelled.")
                job.status = "cancelled"
            except Exception as e:  # noqa: BLE001
                if job.cancel_requested:
                    # the kill surfaced as an error somewhere; it is not one
                    job.append("Cancelled.")
                    job.status = "cancelled"
                else:
                    job.error = str(e)
                    job.append(f"ERROR: {e}")
                    for ln in traceback.format_exc().splitlines()[-6:]:
                        job.append(ln)
                    job.status = "failed"
            finally:
                job.finished = time.time()

        threading.Thread(target=run, daemon=True).start()
        return job

    def get(self, jid: str) -> "Job|None":
        return self.jobs.get(jid)


# --------------------------------------------------------------------------

PID_FILE = ".lattice_pid"


def solver_command(cfg: SolverConfig, jobdir: str, name: str = "") -> list:
    """Build the subprocess argv that runs `run_aster run.export` inside jobdir."""
    if cfg.mode == "native":
        return shlex.split(cfg.cmd) + ["run.export"]
    if cfg.mode == "wsl":
        wsl_dir = win_to_wsl_path(jobdir)
        # the shell records its pid so a cancel can stop the tree under it:
        # ending wsl.exe on the Windows side does not end the Linux processes
        inner = (f"cd {shlex.quote(wsl_dir)} && echo $$ > {PID_FILE} && "
                 f"{cfg.cmd} run.export")
        return ["wsl.exe", "-d", cfg.wsl_distro, "--", "bash", "-lc", inner]
    if cfg.mode == "docker":
        # named, because stopping the docker client leaves the container running
        return ["docker", "run", "--rm"] + (["--name", name] if name else []) + [
                "-v", f"{jobdir}:/job", "-w", "/job",
                cfg.docker_image] + shlex.split(cfg.cmd) + ["run.export"]
    raise RuntimeError("No code_aster solver configured (demo mode). See README → Solver setup.")


def solver_cleanup(cfg: SolverConfig, jobdir: str, name: str = "") -> list:
    """Commands that stop a code_aster run this process cannot signal."""
    if cfg.mode == "wsl":
        pidfile = shlex.quote(win_to_wsl_path(jobdir) + "/" + PID_FILE)
        # -e, not --: without it the default shell expands $(…) and $1 in
        # the script before bash ever sees it
        return [["wsl.exe", "-d", cfg.wsl_distro, "-e", "bash", "-c",
                 _KILL_TREE.format(pidfile=pidfile)]]
    if cfg.mode == "docker" and name:
        return [["docker", "kill", name]]
    return []


def run_solver(cfg: SolverConfig, jobdir: str, job: Job) -> int:
    check_cancelled(job)
    name = f"lattice-{job.id}-{uuid.uuid4().hex[:4]}"
    argv = solver_command(cfg, jobdir, name)
    job.append(f"$ {' '.join(argv)}")
    logfile = os.path.join(jobdir, "log.txt")
    cwd = jobdir if cfg.mode == "native" else None
    rc = -1
    with open(logfile, "w", encoding="utf-8", errors="replace") as lf:
        proc = popen_isolated(argv, cleanup=solver_cleanup(cfg, jobdir, name),
                              cwd=cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace", bufsize=1)
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
    check_cancelled(job)
    return rc


def ccx_env(cfg: SolverConfig, base: dict = None) -> dict:
    """Environment CalculiX must be run under.

    ccx reads its thread count from the environment, not a flag, and this is
    deliberately NOT cfg.ncpus — see SolverConfig.ccx_threads. Multithreaded
    SPOOLES returns wrong answers with exit code 0, so anything that runs ccx,
    tests included, has to go through here rather than build its own env and
    quietly drift onto the broken path.
    """
    env = dict(os.environ if base is None else base)
    env["OMP_NUM_THREADS"] = str(max(1, int(cfg.ccx_threads)))
    env["CCX_NPROC_STIFFNESS"] = env["OMP_NUM_THREADS"]
    env["CCX_NPROC_EQUATION_SOLVER"] = env["OMP_NUM_THREADS"]
    return env


def run_ccx(cfg: SolverConfig, jobdir: str, job: Job, jobname: str = "job") -> int:
    """Run CalculiX in `jobdir`. ccx takes the deck name without .inp."""
    check_cancelled(job)
    argv = shlex.split(cfg.ccx_cmd) + ["-i", jobname]
    job.append(f"$ {' '.join(argv)}  (in {jobdir})")
    env = ccx_env(cfg)
    # ccx APPENDS to an existing .frd, so a stale one from a previous attempt
    # would be read back as if it were this run's answer.
    for ext in ("frd", "dat", "sta", "cvg", "12d"):
        stale = os.path.join(jobdir, f"{jobname}.{ext}")
        if os.path.exists(stale):
            try:
                os.unlink(stale)
            except OSError:
                pass
    logfile = os.path.join(jobdir, "log.txt")
    rc = -1
    with open(logfile, "w", encoding="utf-8", errors="replace") as lf:
        proc = popen_isolated(argv, cwd=jobdir, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              encoding="utf-8", errors="replace",
                              bufsize=1, env=env)
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
    check_cancelled(job)
    return rc


# code_aster prints its real complaint inside a banner and then a one-line
# diagnostic; gmsh and Python print a traceback. "exit code 1" on its own
# sends you looking through thousands of lines for the twenty that matter.
_ERROR_MARKERS = (
    # as_run's own status codes are "<F>_ERROR", "<S>_NO_RESULT_FILE" and the
    # like — underscore, no space.
    "<EXCEPTION>", "<F>_", "<S>_ERROR", "DIAGNOSTIC JOB",
    # code_aster's own messages in the .mess file are a different shape:
    # "<F> <FACTOR_10>", with a space and a message id. Only the runner's form
    # was matched, so the line that actually named the failure — a singular
    # matrix, a missing group — was never picked out of the log. <A> is a
    # warning and stays out of this list on purpose.
    "<F> <", "<E> <",
    # and with ERREUR_F='EXCEPTION' the same failure arrives as a Python
    # exception instead
    "AsterError", "aster.error",
    "Traceback (most recent call last)", "Error   :", "*ERROR",
    "ValueError", "RuntimeError", "erreur", "ERREUR",
    # OpenCASCADE's STEP/IGES readers print their diagnosis on lines of this
    # shape, and gmsh then prints its own generic "Could not read file". The
    # generic one used to be the whole message the user saw.
    "**** ERR", "Incorrect syntax", "Unknown entity",
)

# Lines that say only THAT something failed. Fine to keep in the log, useless
# as the headline when a more specific line is available.
_GENERIC_HEADLINES = (
    "Could not read file", "Unable to open file", "exit code",
)


# code_aster frames its messages in box-drawing characters. The frame is not
# part of the message.
_BOX_CHARS = "\u2500\u2502\u2550\u2551\u2552\u2555\u2558\u255b\u255e\u2561" \
             "\u250c\u2510\u2514\u2518\u251c\u2524|+-= \t"

# Lines from the deck this module generated, echoed back in the log. The
# DEBUT line carries ERREUR_F='EXCEPTION', which contains "ERREUR" and so
# matched the French-message marker — so the app reported its own deck line
# as the reason a run failed, with the real diagnosis further down.
_DECK_ECHO = ("ERREUR_F=", "=_F(", "DEBUT(", "FIN()", "_lattice_failure")


def _unbox(line: str) -> str:
    return line.strip().strip(_BOX_CHARS).strip()


def _is_deck_echo(text: str) -> bool:
    return any(k in text for k in _DECK_ECHO)


def headline(lines) -> "str|None":
    """The one line to put in front of the user from a failed run's output.

    Prefers a line that says what is wrong over one that says only that
    something is. A text file renamed .step reported "Could not read file
    '…/geometry.step'" while the line that actually diagnosed it — "Incorrect
    syntax: unexpected TYPE, expecting STEP" — sat further up the log.

    code_aster is the awkward one: it frames its diagnosis in box-drawing
    characters and puts the message id on one line and the human sentence two
    lines below, so neither line alone is the answer.
    """
    picked = [ln.rstrip() for ln in lines if ln.strip()]
    if not picked:
        return None
    clean = [_unbox(ln) for ln in picked]

    # 1. A framed code_aster exception. The id names the message and the
    #    sentence under it says which group, which node, which operator.
    for i, text in enumerate(clean):
        if "<EXCEPTION>" in text or text.startswith(("<F> <", "<E> <")):
            body = [c for c in clean[i + 1:i + 6] if c and not _is_deck_echo(c)]
            return f"{text} {body[0]}" if body else text

    # 2. Any other marked line that is not this module's own deck echoed back,
    #    and not a line that says only that something failed.
    flagged = [(c, ln) for c, ln in zip(clean, picked)
               if any(m in ln for m in _ERROR_MARKERS)]
    specific = [ln for c, ln in flagged
                if not _is_deck_echo(c)
                and not any(g in ln for g in _GENERIC_HEADLINES)
                and not c.startswith(("File \"", "Traceback"))]
    if specific:
        return specific[0]

    # 3. Nothing but traceback scaffolding. The useful line in a Python
    #    traceback is the LAST one, not the header.
    if any(c.startswith("Traceback") for c in clean):
        tail = [ln for c, ln in zip(clean, picked)
                if not c.startswith(("File \"", "Traceback"))
                and not ln.startswith(("    ", "\t"))
                and not _is_deck_echo(c)]
        if tail:
            return tail[-1]
    return ([ln for _c, ln in flagged] or picked)[0]


def extract_errors(lines, limit: int = 24) -> list:
    """The lines worth reading from a failed run's output.

    Returns the marker lines plus a little of the context around them, in
    order, capped — enough to see what went wrong without reprinting the log.
    """
    keep = set()
    for i, ln in enumerate(lines):
        if any(m in ln for m in _ERROR_MARKERS):
            for j in range(max(0, i - 1), min(len(lines), i + 6)):
                keep.add(j)
    if not keep:
        # nothing recognisable — the tail is the next best thing
        return [ln.rstrip() for ln in lines[-limit:] if ln.strip()]
    out = []
    prev = None
    for i in sorted(keep):
        if prev is not None and i > prev + 1:
            out.append("   …")
        out.append(lines[i].rstrip())
        prev = i
        if len(out) >= limit:
            out.append("   … (truncated; full output in the log file)")
            break
    return [x for x in out if x.strip()]


def summarise_failure(job, logfile: str, rc: int) -> str:
    """A failure message that says what happened and where to look."""
    lines = []
    try:
        with open(logfile, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        pass
    if not lines:
        with job._lock:
            lines = list(job.log)
    detail = extract_errors(lines)
    for ln in detail:
        job.append(ln)
    job.append(f"Full output: {logfile}")
    # The first line extract_errors returns is often context, not the
    # diagnosis — the same reason geometry import used to report "Could not
    # read file" instead of the parse error two lines above it.
    head = headline(detail) or f"exit code {rc}"
    return f"{head}  (exit {rc}; full output in {logfile})"
