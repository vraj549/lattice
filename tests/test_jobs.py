"""Stopping a run stops the solver, not just the process that launched it.

code_aster is launched through run_aster, which starts the solver as its own
child; under WSL or docker the solver is not a local process at all. A cancel
that signalled only the direct child left the solver running and writing into
the run directory the next run would use.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lattice_fea import solver  # noqa: E402
from lattice_fea.config import SolverConfig  # noqa: E402

posix = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def _running(tag: str) -> int:
    out = subprocess.run(["pgrep", "-f", tag], capture_output=True, text=True).stdout
    return len(out.split())


@posix
def test_cancel_stops_the_grandchildren():
    tag = f"sleep 31{os.getpid() % 10}"
    proc = solver.popen_isolated(["bash", "-c", f"{tag}7 & {tag}8 & wait"])
    try:
        time.sleep(0.5)
        assert _running(f"{tag}[78]") >= 2
        solver.terminate_tree(proc)
        time.sleep(0.3)
        assert _running(f"{tag}[78]") == 0, "the solver outlived its cancel"
    finally:
        solver.reap(proc)


@posix
def test_the_wsl_kill_script_takes_the_tree_under_the_recorded_pid(tmp_path):
    """The same script the WSL cleanup runs, against the pid file the WSL
    launch writes — exercised here with a local bash."""
    tag = f"sleep 32{os.getpid() % 10}"
    inner = (f"cd {shlex.quote(str(tmp_path))} && echo $$ > {solver.PID_FILE} && "
             f"bash -c '{tag}1 & {tag}2 & wait'")
    proc = solver.popen_isolated(["bash", "-c", inner])
    try:
        time.sleep(0.5)
        assert _running(f"{tag}[12]") >= 2
        pidfile = shlex.quote(str(tmp_path / solver.PID_FILE))
        subprocess.run(["bash", "-c", solver._KILL_TREE.format(pidfile=pidfile)],
                       timeout=10)
        time.sleep(0.3)
        assert _running(f"{tag}[12]") == 0
    finally:
        solver.terminate_tree(proc)
        solver.reap(proc)


def test_remote_runs_carry_a_way_to_stop_them():
    wsl = SolverConfig(mode="wsl", cmd="run_aster", wsl_distro="Ubuntu")
    argv = solver.solver_command(wsl, r"C:\work\run")
    assert solver.PID_FILE in argv[-1]
    stop = solver.solver_cleanup(wsl, r"C:\work\run")
    assert stop and stop[0][:4] == ["wsl.exe", "-d", "Ubuntu", "-e"]

    dock = SolverConfig(mode="docker", cmd="run_aster", docker_image="img")
    argv = solver.solver_command(dock, "/work/run", "lattice-x")
    assert argv[argv.index("--name") + 1] == "lattice-x"
    assert solver.solver_cleanup(dock, "/work/run", "lattice-x") == [
        ["docker", "kill", "lattice-x"]]

    assert solver.solver_cleanup(SolverConfig(mode="native", cmd="run_aster"), "/w") == []


@posix
def test_a_cancelled_job_launches_nothing_more(tmp_path):
    """The solve path has broad `except Exception` fallbacks — preload
    calibration treats any failure as "run uncorrected". A cancel that looked
    like a failure was absorbed there, and the job went on to start the main
    solve after the user had cancelled it."""
    cfg = SolverConfig(mode="native", cmd="bash -c 'sleep 5' x")
    launched = []

    def work(job):
        for _ in range(3):
            launched.append(1)
            try:
                solver.run_solver(cfg, str(tmp_path), job)
            except Exception:  # noqa: BLE001 — the fallback that swallowed it
                pass
        return "finished"

    jm = solver.JobManager()
    job = jm.submit("solve", "cancel test", work)
    time.sleep(0.5)
    job.cancel()
    t0 = time.time()
    while job.finished is None and time.time() - t0 < 10:
        time.sleep(0.05)
    assert job.status == "cancelled"
    assert len(launched) == 1, "a solver was started after the cancel"
    assert job.result is None
    assert time.time() - t0 < 4, "the cancel waited for the solver to finish"


def test_cancelling_a_finished_job_changes_nothing():
    jm = solver.JobManager()
    job = jm.submit("mesh", "done already", lambda job: "ok")
    t0 = time.time()
    while job.finished is None and time.time() - t0 < 5:
        time.sleep(0.01)
    job.cancel()
    assert job.status == "done"
