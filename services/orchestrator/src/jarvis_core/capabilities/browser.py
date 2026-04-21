"""Real browser capability adapter (Windows-first, stdlib only).

Supported capabilities (v1):
  browser.navigate    - Tier 0. Open URL in the user's default browser.
  browser.read_page   - Tier 0. HTTP GET a URL and extract the <title>.
  browser.download_file - Tier 2. Download to a sandbox path (approval-gated).

No browser automation library is pulled in yet — read_page is a plain HTTP
fetch, and navigate just hands the URL off to Windows via `webbrowser.open`.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, Optional

from ..models import ActionProposal, ActionResult
from .base import CapabilityAdapter

_SAFE_SCHEMES = {"http", "https"}
_MAX_READ_BYTES = 512 * 1024  # 512 KB cap
_USER_AGENT = "JarvisGuardedAssistant/0.1 (+local)"
_HTTP_TIMEOUT = 8
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _validate_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Parameter 'url' must be a non-empty string")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in _SAFE_SCHEMES:
        raise ValueError(f"Only http/https URLs are allowed (got '{parsed.scheme}')")
    if not parsed.netloc:
        raise ValueError("URL is missing a host")
    return url


def _extract_title(body_bytes: bytes, charset: str = "utf-8") -> Optional[str]:
    try:
        text = body_bytes.decode(charset, errors="replace")
    except LookupError:
        text = body_bytes.decode("utf-8", errors="replace")
    match = _TITLE_RE.search(text)
    if not match:
        return None
    return html.unescape(match.group(1).strip())


class BrowserCapability(CapabilityAdapter):
    """Real browser adapter backed by stdlib only."""

    name = "browser"

    def __init__(self, sandbox_root: Optional[Path] = None) -> None:
        self.sandbox_root = Path(sandbox_root) if sandbox_root else None

    # ------------------------------------------------------------------
    def supports(self, capability: str) -> bool:
        return capability in {"browser.navigate", "browser.read_page", "browser.download_file"}

    def execute(self, proposal: ActionProposal) -> ActionResult:
        try:
            if proposal.capability == "browser.navigate":
                return self._execute_navigate(proposal)
            if proposal.capability == "browser.read_page":
                return self._execute_read_page(proposal)
            if proposal.capability == "browser.download_file":
                return self._execute_download(proposal)
        except (ValueError, urllib.error.URLError, OSError) as exc:
            return ActionResult(
                proposal=proposal,
                status="failed",
                summary=f"{proposal.capability} failed: {exc}",
                output={"error": str(exc), "dry_run": proposal.dry_run},
            )
        raise KeyError(f"Unsupported capability: {proposal.capability}")

    def verify(self, proposal: ActionProposal, result: ActionResult) -> Dict[str, Any]:
        if result.status != "executed":
            return {"ok": False, "reason": result.status, "mode": "real"}

        checks: list[str] = []
        if proposal.capability == "browser.navigate":
            checks = ["url.scheme_allowed", "webbrowser.invoked"]
        elif proposal.capability == "browser.read_page":
            checks = ["http.status_ok"]
            if result.output.get("title"):
                checks.append("title.extracted")
        elif proposal.capability == "browser.download_file":
            path_str = result.output.get("path")
            exists = bool(path_str and Path(path_str).exists())
            return {
                "ok": exists or bool(proposal.dry_run),
                "checked": ["destination.in_sandbox", "file.exists"],
                "file_exists": exists,
                "mode": "real",
            }
        return {"ok": True, "checked": checks, "mode": "real"}

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _execute_navigate(self, proposal: ActionProposal) -> ActionResult:
        url = _validate_url(proposal.parameters.get("url", ""))
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal,
                status="executed",
                summary=f"[dry-run] Would open {url} in default browser.",
                output={"url": url, "dry_run": True, "opened": False},
            )
        opened = webbrowser.open(url, new=2, autoraise=True)
        return ActionResult(
            proposal=proposal,
            status="executed" if opened else "failed",
            summary=("Opened " if opened else "Failed to open ") + f"{url} in default browser.",
            output={"url": url, "dry_run": False, "opened": opened},
        )

    def _execute_read_page(self, proposal: ActionProposal) -> ActionResult:
        url = _validate_url(proposal.parameters.get("url", ""))
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal,
                status="executed",
                summary=f"[dry-run] Would fetch {url}.",
                output={"url": url, "dry_run": True},
            )

        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read(_MAX_READ_BYTES)

        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"

        title = _extract_title(body, charset) if content_type.startswith(("text/html", "application/xhtml")) else None
        return ActionResult(
            proposal=proposal,
            status="executed",
            summary=f"Fetched {url} ({len(body)} bytes, status {status}).",
            output={
                "url": url,
                "status": status,
                "content_type": content_type,
                "byte_count": len(body),
                "title": title,
                "truncated": len(body) >= _MAX_READ_BYTES,
                "dry_run": False,
            },
        )

    def _execute_download(self, proposal: ActionProposal) -> ActionResult:
        if self.sandbox_root is None:
            raise ValueError("Downloads are disabled (no sandbox root configured).")
        url = _validate_url(proposal.parameters.get("url", ""))
        filename = proposal.parameters.get("filename")
        if not filename or not isinstance(filename, str):
            filename = Path(urllib.parse.urlparse(url).path).name or "download.bin"
        # Reject path separators — must resolve to a direct child of the sandbox.
        if "/" in filename or "\\" in filename or filename in ("..", "."):
            raise ValueError("Parameter 'filename' must be a plain file name (no path separators).")

        dest = (self.sandbox_root / filename).resolve()
        sandbox = self.sandbox_root.resolve()
        if sandbox not in dest.parents and dest != sandbox:
            raise ValueError("Download destination escapes sandbox root.")
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal,
                status="executed",
                summary=f"[dry-run] Would download {url} to {dest}.",
                output={"url": url, "path": str(dest), "dry_run": True},
            )

        sandbox.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        total = 0
        try:
            with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp, dest.open("wb") as fh:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    total += len(chunk)
                    if total > _MAX_READ_BYTES * 20:  # ~10MB hard cap for v1
                        raise ValueError("Download exceeded 10 MB safety cap.")
        except BaseException:
            # Clean up any partial file so we never leave half-downloads on disk.
            try:
                dest.unlink()
            except FileNotFoundError:
                pass
            raise

        return ActionResult(
            proposal=proposal,
            status="executed",
            summary=f"Downloaded {url} ({total} bytes) to {dest}.",
            output={"url": url, "path": str(dest), "byte_count": total, "dry_run": False},
        )
