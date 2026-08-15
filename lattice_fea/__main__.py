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
    from . import __version__
    from .config import detect

    print(f"Lattice {__version__} — environment check\n")

    try:
        import gmsh  # noqa: F401
        print(f"  gmsh          OK  ({gmsh.GMSH_API_VERSION})")
    except Exception as e:  # noqa: BLE001
        print(f"  gmsh          MISSING — pip install gmsh   ({e})")
    for mod in ("fastapi", "uvicorn", "numpy", "h5py"):
        try:
            __import__(mod)
            print(f"  {mod:<13} OK")
        except Exception:  # noqa: BLE001
            print(f"  {mod:<13} MISSING — pip install {mod}")

    cfg = detect(workspace)
    print(f"\n  solver mode   {cfg.mode}")
    print(f"  detail        {cfg.detail}")
    for n in cfg.notes:
        print(f"  note          {n}")

    # what the UI would use to decide whether a run is possible
    import json as _json
    import os as _os
    proj_root = _os.path.join(workspace, "projects")
    if _os.path.isdir(proj_root):
        print("\n  projects in this workspace:")
        for pid in sorted(_os.listdir(proj_root)):
            pj = _os.path.join(proj_root, pid, "project.json")
            if not _os.path.isfile(pj):
                continue
            try:
                with open(pj) as f:
                    p = _json.load(f)
            except Exception:  # noqa: BLE001
                continue
            s = p.get("setup", {})
            geo = p.get("geometry") or {}
            meshed = _os.path.isfile(_os.path.join(proj_root, pid, "mesh", "stats.json"))
            missing = [str(x["tag"]) for x in geo.get("solids", [])
                       if not s.get("assignments", {}).get(str(x["tag"]))]
            blockers = []
            if not meshed:
                blockers.append("not meshed")
            if missing:
                blockers.append(f"solids without material: {', '.join(missing)}")
            if not any(x.get("faces") for x in s.get("supports", [])):
                blockers.append("no support with faces")
            if not cfg.available():
                blockers.append("no solver")
            print(f"    {p.get('name', pid)}  [{pid}]")
            print(f"      analyses={len(s.get('analyses', []))} "
                  f"loads={len(s.get('loads', []))} bolts={len(s.get('bolts', []))} "
                  f"meshed={'yes' if meshed else 'no'}")
            print(f"      run blocked by: {'; '.join(blockers) if blockers else 'nothing — should be clickable'}")
        print("\n  To start clean, delete the workspace directory:")
        print(f"    {_os.path.abspath(workspace)}")
    if not cfg.available():
        print("\n  Lattice will run in geometry/mesh-only demo mode until a solver is set up.")
        print("  See README → 'Solver setup' for the WSL2 / docker / native options.")


if __name__ == "__main__":
    main()
