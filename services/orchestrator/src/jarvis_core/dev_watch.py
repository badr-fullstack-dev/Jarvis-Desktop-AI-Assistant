"""Development-only bridge auto-restarter.

Usage (from the project root)::

    python -m jarvis_core.dev_watch           # watches the bridge source tree
    python -m jarvis_core.dev_watch --port 7821

What it does
------------
1. Spawns ``python -m jarvis_core`` as a single child process on the
   same port the HUD expects.
2. Walks ``services/orchestrator/src/jarvis_core/`` every second and
   hashes file mtimes. If anything changes, it terminates the child
   process and restarts it.
3. Shuts down cleanly on Ctrl-C.

Deliberate constraints
----------------------
- **Dev-only.** Not used by tests, not wired into production entry
  points. Importing it does nothing; it only does work when invoked
  as ``__main__``.
- **Stdlib only.** No ``watchdog`` / ``watchfiles`` dependency.
- **Exactly one child.** The previous child is terminated before a
  new one starts, so we never end up with duplicate bridges fighting
  for port 7821.
- **No code execution.** The watcher only does ``subprocess.Popen``
  / ``proc.terminate()``. It does not evaluate user-edited files
  itself, so a bad edit cannot escalate into arbitrary execution
  beyond restarting the orchestrator the developer is already running.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List


def _watched_files(roots: List[Path]) -> Dict[Path, float]:
    out: Dict[Path, float] = {}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            try:
                out[path] = path.stat().st_mtime
            except OSError:
                pass
    return out


def _spawn(argv_tail: List[str]) -> subprocess.Popen:
    # Inherit stdout/stderr so bridge logs appear in the watcher terminal.
    # Use the same interpreter that runs the watcher (so env vars and
    # installed packages match exactly).
    cmd = [sys.executable, "-m", "jarvis_core", *argv_tail]
    print(f"[dev-watch] spawning: {' '.join(cmd)}", flush=True)
    # Create a new process group on Windows so we can send CTRL_BREAK
    # to it without killing the watcher itself.
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    return subprocess.Popen(cmd, creationflags=creationflags)


def _terminate(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass


def _changed(prev: Dict[Path, float], curr: Dict[Path, float]) -> List[Path]:
    changed: List[Path] = []
    for p, mt in curr.items():
        if prev.get(p) != mt:
            changed.append(p)
    for p in prev:
        if p not in curr:
            changed.append(p)
    return changed


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jarvis dev auto-restarter")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Seconds between filesystem scans (default 1.0).")
    parser.add_argument("--debounce", type=float, default=0.4,
                        help="Seconds to wait for more changes before restarting.")
    # Remaining args are forwarded verbatim to `python -m jarvis_core`.
    args, forward = parser.parse_known_args(argv)

    here = Path(__file__).resolve()
    pkg_root = here.parent                                # src/jarvis_core
    src_root = pkg_root.parent                             # src
    project_root = src_root.parents[2]                     # project root
    configs_root = project_root / "configs"

    watch_roots = [pkg_root, configs_root]
    print(f"[dev-watch] watching: {[str(r) for r in watch_roots]}", flush=True)

    proc = _spawn(forward)
    prev = _watched_files(watch_roots)

    try:
        while True:
            time.sleep(args.interval)

            # If the child died (crash, port conflict, import error),
            # wait for a source change before respawning to avoid a
            # tight restart loop.
            if proc.poll() is not None:
                print(f"[dev-watch] bridge exited with code {proc.returncode}. "
                      f"Waiting for a source edit to restart.", flush=True)
                while True:
                    time.sleep(args.interval)
                    curr = _watched_files(watch_roots)
                    if _changed(prev, curr):
                        prev = curr
                        proc = _spawn(forward)
                        break

            curr = _watched_files(watch_roots)
            changed = _changed(prev, curr)
            if not changed:
                continue

            # Simple debounce: wait, re-read, restart only if the set
            # still looks dirty.
            time.sleep(args.debounce)
            curr = _watched_files(watch_roots)
            changed = _changed(prev, curr)
            if not changed:
                continue

            print(f"[dev-watch] {len(changed)} file(s) changed "
                  f"(e.g. {changed[0].name}); restarting bridge.", flush=True)
            _terminate(proc)
            proc = _spawn(forward)
            prev = curr
    except KeyboardInterrupt:
        print("[dev-watch] Ctrl-C — shutting down.", flush=True)
        _terminate(proc)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
