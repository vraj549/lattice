"""Solver detection and configuration.

Resolution order: environment variables > lattice.toml (repo root or workspace) > auto-detect.

Env vars:
  LATTICE_ASTER_MODE   = native | wsl | docker | none
  LATTICE_ASTER_CMD    = command used to invoke run_aster (default "run_aster")
  LATTICE_WSL_DISTRO   = WSL distribution name (e.g. "smeca-2024")
  LATTICE_DOCKER_IMAGE = docker image with run_aster on PATH
  LATTICE_MEMORY_MB    = code_aster memory_limit (MB)
  LATTICE_TIME_LIMIT_S = per-run time limit (s)
  LATTICE_NCPUS        = OpenMP threads for the solver
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

try:  # py311+
    import tomllib
except ImportError:  # pragma: no cover
    tomllib = None


def _run(cmd, timeout=15) -> "tuple[int, str]":
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 999, str(e)


def host_ram_mb() -> "int|None":
    """Total physical RAM, without adding a dependency."""
    try:
        if platform.system() == "Windows":
            import ctypes

            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            st = MS()
            st.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
            return int(st.ullTotalPhys / 1048576)
        if platform.system() == "Darwin":
            rc, out = _run(["sysctl", "-n", "hw.memsize"])
            return int(int(out.strip()) / 1048576) if rc == 0 else None
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(int(line.split()[1]) / 1024)
    except Exception:  # noqa: BLE001
        return None
    return None


def wsl_ram_mb(distro: str) -> "int|None":
    """RAM available *inside* the WSL VM — the real ceiling for a WSL solve.
    WSL2 defaults to half the host's RAM, so this is usually the binding
    number rather than the machine's total."""
    if not distro:
        return None
    rc, out = _run(["wsl.exe", "-d", distro, "--", "cat", "/proc/meminfo"], timeout=20)
    if rc != 0:
        return None
    for line in out.replace("\x00", "").splitlines():
        if line.startswith("MemTotal:"):
            try:
                return int(int(line.split()[1]) / 1024)
            except Exception:  # noqa: BLE001
                return None
    return None


@dataclass
class SolverConfig:
    mode: str = "none"              # native | wsl | docker | none
    cmd: str = "run_aster"
    wsl_distro: str = ""
    docker_image: str = "codeastersolver/codeaster-seq:latest"
    memory_mb: int = 6000
    time_limit_s: int = 14400
    ncpus: int = max(1, (os.cpu_count() or 4) - 2)
    detail: str = ""
    notes: list = field(default_factory=list)
    host_cores: int = field(default_factory=lambda: os.cpu_count() or 0)
    host_ram_mb: int = 0
    vm_ram_mb: int = 0          # WSL VM total, when running through WSL

    def available(self) -> bool:
        return self.mode in ("native", "wsl", "docker")

    def is_demo(self) -> bool:
        return "mock_solver" in self.cmd

    def as_dict(self) -> dict:
        return {
            "demo": self.is_demo(),
            "mode": self.mode, "cmd": self.cmd, "wsl_distro": self.wsl_distro,
            "docker_image": self.docker_image, "memory_mb": self.memory_mb,
            "time_limit_s": self.time_limit_s, "ncpus": self.ncpus,
            "available": self.available(), "detail": self.detail, "notes": self.notes,
            "host_cores": self.host_cores, "host_ram_mb": self.host_ram_mb,
            "vm_ram_mb": self.vm_ram_mb,
        }


def _load_toml(paths) -> dict:
    if tomllib is None:
        return {}
    for p in paths:
        try:
            with open(p, "rb") as f:
                data = tomllib.load(f)
            return data.get("solver", {})
        except FileNotFoundError:
            continue
        except Exception as e:  # noqa: BLE001
            print(f"[lattice] warning: could not parse {p}: {e}", file=sys.stderr)
    return {}


