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


def server_jobs(c):
    """The live JobManager behind this client."""
    return c.app.state.jobs


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


def _bolted_project(c, preload_N):
    """A two-plate bolted joint set up through the API, meshed and solved."""
    proj = make_project(c)
    pid = proj["id"]
    p = c.get(f"/api/projects/{pid}").json()
    meta = p["geometry"]
    cyls = sorted((f for f in meta["faces"]
                   if (f.get("fit") or {}).get("kind") == "cylinder"),
                  key=lambda f: f["com"][2])
    flat = sorted((f for f in meta["faces"]
                   if (f.get("fit") or {}).get("kind") == "plane"),
                  key=lambda f: -f["area"])
    setup = p["setup"]
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210.0,
                           "nu": 0.3, "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    setup["bolts"] = [{
        "id": "b1", "name": "Bolt 1", "size": "M6", "d_mm": 6, "E_GPa": 210,
        "yield_MPa": 640, "preload_N": preload_N,
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
    return pid, meta


def test_sizing_uses_the_measured_clamped_length(client):
    """The grip the sizing calculates with is the one the mesh measured, and
    for two 8 mm plates that is 16 mm — not the 8 mm the centroids gave."""
    c = client
    pid, meta = _bolted_project(c, preload_N=0)
    r = c.get(f"/api/projects/{pid}/results/a1/bolt-sizing")
    assert r.status_code == 200, r.text
    out = r.json()
    assert not out.get("blocked"), out
    assert out["rows"], out
    row = out["rows"][0]
    zs = [f["com"][2] for f in meta["faces"]]
    assert row["grip_mm"] == pytest.approx(max(zs) - min(zs), abs=0.05)


def test_sizing_is_refused_once_preload_is_applied(client):
    """The beam force in a preloaded run is the bolt force, not the external
    load; feeding it back would count the preload twice and ask for several
    times the preload the joint needs — so it is refused, not tabulated."""
    c = client
    pid, _ = _bolted_project(c, preload_N=8000)
    out = c.get(f"/api/projects/{pid}/results/a1/bolt-sizing").json()
    assert out["blocked"] is True
    assert out["rows"] == []
    assert out["warnings"]


def _shock_project(c, cfg_extra=None, with_probe=True):
    """A bolted joint with a shock analysis, meshed and solved."""
    proj = make_project(c)
    pid = proj["id"]
    p = c.get(f"/api/projects/{pid}").json()
    meta = p["geometry"]
    cyls = sorted((f for f in meta["faces"]
                   if (f.get("fit") or {}).get("kind") == "cylinder"),
                  key=lambda f: f["com"][2])
    flat = sorted((f for f in meta["faces"]
                   if (f.get("fit") or {}).get("kind") == "plane"),
                  key=lambda f: -f["area"])
    setup = p["setup"]
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210.0,
                           "nu": 0.3, "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    setup["bolts"] = [{
        "id": "b1", "name": "Bolt 1", "size": "M6", "d_mm": 6, "E_GPa": 210,
        "yield_MPa": 640, "preload_N": 0,
        "side_a_faces": [cyls[-1]["tag"]], "side_b_faces": [cyls[0]["tag"]]}]
    if with_probe:
        com = flat[1]["com"]
        setup["probes"] = [{"id": "p1", "name": "tip",
                            "x": com[0], "y": com[1], "z": com[2]}]
    setup["analyses"] = [{
        "id": "a1", "type": "shock", "name": "Shock",
        "config": {"input": "pulse", "pulse": "half_sine", "pulse_g": 20,
                   "pulse_ms": 11, "axis": 2, "rule": "srss",
                   "damping": 0.05, "n_modes": 8, **(cfg_extra or {})},
        "supports": [{"id": "s1", "name": "fix", "type": "fixed",
                      "faces": [flat[0]["tag"]]}],
        "loads": []}]
    assert c.put(f"/api/projects/{pid}/setup", json=setup).status_code == 200
    r = c.post(f"/api/projects/{pid}/mesh")
    m = wait(c, r.json()["job"])
    assert m["status"] == "done", json.dumps(m)[:3000]
    r = c.post(f"/api/projects/{pid}/solve/a1")
    assert r.status_code == 200, r.text
    st = wait(c, r.json()["job"])
    assert st["status"] == "done", json.dumps(st)[:3000]
    return pid


