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
        # Assumptions for VDI 2230 bolt sizing. Not solver inputs — they are
        # what turns an FE bolt load into a required preload.
        "bolt_sizing": {
            "mu_joint": 0.15,       # faying-surface friction
            "n_friction": 1,        # interfaces carrying transverse load
            "mu_thread": 0.14, "mu_head": 0.14,
            "tightening": "torque_wrench",
            "embedding_um": 6.0,    # bedding-in loss across the joint
            "n_intro": 0.5,         # where the load enters the clamped parts
            "S_slip": 1.2, "S_gap": 1.2,
            "nu_yield": 0.9,        # utilisation of yield at assembly
            "p_G": None,            # clamped-material bearing limit, MPa
        },
        "probes": [],           # [{id, name, x,y,z}]  (mm)
        # solid names live here, keyed by tag, next to the material
        # assignments that are already keyed the same way
        "solid_names": {},
        "mesh": {"size_mm": None, "curvature": 16, "order": 2,
                 "elements": "tet", "local": []},
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


# --------------------------------------------------------------- validation

# Values that decide physics. A typo in any of these does not fail — it
# quietly changes the model: an unrecognised support type falls through to
# "prescribed displacement with no components" and holds nothing, an
# unrecognised load type matches no branch and is never applied, and an
# unrecognised contact kind is written as a plain contact zone with no
# friction. Each one produces a run that completes, reports numbers, and
# describes a different structure from the one on screen.
CONTACT_KINDS = ("bonded", "noseparation", "frictionless", "friction")
CONTACT_SOLVE = ("linear", "nonlinear")
SUPPORT_TYPES = ("fixed", "frictionless", "disp")
LOAD_TYPES = ("force", "pressure", "remote", "gravity", "rotation")
ANALYSIS_TYPES = ("static", "modal", "harmonic", "random", "shock")


class SetupInvalid(ValueError):
    """The setup names something the solvers do not implement."""


def _one_of(value, allowed, what: str, where: str) -> None:
    if value in allowed:
        return
    raise SetupInvalid(
        f"{where}: {what} '{value}' is not one of "
        f"{', '.join(repr(a) for a in allowed)}.")


def validate_setup(setup: dict) -> None:
    """Refuse a setup that would be silently mis-solved. Raises SetupInvalid.

    Deliberately narrow: only the enumerated fields that change what is
    solved, and only the numeric fields where an out-of-range value is
    absorbed rather than rejected. Everything else stays permissive, because
    the setup document is written by the UI and by scripts and a schema that
    rejects unknown keys would break both on every addition.
    """
    for i, c in enumerate(setup.get("contacts") or [], 1):
        where = f"contact {i} ('{c.get('name', '')}')"
        _one_of(c.get("kind", "bonded"), CONTACT_KINDS, "behaviour", where)
        if c.get("solve") is not None:
            _one_of(c["solve"], CONTACT_SOLVE, "solve mode", where)

    for ai, a in enumerate(setup.get("analyses") or [], 1):
        aname = a.get("name") or f"analysis {ai}"
        _one_of(a.get("type"), ANALYSIS_TYPES, "analysis type", aname)
        for i, s in enumerate(a.get("supports") or [], 1):
            _one_of(s.get("type", "fixed"), SUPPORT_TYPES, "support type",
                    f"{aname} / support {i} ('{s.get('name', '')}')")
        for i, l in enumerate(a.get("loads") or [], 1):
            _one_of(l.get("type"), LOAD_TYPES, "load type",
                    f"{aname} / load {i} ('{l.get('name', '')}')")

    size = (setup.get("mesh") or {}).get("size_mm")
    if size is not None and size != "":
        try:
            size = float(size)
        except (TypeError, ValueError):
            raise SetupInvalid(f"mesh: element size '{size}' is not a number.")
        if size <= 0.0:
            # `size_mm or diag/25` treats 0 as "unset" and meshes at the
            # automatic size, so a user who typed 0 got a mesh and no hint
            # that the number had been ignored.
            raise SetupInvalid(
                "mesh: element size must be greater than zero. Clear the "
                "field to let Lattice choose a size from the model.")
