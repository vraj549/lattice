"""CLI entry point:  python -m lattice_fea  (or the `lattice` script)."""
from __future__ import annotations

import argparse
import os
import threading
import time
import webbrowser


def main() -> None:
    ap = argparse.ArgumentParser(prog="lattice", description="Lattice FEA workbench")
    ap.add_argument("command", nargs="?", default="serve", choices=["serve", "doctor"])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--workspace", default="workspace")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--demo-solver", action="store_true",
                    help="run with a stand-in solver that fabricates results — "
                         "exercises the whole UI without code_aster installed. "
                         "NOT physics; never use for engineering decisions.")
    args = ap.parse_args()

    if args.demo_solver:
        import os as _os
        import sys as _sys
        mock = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "mock_solver.py")
        _os.environ["LATTICE_ASTER_MODE"] = "native"
        _os.environ["LATTICE_ASTER_CMD"] = f"{_sys.executable} {mock}"
        print("[lattice] *** DEMO SOLVER — results are fabricated, not computed ***")

    if args.command == "doctor":
        doctor(args.workspace)
        return

    import signal

    import uvicorn

    from .server import create_app
    from .solver import kill_all_children

    # Ctrl+C must always work. First press: reap children, then let uvicorn
    # shut down. Second press: leave immediately, whatever a child is doing.
    # (A wsl.exe or docker child can otherwise keep the console hostage.)
    _interrupts = {"n": 0}

    def _on_sigint(signum, frame):
        _interrupts["n"] += 1
        if _interrupts["n"] == 1:
            n = kill_all_children()
            print(f"\n[lattice] stopping… ({n} running job(s) terminated)")
            print("[lattice] press Ctrl+C again to force-quit")
            raise KeyboardInterrupt
        os._exit(1)

    signal.signal(signal.SIGINT, _on_sigint)
    if hasattr(signal, "SIGBREAK"):        # Windows Ctrl+Break
        signal.signal(signal.SIGBREAK, _on_sigint)

    app = create_app(args.workspace)
    url = f"http://{args.host}:{args.port}"
    from . import __version__
    print(f"[lattice] v{__version__} serving on {url}")
    print("[lattice] this process holds the code in memory — after a `git pull`,")
    print("[lattice] stop it (Ctrl+C) and start it again, then hard-refresh the page.")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)),
                         daemon=True).start()
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        kill_all_children()
        print("[lattice] stopped.")


def doctor(workspace: str) -> None:
    """Environment check. Everything installation-related should be visible
    here, and nothing here may disagree with what the app itself decides."""
    from . import __version__
    from .ccx_writer import CAPABILITIES
    from .config import detect

    print(f"Lattice {__version__} — environment check\n")

    ok = True
    try:
        import gmsh  # noqa: F401
        print(f"  gmsh          OK  ({gmsh.GMSH_API_VERSION})")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"  gmsh          MISSING — pip install gmsh   ({e})")
    for mod in ("fastapi", "uvicorn", "numpy", "h5py"):
        try:
            __import__(mod)
            print(f"  {mod:<13} OK")
        except Exception:  # noqa: BLE001
            ok = False
            print(f"  {mod:<13} MISSING — pip install {mod}")

    cfg = detect(workspace)
    engines = cfg.engines()
    print("\n  Solvers")
    if engines:
        for e in engines:
            types = ", ".join(e["types"])
            print(f"    {e['label']:<12} {e['detail']}")
            print(f"    {'':<12} runs: {types}")
        if cfg.ccx_cmd:
            print(f"    {'':<12} CalculiX threads: {cfg.ccx_threads}"
                  + ("  (multithreaded ccx can return wrong results)"
                     if cfg.ccx_threads == 1 else "  — see docs/SOLVERS.md"))
    else:
        print("    none found — import, setup and meshing work; Run is disabled")
        print("    macOS / Linux:  brew install costerwi/homebrew-calculix/calculix-ccx")
        print("    Windows:        install the Salome-Meca WSL2 distribution")
        print("    See docs/INSTALL.md")
    for n in cfg.notes:
        print(f"    note: {n}")

    missing_types = sorted(
        {"static", "modal", "harmonic", "random"}
        - {t for e in engines for t in e["types"]})
    if engines and missing_types:
        print(f"    not runnable here: {', '.join(missing_types)} "
              "(needs code_aster)")

    _doctor_projects(workspace, engines)

    if not engines:
        print("\n  Lattice runs in geometry/mesh-only mode until a solver is set up.")
    elif ok:
        print("\n  Environment OK. Per-project blockers, if any, are listed above.")


def _doctor_projects(workspace: str, engines: list) -> None:
    """Per project, exactly what is stopping a run — using the same rules the
    UI uses, so the two cannot disagree."""
    import json as _json
    import os as _os

    from .meshing import MESH_FORMAT

    proj_root = _os.path.join(workspace, "projects")
    if not _os.path.isdir(proj_root):
        return
    names = sorted(d for d in _os.listdir(proj_root)
                   if _os.path.isfile(_os.path.join(proj_root, d, "project.json")))
    if not names:
        return
    print("\n  Projects")
    for pid in names:
        try:
            with open(_os.path.join(proj_root, pid, "project.json"),
                      encoding="utf-8") as f:
                p = _json.load(f)
        except Exception:  # noqa: BLE001
            print(f"    {pid}: unreadable project.json")
            continue
        s = p.get("setup", {})
        geo = p.get("geometry") or {}
        analyses = s.get("analyses", [])
        stats = {}
        sp = _os.path.join(proj_root, pid, "mesh", "stats.json")
        if _os.path.isfile(sp):
            try:
                with open(sp, encoding="utf-8") as f:
                    stats = _json.load(f)
            except Exception:  # noqa: BLE001
                pass

        print(f"    {p.get('name', pid)}  [{pid}]")
        print(f"      {len(geo.get('solids', []))} solid(s), "
              f"{len(analyses)} analysis(es), "
              f"{len(s.get('bolts', []))} bolt(s), "
              f"{len(s.get('contacts', []))} contact(s)")

        model_blockers = []
        if not geo:
            model_blockers.append("geometry not imported")
        for x in geo.get("solids", []):
            if not s.get("assignments", {}).get(str(x["tag"])):
                model_blockers.append(f"solid {x['tag']} has no material")
        if not stats:
            model_blockers.append("not meshed")
        elif (stats.get("mesh_format") or 0) < MESH_FORMAT:
            model_blockers.append("mesh was written by an older version — re-mesh")

        if not analyses:
            print("      no analyses yet")
        for a in analyses:
            # supports and loads belong to the ANALYSIS, not the model — this
            # check read them off the model for several releases and so
            # reported "no support with faces" for every project.
            issues = list(model_blockers)
            if not any(x.get("faces") for x in a.get("supports", [])):
                issues.append("no support with faces")
            eng = (a.get("config") or {}).get("engine") or (
                "aster" if any(e["id"] == "aster" for e in engines) else
                (engines[0]["id"] if engines else "aster"))
            match = next((e for e in engines if e["id"] == eng), None)
            if not engines:
                issues.append("no solver installed")
            elif match is None:
                issues.append(f"solver '{eng}' is not installed here")
            elif a.get("type") not in match["types"]:
                issues.append(f"{match['label']} cannot run {a.get('type')}")
            label = a.get("name") or a.get("type")
            print(f"      · {label} ({a.get('type')}, {eng}): "
                  + ("; ".join(issues) if issues else "ready to run"))


if __name__ == "__main__":
    main()