def test_shock_run_reports_interface_load_and_bolt_loads(client):
    c = client
    pid = _shock_project(c)
    r = c.get(f"/api/projects/{pid}/results/a1/shock")
    assert r.status_code == 200, r.text
    out = r.json()

    assert out["axis"] == "Z"
    assert out["rows"], out
    # every mode gets a spectrum value, and the pulse's peak bounds none of
    # them from above — an SRS amplifies
    assert all(m["srs_g"] > 0 for m in out["rows"])
    assert max(m["srs_g"] for m in out["rows"]) > 20.0

    # interface load is a real force, and the missing mass is carried
    assert out["force_N"] > out["force_modal_N"] > 0
    assert 0.0 < out["missing_mass"] < 0.5
    assert out["missing_force_N"] > 0
    assert out["bolts"], "a bolted model must report peak bolt loads"
    assert out["bolts"][0]["N"] > 0
    assert out["probes"], "a probed model must report peak displacement"


def test_shock_combination_rule_changes_the_answer(client):
    """The rule is a real choice, not a label: SRSS < NRL < ABS."""
    c = client
    pid = _shock_project(c)
    got = {}
    for rule in ("srss", "nrl", "abs"):
        p = c.get(f"/api/projects/{pid}").json()
        p["setup"]["analyses"][0]["config"]["rule"] = rule
        assert c.put(f"/api/projects/{pid}/setup", json=p["setup"]).status_code == 200
        got[rule] = c.get(f"/api/projects/{pid}/results/a1/shock").json()["force_N"]
    assert got["srss"] < got["nrl"] < got["abs"]


def test_shock_spectrum_and_pulse_inputs_both_run(client):
    c = client
    pid = _shock_project(c, {"input": "spectrum",
                             "spec": [[100, 20], [1000, 200], [10000, 200]]})
    out = c.get(f"/api/projects/{pid}/results/a1/shock").json()
    assert out["input"]["source"] == "SRS table"
    assert out["input"]["zpa"] == 200.0
    assert out["force_N"] > 0


def test_shock_export_carries_the_numbers(client):
    c = client
    pid = _shock_project(c)
    r = c.get(f"/api/projects/{pid}/results/a1/export?what=shock")
    assert r.status_code == 200
    body = r.text
    for header in ("# shock summary", "# shock per mode",
                   "# shock peak bolt loads",
                   "# shock peak displacement at probes"):
        assert header in body, body[:1500]


# ----------------------------------------- provenance of a fabricated result

def test_a_demo_run_still_says_so_after_a_real_solver_is_installed(client, tmp_path,
                                                                   monkeypatch):
    """The red session banner is driven by the live config, so it disappears
    the moment the server restarts without --demo-solver. The fabricated
    numbers stay in the workspace. Provenance has to live with the run.
    """
    c = client
    pid = _shock_project(c)
    meta = c.get(f"/api/projects/{pid}/results/a1").json()
    assert meta["demo"] is True
    # recorded once, as the flag every surface reads — not duplicated into
    # the warnings list, where it printed under a banner that already said it
    assert not any("DEMO SOLVER" in w for w in meta.get("warnings", []))

    # Same workspace, a server that knows nothing about the mock: this is what
    # reopening the project tomorrow, or after installing code_aster, looks like.
    monkeypatch.setenv("LATTICE_ASTER_MODE", "none")
    monkeypatch.delenv("LATTICE_ASTER_CMD", raising=False)
    app2 = server.create_app(c.workspace)
    with TestClient(app2) as c2:
        assert c2.get("/api/config").json()["solver"]["demo"] is False
        again = c2.get(f"/api/projects/{pid}/results/a1").json()
        assert again["demo"] is True, "the run forgot it was fabricated"
        body = c2.get(f"/api/projects/{pid}/results/a1/export?what=all").text
        assert "DEMO SOLVER" in body.split("\n", 4)[1], body[:400]


