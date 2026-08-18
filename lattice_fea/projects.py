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
        """Project directory, refusing anything that escapes the workspace.

        A prefix test is not enough: with root /ws/projects, the id
        "../projects-x" resolves to /ws/projects-x, which passes startswith.
        Compare against root + separator, and reject separators in the id
        outright — ids are generated here and never contain one.
        """
        if not pid or os.sep in pid or "/" in pid or pid in (".", ".."):
            raise ValueError("bad project id")
        d = os.path.abspath(os.path.join(self.root, pid))
        if d != self.root and not d.startswith(self.root + os.sep):
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
            data = json.load(f)
        if isinstance(data.get("setup"), dict):
            data["setup"] = migrate_setup(data["setup"])
        return data

    def save(self, pid: str, data: dict) -> None:
        self.write_json(pid, "project.json", data)

    # ---- io helpers ----
    def write_json(self, pid: str, rel: str, data) -> None:
        p = self.path(pid, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        # unique temp name: two concurrent writers sharing one ".tmp" would
        # interleave and the loser would publish a truncated file
        tmp = f"{p}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, p)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def read_json(self, pid: str, rel: str):
        with open(self.path(pid, rel), encoding="utf-8") as f:
            return json.load(f)

    def write_json_gz(self, pid: str, rel: str, data) -> None:
        p = self.path(pid, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.{uuid.uuid4().hex[:8]}.tmp"
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, p)          # atomic, like write_json
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def read_json_gz(self, pid: str, rel: str):
        with gzip.open(self.path(pid, rel), "rt", encoding="utf-8") as f:
            return json.load(f)

    def exists(self, pid: str, rel: str) -> bool:
        return os.path.isfile(self.path(pid, rel))


def default_setup() -> dict:
    """Shared model data, plus a list of analyses that each own their own
    supports and loads.

    Boundary conditions belong to an analysis, not the model: a modal run
    needs supports and no loads, a static run needs both, a random run needs
    supports and a spectrum. Scoping them per analysis means each one asks
    only for what it actually uses — the structure Ansys and SimScale use.

    Shared (defined once, used by every analysis): geometry, materials,
    bolts, ties, probes, mesh.
    """
    return {
        "materials": [],        # [{id, name, E_GPa, nu, rho_kgm3, yield_MPa}]
        "assignments": {},      # {solidTag(str): materialId}
        "bolts": [],            # [{id, name, side_a_faces:[], side_b_faces:[],
                                #   size, d_mm, as_mm2, grade, yield_MPa, E_GPa, preload_N}]
        "ties": [],             # [{id, name, slave_faces:[], master_solid:int}]
        # [{id, name, kind, mu, faces_a:[], faces_b:[], solids:[a,b], suppressed}]
        # kind: bonded | noseparation | frictionless | friction
        "contacts": [],
        "probes": [],           # [{id, name, x,y,z}]  (mm)
        "mesh": {"size_mm": None, "curvature": 16, "order": 2, "local": []},
        "analyses": [],         # each: {id, type, name, config, supports[], loads[]}
    }


def migrate_setup(setup: dict) -> dict:
    """Move model-level supports/loads into each analysis (pre-0.9 projects).

    Old projects applied one global set of BCs to every analysis. Copy them
    into each analysis so nothing is lost, then drop the globals.
    """
    if "supports" not in setup and "loads" not in setup:
        return setup
    g_sup = setup.pop("supports", []) or []
    g_load = setup.pop("loads", []) or []
    for a in setup.get("analyses", []):
        if not a.get("supports"):
            a["supports"] = [dict(s) for s in g_sup]
        if not a.get("loads") and a.get("type") != "modal":
            a["loads"] = [dict(l) for l in g_load]
        a.setdefault("supports", [])
        a.setdefault("loads", [])
    return setup
