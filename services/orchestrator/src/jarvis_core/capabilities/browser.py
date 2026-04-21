"""Real browser capability adapter (Windows-first, stdlib only).

Supported capabilities (v2):
  browser.navigate       - Tier 0. Open URL in the user's default browser.
  browser.read_page      - Tier 0. HTTP GET a URL and extract title + text.
  browser.summarize      - Tier 0. Fetch (or reuse context) + structured summary.
  browser.current_page   - Tier 0. Inspect the in-memory browser context.
  browser.download_file  - Tier 2. Download to a sandbox path (approval-gated).

No browser automation library is pulled in. read_page / summarize are
plain stdlib HTTP fetches, title + readable text are extracted with
regex stripping only — there is no DOM, no JavaScript execution, no
form submission, and no auto-clicking. All reads are capped.
"""

from __future__ import annotations

import html
import re
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..browser_context import BrowserContext
from ..models import ActionProposal, ActionResult
from .base import CapabilityAdapter

_SAFE_SCHEMES = {"http", "https"}
_MAX_READ_BYTES = 512 * 1024  # 512 KB cap
_MAX_EXCERPT_CHARS = 4000     # ~4 KB of readable text kept per fetch
_USER_AGENT = "JarvisGuardedAssistant/0.1 (+local)"
_HTTP_TIMEOUT = 8
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")


def _validate_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Parameter 'url' must be a non-empty string")
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme.lower() not in _SAFE_SCHEMES:
        raise ValueError(f"Only http/https URLs are allowed (got '{parsed.scheme}')")
    if not parsed.netloc:
        raise ValueError("URL is missing a host")
    return url


def _decode(body_bytes: bytes, charset: str = "utf-8") -> str:
    try:
        return body_bytes.decode(charset, errors="replace")
    except LookupError:
        return body_bytes.decode("utf-8", errors="replace")


def _extract_title(text: str) -> Optional[str]:
    match = _TITLE_RE.search(text)
    if not match:
        return None
    return html.unescape(match.group(1).strip())


def _extract_readable_text(html_text: str) -> str:
    """Strip scripts/styles/tags and collapse whitespace. No DOM, no JS."""
    stripped = _SCRIPT_STYLE_RE.sub(" ", html_text)
    stripped = _TAG_RE.sub(" ", stripped)
    stripped = html.unescape(stripped)
    # Collapse horizontal whitespace but preserve paragraph breaks.
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in stripped.splitlines()]
    return "\n".join(line for line in lines if line)


def _summarize(text: str, *, max_sentences: int = 3) -> List[str]:
    """Deterministic summary: the first N non-trivial sentences."""
    if not text:
        return []
    # Work only over the first ~2000 chars for predictable cost.
    head = text[:2000]
    parts = _SENTENCE_SPLIT_RE.split(head)
    out: List[str] = []
    for part in parts:
        candidate = part.strip()
        if len(candidate) >= 20:
            out.append(candidate)
        if len(out) >= max_sentences:
            break
    return out


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