def test_a_real_run_is_not_labelled_demo(client, monkeypatch):
    """The label has to discriminate, or it is noise people learn to ignore."""
    c = client
    pid = _shock_project(c)
    path = os.path.join(c.workspace, "projects", pid, "runs", "a1", "meta.json")
    with open(path) as fh:
        meta = json.load(fh)
    meta["demo"] = False
    with open(path, "w") as fh:
        json.dump(meta, fh)
    body = c.get(f"/api/projects/{pid}/results/a1/export?what=all").text
    assert "DEMO SOLVER" not in body


# ---------------------------------------------- the setup document is checked

@pytest.mark.parametrize("patch,fragment", [
    ({"contacts": [{"id": "c", "name": "p/p", "kind": "frictional",
                    "faces_a": [1], "faces_b": [2], "solids": [1, 2]}]},
     "behaviour 'frictional'"),
    ({"contacts": [{"id": "c", "kind": "friction", "solve": "sort-of",
                    "faces_a": [1], "faces_b": [2], "solids": [1, 2]}]},
     "solve mode 'sort-of'"),
    ({"mesh": {"size_mm": 0}}, "greater than zero"),
])
def test_a_typo_that_changes_the_physics_is_refused(client, patch, fragment):
    """Every writer treats an unrecognised enum as a default rather than an
    error: an unknown contact kind becomes a plain contact zone with no
    friction, an unknown support type becomes a prescribed displacement
    holding nothing, an unknown load type matches no branch and is never
    applied, and a mesh size of 0 means "unset" so the model meshes at the
    automatic size. All four finish, report numbers, and describe a different
    structure than the one on screen.
    """
    c = client
    pid = make_project(c)["id"]
    setup = c.get(f"/api/projects/{pid}").json()["setup"]
    setup.update(patch)
    r = c.put(f"/api/projects/{pid}/setup", json=setup)
    assert r.status_code == 422, r.text
    assert fragment in r.text, r.text


@pytest.mark.parametrize("bad,fragment", [
    ({"type": "fixxed", "name": "base", "faces": [1], "id": "s"}, "support type"),
    ({"type": "fixed", "name": "base", "faces": [1], "id": "s"}, None),
])
def test_support_and_load_types_are_checked_per_analysis(client, bad, fragment):
    c = client
    pid = make_project(c)["id"]
    setup = c.get(f"/api/projects/{pid}").json()["setup"]
    setup["analyses"] = [{"id": "a1", "type": "static", "name": "Static",
                          "config": {}, "supports": [bad], "loads": []}]
    r = c.put(f"/api/projects/{pid}/setup", json=setup)
    if fragment is None:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 422 and fragment in r.text, r.text


def test_a_valid_setup_still_saves(client):
    """Validation that rejects legitimate models is worse than none."""
    c = client
    pid = make_project(c)["id"]
    setup = c.get(f"/api/projects/{pid}").json()["setup"]
    for kind in ("bonded", "noseparation", "frictionless", "friction"):
        setup["contacts"] = [{"id": "c", "name": "p/p", "kind": kind,
                              "faces_a": [1], "faces_b": [2], "solids": [1, 2]}]
        assert c.put(f"/api/projects/{pid}/setup", json=setup).status_code == 200, kind


# ------------------------------- the mesh is one shared file, not per analysis

def test_remeshing_is_refused_while_a_solve_is_reading_the_mesh(client, monkeypatch):
    """A solve reads mesh.unv for its whole life — the preload calibration
    re-copies it part-way through — so a re-mesh during one gives a preload
    calibrated on one discretisation and applied to another, at exit code 0.

    Solves of different analyses share nothing and must still run together.
    """
    c = client
    pid = _shock_project(c)

    class FakeJob:
        status, kind, key, label = "running", "solve", f"{pid}/a1", "shock a1"

    jobs = server_jobs(c)
    jobs.jobs["fake"] = FakeJob()
    try:
        r = c.post(f"/api/projects/{pid}/mesh")
        assert r.status_code == 409, r.text
        assert "mesh" in r.text.lower()
    finally:
        jobs.jobs.pop("fake", None)

    # and with nothing running it is allowed again
    r = c.post(f"/api/projects/{pid}/mesh")
    assert r.status_code == 200, r.text
    assert wait(c, r.json()["job"])["status"] == "done"


