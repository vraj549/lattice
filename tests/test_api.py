"""End-to-end through the HTTP API, driving the mock solver.

The server layer had no tests. It is where the run is orchestrated — deck
written, calibration solved, deck rewritten, results parsed — and none of that
is exercised by writing a .comm and reading it back.
"""
import json
import os
import re
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

fastapi_testclient = pytest.importorskip("fastapi.testclient")
TestClient = fastapi_testclient.TestClient

from lattice_fea import comm_writer, mock_solver, server  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(__file__), "..", "examples")
PLATES = os.path.join(EXAMPLES, "bolted_plates.step")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(PLATES),
    reason="run examples/make_examples.py first")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    mock = os.path.join(os.path.dirname(server.__file__), "mock_solver.py")
    monkeypatch.setenv("LATTICE_ASTER_MODE", "native")
    monkeypatch.setenv("LATTICE_ASTER_CMD", f"{sys.executable} {mock}")
    ws = str(tmp_path / "ws")
    app = server.create_app(ws)
    with TestClient(app) as c:
        c.workspace = ws
        yield c


def wait(c, job, timeout=600):
    """Block until a job finishes; return its final state."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = c.get(f"/api/jobs/{job}").json()
        if r.get("status") in ("done", "failed", "cancelled"):
            return r
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def make_project(c):
    with open(PLATES, "rb") as fh:
        r = c.post("/api/projects", files={"step": ("plates.step", fh)},
                   data={"name": "plates", "assembly": "bonded"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert wait(c, out["job"])["status"] == "done"
    return out


def test_bolted_static_calibrates_the_preload(client):
    """The whole point: ask for 8000 N and the run must contain 8000 N.

    The mock solver takes 18% back the way a real joint does, so an
    uncalibrated run lands at 6560 N.
    """
    c = client
    proj = make_project(c)
    pid = proj["id"]
    p = c.get(f"/api/projects/{pid}").json()
    meta = p["geometry"]
    faces = meta["faces"]
    cyls = [f for f in faces if (f.get("fit") or {}).get("kind") == "cylinder"]
    assert len(cyls) >= 4
    by_z = sorted(cyls, key=lambda f: f["com"][2])
    flat = sorted((f for f in faces if (f.get("fit") or {}).get("kind") == "plane"),
                  key=lambda f: -f["area"])

    setup = p["setup"]
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210.0,
                           "nu": 0.3, "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    setup["bolts"] = [{
        "id": "b1", "name": "Bolt 1", "d_mm": 6, "E_GPa": 210,
        "preload_N": 8000,
        "side_a_faces": [by_z[-1]["tag"]], "side_b_faces": [by_z[0]["tag"]]}]
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Static", "config": {},
        "supports": [{"id": "s1", "name": "fix", "type": "fixed",
                      "faces": [flat[0]["tag"]]}],
        "loads": [{"id": "l1", "name": "pull", "type": "force",
                   "faces": [flat[1]["tag"]], "fx": 0, "fy": 0, "fz": 500}],
    }]
    r = c.put(f"/api/projects/{pid}/setup", json=setup)
    assert r.status_code == 200, r.text

    r = c.post(f"/api/projects/{pid}/mesh")
    assert r.status_code == 200, r.text
    assert wait(c, r.json()["job"])["status"] == "done"

    r = c.post(f"/api/projects/{pid}/solve/a1")
    assert r.status_code == 200, r.text
    st = wait(c, r.json()["job"])
    assert st["status"] == "done", json.dumps(st)[:3000]

    m = c.get(f"/api/projects/{pid}/results/a1").json()
    P = m.get("preload")
    assert P, "the run must report what preload it actually contains"
    assert P["calibrated"] is True, "\n".join(st.get("log") or [])[-3000:]
    assert P["requested"]["1"] == 8000.0
    assert P["achieved"]["1"] == pytest.approx(8000.0, rel=0.01)
    assert P["max_error"] <= 0.01

    # the correction is real: without it the mock lands 18% low
    assert 8000.0 * (1 - mock_solver.JOINT_SHARE) == pytest.approx(6560.0)

    # the calibration really ran as a separate solve, and the deck that ran
    # carries the corrected strain rather than the raw one
    run_dir = os.path.join(c.workspace, "projects", pid, "runs", "a1")
    assert os.path.isdir(os.path.join(run_dir, "calib"))
    raw = -8000.0 / (210000.0 * comm_writer.bolt_area(setup["bolts"][0]))
    eps = float(re.search(r"GROUP_MA=\('BOLT1',\), EPX=([-\d.eE+]+)",
                          open(os.path.join(run_dir, "run.comm")).read()).group(1))
    assert abs(eps) > abs(raw) * 1.1


def test_calibration_is_skipped_without_preload(client):
    """A sizing run has preload zero. It must not pay for extra solves."""
    c = client
    proj = make_project(c)
    pid = proj["id"]
    p = c.get(f"/api/projects/{pid}").json()
    meta = p["geometry"]
    faces = meta["faces"]
    cyls = sorted((f for f in faces if (f.get("fit") or {}).get("kind") == "cylinder"),
                  key=lambda f: f["com"][2])
    flat = sorted((f for f in faces if (f.get("fit") or {}).get("kind") == "plane"),
                  key=lambda f: -f["area"])
    setup = p["setup"]
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210.0,
                           "nu": 0.3, "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    setup["bolts"] = [{
        "id": "b1", "name": "Bolt 1", "d_mm": 6, "E_GPa": 210, "preload_N": 0,
        "side_a_faces": [cyls[-1]["tag"]], "side_b_faces": [cyls[0]["tag"]]}]
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Static", "config": {},
        "supports": [{"id": "s1", "name": "fix", "type": "fixed",
                      "faces": [flat[0]["tag"]]}],
        "loads": [{"id": "l1", "name": "pull", "type": "force",
                   "faces": [flat[1]["tag"]], "fx": 0, "fy": 0, "fz": 500}],
    }]
    assert c.put(f"/api/projects/{pid}/setup", json=setup).status_code == 200
    r = c.post(f"/api/projects/{pid}/mesh")
    assert wait(c, r.json()["job"])["status"] == "done"
    r = c.post(f"/api/projects/{pid}/solve/a1")
    assert wait(c, r.json()["job"])["status"] == "done"
    m = c.get(f"/api/projects/{pid}/results/a1").json()
    assert "preload" not in m