def _wsl_distros() -> list:
    rc, out = _run(["wsl.exe", "-l", "-q"])
    if rc != 0:
        return []
    # wsl -l -q output is UTF-16 on some hosts; normalise embedded NULs
    out = out.replace("\x00", "")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _wsl_has_cmd(distro: str, cmd: str) -> bool:
    rc, _ = _run(["wsl.exe", "-d", distro, "--", "bash", "-lc",
                  f"command -v {cmd.split()[0]} >/dev/null 2>&1"], timeout=30)
    return rc == 0


def _probe_resources(cfg: "SolverConfig") -> "SolverConfig":
    cfg.host_ram_mb = host_ram_mb() or 0
    if cfg.mode == "wsl":
        cfg.vm_ram_mb = wsl_ram_mb(cfg.wsl_distro) or 0
    return cfg


def detect(workspace: str = ".") -> SolverConfig:
    cfg = SolverConfig()
    file_cfg = _load_toml([os.path.join(workspace, "lattice.toml"), "lattice.toml"])
    for k_file, k_attr in [("mode", "mode"), ("cmd", "cmd"), ("wsl_distro", "wsl_distro"),
                           ("docker_image", "docker_image"), ("memory_mb", "memory_mb"),
                           ("time_limit_s", "time_limit_s"), ("ncpus", "ncpus")]:
        if k_file in file_cfg:
            setattr(cfg, k_attr, file_cfg[k_file])

    env = os.environ
    cfg.mode = env.get("LATTICE_ASTER_MODE", cfg.mode)
    cfg.cmd = env.get("LATTICE_ASTER_CMD", cfg.cmd)
    cfg.wsl_distro = env.get("LATTICE_WSL_DISTRO", cfg.wsl_distro)
    cfg.docker_image = env.get("LATTICE_DOCKER_IMAGE", cfg.docker_image)
    cfg.memory_mb = int(env.get("LATTICE_MEMORY_MB", cfg.memory_mb))
    cfg.time_limit_s = int(env.get("LATTICE_TIME_LIMIT_S", cfg.time_limit_s))
    cfg.ncpus = int(env.get("LATTICE_NCPUS", cfg.ncpus))

    explicitly_set = cfg.mode != "none" or "LATTICE_ASTER_MODE" in env or "mode" in file_cfg
    if explicitly_set and cfg.mode != "none":
        cfg.detail = f"configured: {cfg.mode}"
        return _probe_resources(cfg)

    # --- auto-detect ---
    if shutil.which(cfg.cmd.split()[0]):
        cfg.mode = "native"
        cfg.detail = f"found `{cfg.cmd}` on PATH"
        return _probe_resources(cfg)

    if platform.system() == "Windows" or shutil.which("wsl.exe"):
        distros = _wsl_distros()
        preferred = [cfg.wsl_distro] if cfg.wsl_distro else []
        for d in preferred + [x for x in distros if "meca" in x.lower() or "aster" in x.lower()] + distros:
            if d and _wsl_has_cmd(d, cfg.cmd):
                cfg.mode, cfg.wsl_distro = "wsl", d
                cfg.detail = f"found `{cfg.cmd}` in WSL distro `{d}`"
                return _probe_resources(cfg)
        if distros:
            cfg.notes.append(
                f"WSL distros found ({', '.join(distros)}) but none expose `{cfg.cmd}`. "
                "Install the Salome-Meca WSL2 distribution (see README) or set LATTICE_ASTER_CMD.")

    if shutil.which("docker"):
        rc, _ = _run(["docker", "image", "inspect", cfg.docker_image], timeout=20)
        if rc == 0:
            cfg.mode = "docker"
            cfg.detail = f"docker image `{cfg.docker_image}` present"
            return _probe_resources(cfg)
        cfg.notes.append(
            f"Docker is installed but image `{cfg.docker_image}` is not pulled. "
            f"Run: docker pull {cfg.docker_image}  (then restart Lattice)")

    cfg.mode = "none"
    cfg.detail = "no code_aster found — running in geometry/mesh-only demo mode"
    return _probe_resources(cfg)


def win_to_wsl_path(p: str) -> str:
    """C:\\foo\\bar -> /mnt/c/foo/bar"""
    p = p.replace("\\", "/")
    if len(p) > 2 and p[1] == ":":
        p = f"/mnt/{p[0].lower()}{p[2:]}"
    return p