def test_solving_is_refused_while_the_mesh_is_being_written(client):
    c = client
    pid = _shock_project(c)

    class FakeMesh:
        status, kind, key, label = "running", "mesh", pid, f"mesh {pid}"

    jobs = server_jobs(c)
    jobs.jobs["fakemesh"] = FakeMesh()
    try:
        r = c.post(f"/api/projects/{pid}/solve/a1")
        assert r.status_code == 409, r.text
    finally:
        jobs.jobs.pop("fakemesh", None)


def test_two_analyses_may_solve_at_once(client):
    """Different analyses write different run directories and only read the
    mesh. Serialising them would be a real cost for no reason."""
    c = client
    pid = _shock_project(c)

    class FakeOther:
        status, kind, key, label = "running", "solve", f"{pid}/a2", "static a2"

    jobs = server_jobs(c)
    jobs.jobs["other"] = FakeOther()
    try:
        r = c.post(f"/api/projects/{pid}/solve/a1")
        assert r.status_code == 200, r.text
        assert wait(c, r.json()["job"])["status"] == "done"
    finally:
        jobs.jobs.pop("other", None)


def test_sizing_follows_the_bolt_not_its_position_in_the_list(client):
    """Bolt forces come back keyed by the BOLT<k> group the mesh created, and
    k was the bolt's position when the mesh was written. The table joined on
    its position NOW, so deleting a bolt after solving shifted every later one
    down and sized it against its neighbour's forces.

    The two bolts here are given deliberately different diameters, so a
    mis-join is visible in the answer rather than only in the bookkeeping.
    """
    c = client
    proj = make_project(c)
    pid = proj["id"]
    p = c.get(f"/api/projects/{pid}").json()
    meta = p["geometry"]
    cyls = sorted((f for f in meta["faces"]
                   if (f.get("fit") or {}).get("kind") == "cylinder"),
                  key=lambda f: f["com"][2])
    flat = sorted((f for f in meta["faces"]
                   if (f.get("fit") or {}).get("kind") == "plane"),
                  key=lambda f: -f["area"])
    assert len(cyls) >= 4, "this fixture needs two bolt holes"

    setup = p["setup"]
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210.0,
                           "nu": 0.3, "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    tops = [f for f in cyls if f["com"][2] == cyls[-1]["com"][2]]
    bots = [f for f in cyls if f["com"][2] == cyls[0]["com"][2]]
    assert len(tops) >= 2 and len(bots) >= 2
    setup["bolts"] = [
        {"id": "b1", "name": "Bolt 1", "size": "M6", "d_mm": 6, "E_GPa": 210,
         "yield_MPa": 640, "preload_N": 0,
         "side_a_faces": [tops[0]["tag"]], "side_b_faces": [bots[0]["tag"]]},
        {"id": "b2", "name": "Bolt 2", "size": "M10", "d_mm": 10, "E_GPa": 210,
         "yield_MPa": 640, "preload_N": 0,
         "side_a_faces": [tops[1]["tag"]], "side_b_faces": [bots[1]["tag"]]},
    ]
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Static", "config": {},
        "supports": [{"id": "s1", "name": "fix", "type": "fixed",
                      "faces": [flat[0]["tag"]]}],
        "loads": [{"id": "l1", "name": "pull", "type": "force",
                   "faces": [flat[1]["tag"]], "fx": 0, "fy": 0, "fz": 900}]}]
    assert c.put(f"/api/projects/{pid}/setup", json=setup).status_code == 200
    assert wait(c, c.post(f"/api/projects/{pid}/mesh").json()["job"])["status"] == "done"
    assert wait(c, c.post(f"/api/projects/{pid}/solve/a1").json()["job"])["status"] == "done"

    both = c.get(f"/api/projects/{pid}/results/a1/bolt-sizing").json()
    assert len(both["rows"]) == 2, both
    by_name = {r["name"]: r for r in both["rows"]}
    forces = {n: (r["F_A"], r["F_Q"]) for n, r in by_name.items()}

    # Drop the FIRST bolt. Bolt 2 keeps its mesh group (BOLT2) but is now at
    # position 1, which is where the old join looked.
    setup["bolts"] = [setup["bolts"][1]]
    assert c.put(f"/api/projects/{pid}/setup", json=setup).status_code == 200

    after = c.get(f"/api/projects/{pid}/results/a1/bolt-sizing").json()
    assert len(after["rows"]) == 1, after
    row = after["rows"][0]
    assert row["name"] == "Bolt 2"
    assert (row["F_A"], row["F_Q"]) == forces["Bolt 2"], (
        "Bolt 2 was sized against another bolt's forces: "
        f"{(row['F_A'], row['F_Q'])} vs its own {forces['Bolt 2']} "
        f"(Bolt 1 carried {forces['Bolt 1']})")