class BrowserCapability(CapabilityAdapter):
    """Real browser adapter backed by stdlib only."""

    name = "browser"

    _SUPPORTED = {
        "browser.navigate",
        "browser.read_page",
        "browser.summarize",
        "browser.current_page",
        "browser.download_file",
    }

    def __init__(
        self,
        sandbox_root: Optional[Path] = None,
        context: Optional[BrowserContext] = None,
    ) -> None:
        self.sandbox_root = Path(sandbox_root) if sandbox_root else None
        # Context is optional at construction so existing callers keep
        # working; the API layer injects a real one.
        self.context = context if context is not None else BrowserContext()

    # ------------------------------------------------------------------
    def supports(self, capability: str) -> bool:
        return capability in self._SUPPORTED

    def execute(self, proposal: ActionProposal) -> ActionResult:
        try:
            if proposal.capability == "browser.navigate":
                return self._execute_navigate(proposal)
            if proposal.capability == "browser.read_page":
                return self._execute_read_page(proposal)
            if proposal.capability == "browser.summarize":
                return self._execute_summarize(proposal)
            if proposal.capability == "browser.current_page":
                return self._execute_current_page(proposal)
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
            if result.output.get("text_excerpt"):
                checks.append("text.extracted")
        elif proposal.capability == "browser.summarize":
            checks = ["summary.produced"]
            if result.output.get("source") == "context":
                checks.append("context.reused")
            else:
                checks.append("http.status_ok")
        elif proposal.capability == "browser.current_page":
            return {"ok": True, "checked": ["context.read"], "mode": "real"}
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

    def _fetch_page(self, url: str) -> Dict[str, Any]:
        """HTTP GET a URL and return a structured, size-capped extract.

        Shared by read_page and summarize. Does NOT update context — the
        caller decides whether to record it.
        """
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read(_MAX_READ_BYTES)

        charset = "utf-8"
        if "charset=" in content_type:
            charset = content_type.split("charset=")[-1].split(";")[0].strip() or "utf-8"

        decoded = _decode(body, charset)
        is_html = content_type.startswith(("text/html", "application/xhtml"))
        title = _extract_title(decoded) if is_html else None
        text = _extract_readable_text(decoded) if is_html else decoded
        excerpt = _truncate(text, _MAX_EXCERPT_CHARS)
        return {
            "url": url,
            "status": status,
            "content_type": content_type,
            "byte_count": len(body),
            "title": title,
            "text_excerpt": excerpt,
            "truncated": len(body) >= _MAX_READ_BYTES,
        }

    def _execute_read_page(self, proposal: ActionProposal) -> ActionResult:
        url = _validate_url(proposal.parameters.get("url", ""))
        if proposal.dry_run:
            return ActionResult(
                proposal=proposal,
                status="executed",
                summary=f"[dry-run] Would fetch {url}.",
                output={"url": url, "dry_run": True},
            )

        fetched = self._fetch_page(url)
        self.context.record_page(
            url=fetched["url"],
            title=fetched.get("title"),
            text_excerpt=fetched.get("text_excerpt"),
            byte_count=fetched.get("byte_count", 0),
            source="browser.read_page",
        )
        return ActionResult(
            proposal=proposal,
            status="executed",
            summary=(
                f"Fetched {url} ({fetched['byte_count']} bytes, "
                f"status {fetched['status']})."
            ),
            output={**fetched, "dry_run": False},
        )

    def _execute_summarize(self, proposal: ActionProposal) -> ActionResult:
        url = (proposal.parameters.get("url") or "").strip() or None
        use_context = bool(proposal.parameters.get("use_context"))

        if url:
            url = _validate_url(url)
            if proposal.dry_run:
                return ActionResult(
                    proposal=proposal, status="executed",
                    summary=f"[dry-run] Would fetch + summarize {url}.",
                    output={"url": url, "dry_run": True, "source": "fetch"},
                )
            fetched = self._fetch_page(url)
            self.context.record_page(
                url=fetched["url"],
                title=fetched.get("title"),
                text_excerpt=fetched.get("text_excerpt"),
                byte_count=fetched.get("byte_count", 0),
                source="browser.summarize",
            )
            sentences = _summarize(fetched.get("text_excerpt") or "")
            return ActionResult(
                proposal=proposal, status="executed",
                summary=(
                    f"Summarised {url} "
                    f"({len(sentences)} sentence(s) from "
                    f"{fetched['byte_count']} bytes)."
                ),
                output={
                    **fetched,
                    "source": "fetch",
                    "summary_sentences": sentences,
                    "dry_run": False,
                },
            )

        if not use_context:
            raise ValueError(
                "browser.summarize requires either 'url' or "
                "'use_context=true' referencing the last-read page."
            )
        snap = self.context.snapshot()
        if snap is None:
            raise ValueError(
                "No browser context available. Ask the assistant to read "
                "a URL first (e.g. 'read https://example.com')."
            )
        excerpt = snap.get("textExcerpt") or ""
        sentences = _summarize(excerpt)
        return ActionResult(
            proposal=proposal, status="executed",
            summary=(
                f"Summarised current page "
                f"({snap.get('title') or snap.get('url')}) from cached context."
            ),
            output={
                "url": snap.get("url"),
                "title": snap.get("title"),
                "text_excerpt": excerpt,
                "byte_count": snap.get("byteCount", 0),
                "summary_sentences": sentences,
                "source": "context",
                "context_source": snap.get("source"),
                "context_updated_at": snap.get("updatedAt"),
                "dry_run": False,
            },
        )

    def _execute_current_page(self, proposal: ActionProposal) -> ActionResult:
        snap = self.context.snapshot()
        if snap is None:
            raise ValueError(
                "No browser context available. The assistant has not read a "
                "page yet and no snapshot has been pushed."
            )
        return ActionResult(
            proposal=proposal, status="executed",
            summary=(
                f"Current page: "
                f"{snap.get('title') or snap.get('url')} ({snap.get('url')})"
            ),
            output={
                "url": snap.get("url"),
                "title": snap.get("title"),
                "byte_count": snap.get("byteCount", 0),
                "text_excerpt": snap.get("textExcerpt"),
                "context_source": snap.get("source"),
                "context_updated_at": snap.get("updatedAt"),
                "dry_run": bool(proposal.dry_run),
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
