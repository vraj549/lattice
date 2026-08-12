"""CLI entry point:  python -m lattice_fea  (or the `lattice` script)."""
from __future__ import annotations

import argparse
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
    args = ap.parse_args()

    if args.command == "doctor":
        doctor(args.workspace)
        return

    import uvicorn

    from .server import create_app

    app = create_app(args.workspace)
    url = f"http://{args.host}:{args.port}"
    print(f"[lattice] serving on {url}")
    if not args.no_browser:
        threading.Thread(target=lambda: (time.sleep(1.2), webbrowser.open(url)),
                         daemon=True).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


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
    if not cfg.available():
        print("\n  Lattice will run in geometry/mesh-only demo mode until a solver is set up.")
        print("  See README → 'Solver setup' for the WSL2 / docker / native options.")


if __name__ == "__main__":
    main()