# ---------------------------------------------- a run that fails, end to end

def _plates_static(c):
    """A meshed project with one static analysis, not yet solved."""
    proj = make_project(c)
    pid = proj["id"]
    p = c.get(f"/api/projects/{pid}").json()
    meta = p["geometry"]
    flat = sorted((f for f in meta["faces"]
                   if (f.get("fit") or {}).get("kind") == "plane"),
                  key=lambda f: -f["area"])
    setup = p["setup"]
    setup["materials"] = [{"id": "st", "name": "Steel", "E_GPa": 210.0,
                           "nu": 0.3, "rho_kgm3": 7850}]
    setup["assignments"] = {str(s["tag"]): "st" for s in meta["solids"]}
    setup["analyses"] = [{
        "id": "a1", "type": "static", "name": "Static", "config": {},
        "supports": [{"id": "s1", "name": "fix", "type": "fixed",
                      "faces": [flat[0]["tag"]]}],
        "loads": [{"id": "l1", "name": "pull", "type": "force",
                   "faces": [flat[1]["tag"]], "fx": 0, "fy": 0, "fz": 500}]}]
    assert c.put(f"/api/projects/{pid}/setup", json=setup).status_code == 200
    assert wait(c, c.post(f"/api/projects/{pid}/mesh").json()["job"])["status"] == "done"
    return pid


def test_a_run_that_produced_nothing_does_not_claim_results(client, monkeypatch):
    """meta.json was written whether or not anything came back, and
    results-status answered has_results from the file's existence. So a run
    that produced nothing still badged the analysis done — reopen the project
    the next day and it claimed a finished analysis with an empty panel and the
    reason nowhere on screen.
    """
    c = client
    monkeypatch.setenv("LATTICE_MOCK_FAIL", "immediately")
    pid = _plates_static(c)
    st = wait(c, c.post(f"/api/projects/{pid}/solve/a1").json()["job"])
    assert st["status"] == "failed", json.dumps(st)[:2000]

    status = c.get(f"/api/projects/{pid}/results-status").json()
    assert status["a1"]["failed"] is True
    assert status["a1"]["has_results"] is False, "an empty run claimed results"

    meta = c.get(f"/api/projects/{pid}/results/a1").json()
    assert meta["failed"] is True and meta["recovered"] is False
    # the reason is stored with the run, because the job log is pruned
    assert "FACTOR_10" in meta["error"], meta["error"]


def test_a_run_that_died_after_writing_its_fields_keeps_them(client, monkeypatch):
    """The recoverable case, and the one the whole guarded-deck change exists
    for: the solve finished, the MED was written, and something after it fell
    over. Those fields are real and must survive — but the analysis is still
    reported as failed, so nobody reads a fragment as a finished answer.
    """
    c = client
    monkeypatch.setenv("LATTICE_MOCK_FAIL", "after_fields")
    pid = _plates_static(c)
    st = wait(c, c.post(f"/api/projects/{pid}/solve/a1").json()["job"])

    meta = c.get(f"/api/projects/{pid}/results/a1").json()
    assert meta["fields"], "the fields it had already written were thrown away"
    assert meta["recovered"] is True
    assert meta["failed"] is True, "a partial run must not read as complete"
    assert meta["exit_code"] != 0

    status = c.get(f"/api/projects/{pid}/results-status").json()
    assert status["a1"] == {"has_results": True, "failed": True,
                            "no_signature": False, "stale": False}
    # and the job did not pretend to succeed
    assert st["status"] in ("done", "failed")
    assert any("recover" in ln.lower() for ln in st.get("log") or []), st.get("log")


# ------------------------------------------- removing a body from the analysis

