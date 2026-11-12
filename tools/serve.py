from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.api import server


def main() -> int:
    ap = argparse.ArgumentParser(description="Интерфейс FORGE")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    args = ap.parse_args()

    url = f"http://{args.host}:{args.port}"
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    server.serve(args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
