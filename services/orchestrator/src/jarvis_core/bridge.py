"""Local HTTP bridge between the Tauri HUD and the Python orchestrator.

Runs on http://127.0.0.1:7821 by default.  Start with:
    python -m jarvis_core [--port 7821] [--root <project-root>]

All endpoints return JSON.  The bridge never executes raw shell commands;
every action still flows through ActionGateway + PolicyEngine.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .voice import VoiceError

PORT = 7821

# Populated by start_server()
_api: Any = None  # LocalSupervisorAPI


# ---------------------------------------------------------------------------
# State assembly
# ---------------------------------------------------------------------------

def _format_time(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M:%S")
    except Exception:
        return iso[:8] if len(iso) >= 8 else iso


def _trace_summary(item: Dict[str, Any]) -> str:
    event = item.get("event", "")
    if event == "subagent.completed":
        return f"{item.get('agent', '?')} completed: {item.get('summary', '')}"
    if event == "approval.requested":
        appr = item.get("approval", {})
        return f"Approval required for {appr.get('capability', '?')} (tier {appr.get('risk_tier', '?')})"
    if event == "action.executed":
        result = item.get("result", {}) or {}
        proposal = result.get("proposal", {}) or {}
        cap = proposal.get("capability", "")
        if cap in {"browser.read_page", "browser.summarize", "browser.current_page"}:
            out = result.get("output", {}) or {}
            url = out.get("url") or ""
            title = out.get("title") or ""
            if title and url:
                return f"{cap}: {title} ({url})"
            if url:
                return f"{cap}: {url}"
        return result.get("summary", "Action executed")
    if event == "lesson.proposed":
        return f"Lesson proposed: {item.get('memory', {}).get('summary', '')}"
    if event.startswith("workflow."):
        wf = item.get("workflow") or {}
        suffix = event.split(".", 1)[1] if "." in event else event
        cur = wf.get("currentStep", 0)
        total = len(wf.get("steps") or [])
        pid = wf.get("patternId", "?")
        if suffix == "created":
            return f"Workflow created ({pid}) — {total} steps"
        if suffix == "waiting_for_approval":
            return f"Workflow {pid} paused at step {cur + 1}/{total} (approval required)"
        if suffix == "completed":
            return f"Workflow {pid} completed ({total} steps)"
        if suffix == "failed":
            err = wf.get("error") or "unknown reason"
            return f"Workflow {pid} failed: {err}"
        if suffix == "in_progress":
            return f"Workflow {pid} in progress (step {cur + 1}/{total})"
        return f"Workflow {pid} → {suffix}"
    if event == "plan.evaluated":
        plan = item.get("plan", {})
        status = plan.get("status")
        if status == "mapped":
            return f"Planner mapped → {plan.get('capability')} ({plan.get('matchedRule')})"
        if status == "clarification_needed":
            return f"Planner needs clarification: {plan.get('ambiguity') or 'ambiguous target'}"
        return f"Planner: unsupported — {plan.get('ambiguity') or 'no matching rule'}"
    return item.get("summary", event)


def _derive_agents(task: Any) -> List[Dict[str, Any]]:
    """Map task status to the four fixed subagent cards."""
    if task is None:
        status_map = {"planner": "idle", "researcher": "idle", "security": "idle", "verifier": "idle"}
    elif task.status.value == "blocked":
        status_map = {"planner": "done", "researcher": "done", "security": "blocked", "verifier": "idle"}
    elif task.status.value in ("running", "completed"):
        status_map = {"planner": "done", "researcher": "done", "security": "done", "verifier": "done"}
    else:
        status_map = {"planner": "idle", "researcher": "idle", "security": "idle", "verifier": "idle"}

    return [
        {"id": "planner", "label": "Planner", "role": "Task decomposition",
         "status": status_map["planner"],
         "detail": "Splits the request into research, verification, and approval checkpoints."},
        {"id": "researcher", "label": "Researcher", "role": "Evidence collection",
         "status": status_map["researcher"],
         "detail": "Collects vendor details, signatures, and installation notes."},
        {"id": "security", "label": "Security Sentinel", "role": "Risk scoring",
         "status": status_map["security"],
         "detail": "Scores actions against policy tiers and flags those requiring approval."},
        {"id": "verifier", "label": "Verifier", "role": "Outcome validation",
         "status": status_map["verifier"],
         "detail": "Runs postflight checks once execution evidence is available."},
    ]


def _build_hud_state() -> Dict[str, Any]:
    if _api is None:
        return {
            "mode": "Guarded Autonomy",
            "task": "", "transcript": "",
            "agents": _derive_agents(None),
            "approvals": [], "memory": [], "trace": [],
            "voice": {"state": "idle", "enabled": False, "transcript": None,
                      "error": None, "provider": "unavailable",
                      "lastAudioBytes": 0, "lastMime": None, "updatedAt": ""},
            "browserContext": None,
            "workflow": None,
            "degraded": True,
            "degradedReason": "Orchestrator not initialised",
        }

    supervisor = _api.supervisor
    memory = _api.memory

    task: Any = None
    if supervisor.tasks:
        task = max(supervisor.tasks.values(), key=lambda t: t.created_at)

    approvals: List[Dict[str, Any]] = []
    if task:
        for appr in task.approvals:
            d = appr.to_dict()
            approvals.append({
                "approvalId": d.get("approval_id", ""),
                "title": d.get("title", "Action Approval"),
                "tier": d.get("risk_tier", 0),
                "capability": d.get("capability", ""),
                "reason": d.get("reason", ""),
                "target": str(d.get("preview", {}).get("parameters", "")),
            })

    mem_items = [
        {
            "memoryId": m.get("memory_id", ""),
            "kind": m.get("kind", "lesson"),
            "summary": m.get("summary", ""),
            "trustScore": m.get("trust_score", 0.0),
            "status": m.get("status", "candidate"),
        }
        for m in memory.list()
    ]

    trace: List[Dict[str, Any]] = []
    if task:
        for item in task.trace[-20:]:
            trace.append({
                "time": _format_time(task.updated_at),
                "type": item.get("event", ""),
                "summary": _trace_summary(item),
            })

    # Latest action result (any task).
    latest_result: Optional[Dict[str, Any]] = None
    latest = supervisor.latest_action_result()
    if latest is not None:
        latest_result = {
            "actionId": latest.proposal.action_id,
            "capability": latest.proposal.capability,
            "status": latest.status,
            "summary": latest.summary,
            "output": latest.output,
            "verification": latest.verification,
        }

    current_plan: Optional[Dict[str, Any]] = None
    plan_action: Optional[Dict[str, Any]] = None
    workflow_view: Optional[Dict[str, Any]] = None
    if task is not None:
        ctx = getattr(task, "context", {}) or {}
        if isinstance(ctx.get("plan"), dict):
            current_plan = ctx["plan"]
        if isinstance(ctx.get("planAction"), dict):
            plan_action = ctx["planAction"]
        if isinstance(ctx.get("workflow"), dict):
            workflow_view = ctx["workflow"]

    return {
        "mode": "Guarded Autonomy",
        "task": task.objective if task else "",
        "transcript": task.objective if task else "",
        "agents": _derive_agents(task),
        "approvals": approvals,
        "memory": mem_items,
        "trace": trace,
        "latestResult": latest_result,
        "currentTaskId": task.task_id if task else None,
        "currentPlan": current_plan,
        "planAction": plan_action,
        "voice": _api.voice.snapshot(),
        "browserContext": _api.browser_context.snapshot(),
        "workflow": workflow_view,
        "degraded": False,
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

def _run_async(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _BridgeHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt: str, *args: Any) -> None:
        return  # silence default access log

    def _send_json(self, data: Any, code: int = 200) -> None:
        body = json.dumps(data).encode()
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            # The desktop client timed out or disconnected while a long-running
            # operation was in flight. Treat this as a benign disconnect.
            return

    def _send_error_json(self, code: int, message: str) -> None:
        self._send_json({"error": message}, code)

    def _read_json_body(self) -> Optional[Dict[str, Any]]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except Exception:
            return None

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/")

        if path == "/health":
            self._send_json({"status": "ok", "port": PORT})
        elif path == "/hud-state":
            try:
                self._send_json(_build_hud_state())
            except Exception as exc:
                self._send_error_json(500, str(exc))
        elif path == "/memory":
            try:
                items = _api.memory.list() if _api else []
                self._send_json({"items": items})
            except Exception as exc:
                self._send_error_json(500, str(exc))
        elif path.startswith("/tasks/") and path.endswith("/trace"):
            task_id = path[len("/tasks/"):-len("/trace")]
            try:
                trace = _api.supervisor.fetch_trace(task_id) if _api else []
                self._send_json({"task_id": task_id, "trace": trace})
            except KeyError:
                self._send_error_json(404, f"Task {task_id!r} not found")
            except Exception as exc:
                self._send_error_json(500, str(exc))
        elif path == "/voice":
            if _api is None:
                self._send_error_json(503, "Orchestrator not initialised")
                return
            self._send_json(_api.voice.snapshot())
        elif path == "/browser/context":
            if _api is None:
                self._send_error_json(503, "Orchestrator not initialised")
                return
            self._send_json({"context": _api.browser_context.snapshot()})
        elif path == "/approvals":
            if _api is None:
                self._send_error_json(503, "Orchestrator not initialised")
                return
            self._send_json({"items": _api.supervisor.list_pending_approvals()})
        elif path.startswith("/actions/"):
            action_id = path[len("/actions/"):]
            if _api is None:
                self._send_error_json(503, "Orchestrator not initialised")
                return
            result = _api.supervisor.get_action_result(action_id)
            if result is None:
                self._send_error_json(404, f"Action {action_id!r} not found")
                return
            self._send_json(result.to_dict())
        else:
            self._send_error_json(404, f"Unknown route: {path}")

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")

        if path == "/tasks":
            body = self._read_json_body()
            if body is None:
                self._send_error_json(400, "Invalid JSON body")
                return
            objective = (body.get("objective") or "").strip()
            if not objective:
                self._send_error_json(400, "Field 'objective' is required")
                return
            if _api is None:
                self._send_error_json(503, "Orchestrator not initialised")
                return
            try:
                task = _run_async(_api.submit_voice_or_text_task(objective, source="hud-text"))
                self._send_json(task.to_dict(), 201)
            except Exception as exc:
                self._send_error_json(500, str(exc))

        elif path == "/actions/propose":
            self._handle_propose_action()
        elif path == "/actions/execute":
            self._handle_execute_action()
        elif path == "/actions/deny":
            self._handle_deny_action()

        elif path == "/voice/start":
            self._handle_voice_simple("start")
        elif path == "/voice/stop":
            self._handle_voice_stop()
        elif path == "/voice/submit":
            self._handle_voice_submit()
        elif path == "/voice/discard":
            self._handle_voice_simple("discard")
        elif path == "/voice/reset":
            self._handle_voice_simple("reset")
        elif path == "/voice/enable":
            self._handle_voice_enable()

        elif path == "/browser/snapshot":
            self._handle_browser_snapshot()
        elif path == "/browser/clear":
            self._handle_browser_clear()

        else:
            self._send_error_json(404, f"Unknown route: {path}")

    # ------------------------------------------------------------------
    # Browser context handlers
    # ------------------------------------------------------------------
    def _handle_browser_snapshot(self) -> None:
        """Explicit, user-initiated push of the current page into context.

        The HUD (or any local tool) can send the URL/title/text it
        observes, and the planner will treat it as the 'current page'.
        We do NOT fetch anything on the user's behalf here — this is
        purely a record of what the caller supplied.
        """
        if _api is None:
            self._send_error_json(503, "Orchestrator not initialised")
            return
        body = self._read_json_body()
        if body is None:
            self._send_error_json(400, "Invalid JSON body")
            return
        url = (body.get("url") or "").strip()
        if not url:
            self._send_error_json(400, "Field 'url' is required")
            return
        try:
            snap = _api.browser_context.record_page(
                url=url,
                title=body.get("title"),
                text_excerpt=body.get("text") or body.get("textExcerpt"),
                byte_count=int(body.get("byteCount") or 0),
                source="hud.snapshot",
            )
        except ValueError as exc:
            self._send_error_json(400, str(exc))
            return
        self._send_json({"context": snap})

    def _handle_browser_clear(self) -> None:
        if _api is None:
            self._send_error_json(503, "Orchestrator not initialised")
            return
        _api.browser_context.clear()
        self._send_json({"context": _api.browser_context.snapshot()})

    # ------------------------------------------------------------------
    # Action workflow handlers
    # ------------------------------------------------------------------
    def _handle_propose_action(self) -> None:
        if _api is None:
            self._send_error_json(503, "Orchestrator not initialised")
            return
        body = self._read_json_body() or {}
        capability = (body.get("capability") or "").strip()
        parameters = body.get("parameters") or {}
        if not capability:
            self._send_error_json(400, "Field 'capability' is required")
            return
        if not isinstance(parameters, dict):
            self._send_error_json(400, "Field 'parameters' must be an object")
            return

        supervisor = _api.supervisor
        task_id = body.get("task_id")
        # If no task_id, auto-create an ad-hoc task so action proposals work standalone.
        try:
            if not task_id:
                intent = body.get("intent") or f"Ad-hoc action: {capability}"
                task = _run_async(_api.submit_voice_or_text_task(intent, source="hud-action"))
                task_id = task.task_id
            elif task_id not in supervisor.tasks:
                self._send_error_json(404, f"Unknown task_id: {task_id}")
                return

            from .models import ActionProposal
            proposal = ActionProposal(
                task_id=task_id,
                capability=capability,
                intent=body.get("intent") or capability,
                parameters=parameters,
                requested_by=body.get("requested_by") or "hud",
                evidence=body.get("evidence") or ["hud-form"],
                confidence=float(body.get("confidence", 0.9)),
                dry_run=bool(body.get("dry_run", False)),
            )
            outcome = supervisor.propose_action(proposal)
            outcome["task_id"] = task_id
            self._send_json(outcome, 201)
        except KeyError as exc:
            self._send_error_json(404, str(exc))
        except (ValueError, TypeError) as exc:
            self._send_error_json(400, str(exc))
        except Exception as exc:
            self._send_error_json(500, str(exc))

    def _handle_execute_action(self) -> None:
        if _api is None:
            self._send_error_json(503, "Orchestrator not initialised")
            return
        body = self._read_json_body() or {}
        approval_id = body.get("approval_id")
        if not approval_id:
            self._send_error_json(400, "Field 'approval_id' is required")
            return
        try:
            # Route through the API wrapper so any owning workflow also
            # advances / pauses / fails alongside the supervisor action.
            result = _api.approve_and_execute(approval_id)
            self._send_json({"result": result.to_dict(),
                             "verification": result.verification}, 200)
        except KeyError as exc:
            self._send_error_json(404, str(exc))
        except Exception as exc:
            self._send_error_json(500, str(exc))

    def _handle_deny_action(self) -> None:
        if _api is None:
            self._send_error_json(503, "Orchestrator not initialised")
            return
        body = self._read_json_body() or {}
        approval_id = body.get("approval_id")
        reason = (body.get("reason") or "").strip()
        if not approval_id:
            self._send_error_json(400, "Field 'approval_id' is required")
            return
        try:
            payload = _api.deny_approval(approval_id, reason=reason)
            self._send_json(payload, 200)
        except KeyError as exc:
            self._send_error_json(404, str(exc))
        except Exception as exc:
            self._send_error_json(500, str(exc))


    # ------------------------------------------------------------------
    # Voice workflow handlers
    # ------------------------------------------------------------------
    def _voice_ok(self) -> bool:
        if _api is None:
            self._send_error_json(503, "Orchestrator not initialised")
            return False
        return True

    def _handle_voice_simple(self, action: str) -> None:
        if not self._voice_ok():
            return
        try:
            if action == "start":
                snap = _api.voice.start()
            elif action == "discard":
                snap = _api.voice.discard()
            elif action == "reset":
                snap = _api.voice.reset()
            else:
                self._send_error_json(400, f"Unknown voice action: {action}")
                return
            self._send_json(snap)
        except VoiceError as exc:
            self._send_error_json(409, str(exc))
        except Exception as exc:
            self._send_error_json(500, str(exc))

    def _handle_voice_stop(self) -> None:
        if not self._voice_ok():
            return
        body = self._read_json_body() or {}
        audio_b64 = body.get("audio_base64") or ""
        mime = (body.get("mime") or "audio/webm").strip()
        try:
            audio_bytes = base64.b64decode(audio_b64, validate=False) if audio_b64 else b""
        except (binascii.Error, ValueError) as exc:
            self._send_error_json(400, f"Invalid audio_base64: {exc}")
            return
        try:
            snap = _api.voice.stop(audio_bytes, mime=mime)
            self._send_json(snap)
        except VoiceError as exc:
            self._send_error_json(409, str(exc))
        except Exception as exc:
            self._send_error_json(500, str(exc))

    def _handle_voice_submit(self) -> None:
        if not self._voice_ok():
            return
        body = self._read_json_body() or {}
        override = body.get("transcript")
        create_task = bool(body.get("create_task", True))
        try:
            text = _api.voice.consume_transcript(override=override)
        except VoiceError as exc:
            self._send_error_json(409, str(exc))
            return
        if not create_task:
            self._send_json({"transcript": text, "task": None})
            return
        try:
            task = _run_async(_api.submit_voice_or_text_task(text, source="hud-voice"))
            self._send_json({"transcript": text, "task": task.to_dict()}, 201)
        except Exception as exc:
            self._send_error_json(500, str(exc))

    def _handle_voice_enable(self) -> None:
        if not self._voice_ok():
            return
        body = self._read_json_body() or {}
        enabled = bool(body.get("enabled", True))
        self._send_json(_api.voice.set_enabled(enabled))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_server(api: Any, port: int = PORT, *, daemon: bool = True) -> ThreadingHTTPServer:
    """Initialise the bridge with a LocalSupervisorAPI and start serving.

    Returns the HTTPServer instance (call .shutdown() to stop).
    """
    global _api
    _api = api

    server = ThreadingHTTPServer(("127.0.0.1", port), _BridgeHandler)
    threading.Thread(target=server.serve_forever, name="jarvis-bridge", daemon=daemon).start()
    return server