def test_a_removed_body_is_not_in_the_mesh(client):
    """Removal has to be real, not a display setting: no elements, no mass, no
    faces to pick. The geometry file is deliberately untouched — re-importing
    the STEP renumbers every tag in the project — so the mesher drops the
    volume instead, and the body can come back.
    """
    c = client
    pid = _plates_static(c)
    before = c.get(f"/api/projects/{pid}/mesh").json()
    p = c.get(f"/api/projects/{pid}").json()
    solids = p["geometry"]["solids"]
    assert len(solids) == 2, "this fixture needs two bodies"
    victim = solids[0]["tag"]

    setup = p["setup"]
    setup["suppressed_solids"] = [victim]
    # its own material assignment can stay; it simply stops being meshed
    assert c.put(f"/api/projects/{pid}/setup", json=setup).status_code == 200
    job = wait(c, c.post(f"/api/projects/{pid}/mesh").json()["job"])
    assert job["status"] == "done", json.dumps(job)[:2000]

    after = c.get(f"/api/projects/{pid}/mesh").json()
    st_before, st_after = before["stats"], after["stats"]
    assert st_after["nodes"] < st_before["nodes"], "the body is still meshed"
    assert f"V{victim}" not in json.dumps(st_after), "its volume group survived"
    # gmsh reports the geometry it actually meshed; the removed body is not in it
    assert st_after["geo_volume"] < st_before["geo_volume"] * 0.99

    # and it comes back
    setup["suppressed_solids"] = []
    assert c.put(f"/api/projects/{pid}/setup", json=setup).status_code == 200
    assert wait(c, c.post(f"/api/projects/{pid}/mesh").json()["job"])["status"] == "done"
    restored = c.get(f"/api/projects/{pid}/mesh").json()["stats"]
    assert restored["nodes"] == pytest.approx(st_before["nodes"], rel=0.02)


def test_removing_every_body_is_refused_rather_than_meshing_nothing(client):
    c = client
    pid = _plates_static(c)
    p = c.get(f"/api/projects/{pid}").json()
    setup = p["setup"]
    setup["suppressed_solids"] = [s["tag"] for s in p["geometry"]["solids"]]
    assert c.put(f"/api/projects/{pid}/setup", json=setup).status_code == 200
    mdir = os.path.join(c.workspace, "projects", pid, "mesh")
    before = {n: open(os.path.join(mdir, n), "rb").read()
              for n in ("stats.json", "mesh.unv")}
    job = wait(c, c.post(f"/api/projects/{pid}/mesh").json()["job"])
    assert job["status"] == "failed"
    assert "nothing left to mesh" in json.dumps(job), json.dumps(job)[:1500]
    # A failed mesh leaves the previous one whole. It used to be written in
    # place, so a failure after the UNV was out paired the new mesh file with
    # the old mesh's groups and node numbers.
    for n, blob in before.items():
        assert open(os.path.join(mdir, n), "rb").read() == blob, n
    assert not os.path.exists(os.path.join(mdir, "building"))


def test_a_mesh_that_fails_after_writing_leaves_the_old_one_whole(client, monkeypatch):
    """The mesher writes the UNV well before it finishes. Written in place, a
    failure after that point left the new mesh file under the old stats, and
    the next solve ran on a mesh described by another mesh's groups, probe
    nodes and node numbers."""
    from lattice_fea import server
    c = client
    pid = _plates_static(c)
    mdir = os.path.join(c.workspace, "projects", pid, "mesh")
    before = {n: open(os.path.join(mdir, n), "rb").read()
              for n in ("stats.json", "mesh.unv", "mesh.inp")}

    def dies_late(job, args):
        with open(args["unv"], "w") as fh:
            fh.write("half a mesh")
        raise RuntimeError("the mesher fell over after writing the UNV")

    monkeypatch.setattr(server, "run_gmsh_worker", dies_late)
    job = wait(c, c.post(f"/api/projects/{pid}/mesh").json()["job"])
    assert job["status"] == "failed"
    for n, blob in before.items():
        assert open(os.path.join(mdir, n), "rb").read() == blob, n
    assert not os.path.exists(os.path.join(mdir, "building"))


