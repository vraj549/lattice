"""Disk-backed project store.

workspace/projects/<id>/
    project.json     setup + geometry metadata
    geometry.step    original upload
    geometry.brep    canonical (post-fragment) geometry — all tags refer to this
    tess.json.gz     display tessellation cache
    mesh/            mesh.unv, stats.json, skin.json.gz
    runs/<aid>/      run.comm, run.export, result.med, *.csv, log.txt, parsed/
"""
from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
import uuid

_LOCK = threading.Lock()


def _slug(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", name).strip("-")
    return s[:40] or "project"


class ProjectStore:
    def __init__(self, workspace: str):
        self.root = os.path.abspath(os.path.join(workspace, "projects"))
        os.makedirs(self.root, exist_ok=True)

    # ---- paths ----
    def dir(self, pid: str) -> str:
        d = os.path.abspath(os.path.join(self.root, pid))
        if not d.startswith(self.root):
            raise ValueError("bad project id")
        return d

    def path(self, pid: str, *parts: str) -> str:
        return os.path.join(self.dir(pid), *parts)

    # ---- crud ----
    def create(self, name: str) -> str:
        pid = f"{_slug(name)}-{uuid.uuid4().hex[:6]}"
        os.makedirs(self.path(pid, "runs"), exist_ok=True)
        os.makedirs(self.path(pid, "mesh"), exist_ok=True)
        self.write_json(pid, "project.json", {
            "id": pid, "name": name, "created": time.time(), "units": "mm",
            "geometry": None,          # filled after import
            "setup": default_setup(),
        })
        return pid

    def list(self) -> list:
        out = []
        for pid in sorted(os.listdir(self.root)):
            pj = os.path.join(self.root, pid, "project.json")
            if os.path.isfile(pj):
                try:
                    with open(pj, encoding="utf-8") as f:
                        j = json.load(f)
                    out.append({"id": pid, "name": j.get("name", pid),
                                "created": j.get("created"),
                                "has_geometry": bool(j.get("geometry"))})
                except Exception:  # noqa: BLE001
                    continue
        out.sort(key=lambda x: -(x.get("created") or 0))
        return out

    def load(self, pid: str) -> dict:
        with open(self.path(pid, "project.json"), encoding="utf-8") as f:
            return json.load(f)

    def save(self, pid: str, data: dict) -> None:
        self.write_json(pid, "project.json", data)

    # ---- io helpers ----
    def write_json(self, pid: str, rel: str, data) -> None:
        p = self.path(pid, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with _LOCK:
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, p)

    def read_json(self, pid: str, rel: str):
        with open(self.path(pid, rel), encoding="utf-8") as f:
            return json.load(f)

    def write_json_gz(self, pid: str, rel: str, data) -> None:
        p = self.path(pid, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with gzip.open(p, "wt", encoding="utf-8") as f:
            json.dump(data, f)

    def read_json_gz(self, pid: str, rel: str):
        with gzip.open(self.path(pid, rel), "rt", encoding="utf-8") as f:
            return json.load(f)

    def exists(self, pid: str, rel: str) -> bool:
        return os.path.isfile(self.path(pid, rel))


def default_setup() -> dict:
    return {
        "materials": [],        # [{id, name, E_GPa, nu, rho_kgm3, yield_MPa}]
        "assignments": {},      # {solidTag(str): materialId}
        "supports": [],         # [{id, name, type:'fixed', faces:[int]}]
        "loads": [],            # [{id, name, type:'force'|'pressure'|'gravity', faces, fx,fy,fz | pressure | g:[gx,gy,gz]}]
        "bolts": [],            # [{id, name, side_a_faces:[], side_b_faces:[], d_mm, E_GPa, preload_N}]
        "ties": [],             # [{id, name, slave_faces:[], master_solid:int}]
        "mesh": {"size_mm": None, "curvature": 16, "order": 2, "local": []},
        "probes": [],           # [{id, name, x,y,z}]  (mm)
        "analyses": [],         # see comm_writer for per-type config
    }
