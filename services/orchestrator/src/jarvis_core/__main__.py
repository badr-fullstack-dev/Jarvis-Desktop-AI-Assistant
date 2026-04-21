"""Entry point: `python -m jarvis_core` starts the local HUD bridge."""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from .api import LocalSupervisorAPI
from .bridge import PORT, start_server


def _default_root() -> Path:
    # services/orchestrator/src/jarvis_core/__main__.py -> project root
    return Path(__file__).resolve().parents[4]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jarvis orchestrator HUD bridge")
    parser.add_argument("--port", type=int, default=PORT, help=f"HTTP port (default {PORT})")
    parser.add_argument("--root", type=Path, default=_default_root(),
                        help="Project root containing configs/ and runtime/")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    print(f"[jarvis-bridge] root={root}", file=sys.stderr)

    api = LocalSupervisorAPI(root)
    server = start_server(api, port=args.port, daemon=False)

    print(f"[jarvis-bridge] listening on http://127.0.0.1:{args.port}", file=sys.stderr)

    stop = False

    def _handle_signal(_sig, _frame):
        nonlocal stop
        stop = True
        server.shutdown()

    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (AttributeError, ValueError):
        pass  # Windows may not expose SIGTERM in all contexts

    try:
        while not stop:
            signal.pause() if hasattr(signal, "pause") else __import__("time").sleep(1)
    except (KeyboardInterrupt, SystemExit):
        server.shutdown()

    print("[jarvis-bridge] stopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