def test_publishing_a_mesh_drops_files_the_new_mesh_did_not_make(tmp_path):
    """A CalculiX deck that failed to write must not be replaced by the
    previous mesh's deck, which numbers different nodes."""
    from lattice_fea.server import publish_mesh
    stage, mdir = tmp_path / "stage", tmp_path / "mesh"
    stage.mkdir(); mdir.mkdir()
    for n in ("stats.json", "mesh.unv", "mesh.inp", "skin.json.gz"):
        (mdir / n).write_text("old")
    for n in ("stats.json", "mesh.unv", "skin.json.gz"):
        (stage / n).write_text("new")
    publish_mesh(str(stage), str(mdir))
    assert sorted(x.name for x in mdir.iterdir()) == ["mesh.unv", "skin.json.gz", "stats.json"]
    assert all((mdir / n).read_text() == "new" for n in ("stats.json", "mesh.unv"))


# ------------------------------------------------------- mesh quality gating

def test_a_mesh_with_inverted_elements_will_not_solve(client):
    """An inverted element has a Jacobian that changes sign inside it, so its
    stiffness contribution is wrong rather than inaccurate — and the solver
    returns a full set of plausible numbers anyway. Quality was measured and
    printed in a table cell; nothing ever looked at it.
    """
    c = client
    pid = _plates_static(c)
    path = os.path.join(c.workspace, "projects", pid, "mesh", "stats.json")
    with open(path) as fh:
        stats = json.load(fh)
    assert stats.get("quality_counts"), "quality is not being counted at all"
    assert stats["quality_counts"]["inverted"] == 0, "this fixture should be clean"

    stats["quality_counts"]["inverted"] = 3
    with open(path, "w") as fh:
        json.dump(stats, fh)
    r = c.post(f"/api/projects/{pid}/solve/a1")
    assert r.status_code == 422, r.text
    assert "inverted" in r.text


def test_a_clean_mesh_still_solves(client):
    """A gate that refuses good meshes is worse than no gate."""
    c = client
    pid = _plates_static(c)
    r = c.post(f"/api/projects/{pid}/solve/a1")
    assert r.status_code == 200, r.text
    assert wait(c, r.json()["job"])["status"] == "done"


# --------------------------------------------- the setup document is a shape

@pytest.mark.parametrize("patch,fragment", [
    ({"analyses": "nope"}, "must be a list"),
    ({"analyses": ["nope"]}, "must be an object"),
    ({"contacts": [{"id": "c", "kind": "bonded", "faces_a": 5, "faces_b": [6]}]},
     "list of face numbers"),
    ({"contacts": [{"id": "c", "kind": "bonded", "faces_a": ["x"], "faces_b": [6]}]},
     "whole numbers"),
    ({"bolts": [{"id": "b", "name": "B", "side_a_faces": 3}]}, "list of face numbers"),
])
def test_a_malformed_setup_is_refused_not_a_500(client, patch, fragment):
    """validate_setup walks these lists and calls .get on every entry, so a
    string where a list belongs iterated its characters and raised
    AttributeError — which escaped as a 500. A bare number in a face field was
    accepted outright and only failed at mesh time, where the message is about
    mesh groups rather than about the field that was wrong.
    """
    c = client
    pid = make_project(c)["id"]
    setup = c.get(f"/api/projects/{pid}").json()["setup"]
    setup.update(patch)
    r = c.put(f"/api/projects/{pid}/setup", json=setup)
    assert r.status_code == 422, f"{r.status_code}: {r.text[:300]}"
    assert fragment in r.text, r.text


def test_a_project_with_a_damaged_index_is_still_listed(client):
    """Only project.json is unreadable; the geometry, the mesh and every run
    are untouched beside it. The listing caught the parse error and dropped
    the row, so the whole project disappeared from the app while the work sat
    in the workspace — a worse failure than a row that will not open.
    """
    c = client
    pid = make_project(c)["id"]
    path = os.path.join(c.workspace, "projects", pid, "project.json")
    with open(path, "w") as fh:
        fh.write("{ this is not json")

    rows = c.get("/api/projects").json()
    row = next((r for r in rows if r["id"] == pid), None)
    assert row is not None, "the project vanished from the listing"
    assert row.get("damaged"), row
    # opening it still fails honestly rather than serving half a project
    assert c.get(f"/api/projects/{pid}").status_code == 404
