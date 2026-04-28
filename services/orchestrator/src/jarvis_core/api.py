from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

from .browser_context import BrowserContext
from .capabilities import (
    ApplicationCapability,
    BrowserCapability,
    DesktopCapability,
    FilesystemCapability,
)
from .event_log import SignedEventLog
from .gateway import ActionGateway
from .memory import MemoryStore
from .models import ActionProposal
from .ocr_providers import build_ocr_provider_from_env
from .planner import MAPPED, DeterministicPlanner, PlanResult
from .policy import PolicyEngine
from .reflection import ApprovedMemoryHints
from .supervisor import SupervisorRuntime
from .voice import VoiceSession
from .voice_providers import build_provider_from_env
from .workflow import WorkflowPlanner, WorkflowRunner


_AUDIT_KEY_FILENAME = "audit.key"


def _load_or_create_audit_secret(runtime_path: Path) -> str:
    """Return the HMAC secret used to sign the local audit log.

    Resolution order:
      1. ``JARVIS_AUDIT_SECRET`` environment variable, if non-empty.
      2. ``<runtime>/audit.key`` if it exists.
      3. Otherwise, generate a fresh 256-bit hex secret with
         ``secrets.token_hex(32)`` and persist it to ``<runtime>/audit.key``.

    The runtime directory is git-ignored, so generated keys never leave the
    machine. Deleting ``audit.key`` (or rotating the env var) invalidates
    verification of pre-existing local audit logs — by design, since the
    chain is HMAC-bound to the secret.
    """
    env_secret = os.environ.get("JARVIS_AUDIT_SECRET", "").strip()
    if env_secret:
        return env_secret

    runtime_path = Path(runtime_path)
    runtime_path.mkdir(parents=True, exist_ok=True)
    key_path = runtime_path / _AUDIT_KEY_FILENAME

    if key_path.exists():
        existing = key_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    new_secret = secrets.token_hex(32)
    key_path.write_text(new_secret, encoding="utf-8")
    try:
        # Best-effort tighten on POSIX; on Windows os.chmod is largely a no-op
        # but harmless. Real protection comes from runtime/ being git-ignored.
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return new_secret


class LocalSupervisorAPI:
    """Convenience API for local tools, tests, and future IPC bridges."""

    def __init__(self, root: Path) -> None:
        root = Path(root)
        config_path = root / "configs" / "policy.default.json"
        runtime_path = root / "runtime"
        sandbox_path = runtime_path / "sandbox"
        screenshots_path = runtime_path / "screenshots"
        self.screenshots_root = screenshots_path

        audit_secret = _load_or_create_audit_secret(runtime_path)
        self.event_log = SignedEventLog(runtime_path / "events.jsonl", secret=audit_secret)
        self.memory = MemoryStore(runtime_path / "memory")
        self.policy = PolicyEngine(config_path)
        # Shared, in-process browser context. Populated by
        # browser.read_page / browser.summarize or by an explicit HUD
        # snapshot — never by silent scraping.
        self.browser_context = BrowserContext()
        self.gateway = ActionGateway(
            self.policy,
            self.event_log,
            adapters=[
                BrowserCapability(sandbox_root=sandbox_path,
                                  context=self.browser_context),
                FilesystemCapability(sandbox_root=sandbox_path,
                                     read_roots=[root]),
                ApplicationCapability(),
                DesktopCapability(
                    screenshots_dir=screenshots_path,
                    # Default is the explicit "no OCR configured" provider.
                    # Set JARVIS_OCR_PROVIDER=windows-media-ocr (after `pip
                    # install winsdk` and adding a Windows OCR language pack)
                    # or =auto to enable real local OCR. See ocr_providers.py.
                    ocr_provider=build_ocr_provider_from_env(),
                ),
            ],
            workspace_root=root,
            sandbox_root=sandbox_path,
        )
        self.supervisor = SupervisorRuntime(self.gateway, self.memory, self.event_log)
        # Voice session is off-by-state (idle) and requires explicit start().
        # No microphone access happens here; the HUD is the only recorder.
        # The transcription provider is selected from environment variables
        # (see voice_providers.build_provider_from_env) and defaults to the
        # clearly-labelled stub so nothing real happens without opt-in.
        self.voice = VoiceSession(provider=build_provider_from_env())
        # Deterministic, LLM-free command interpreter. See planner.py.
        # The planner receives an ``ApprovedMemoryHints`` view so plans
        # can be annotated with relevant approved memory — but memory
        # never changes the chosen capability, parameters, or
        # confidence. Policy is still authoritative.
        self.planner = DeterministicPlanner(
            memory_hint_provider=ApprovedMemoryHints(self.memory),
        )
        # Bounded multi-step workflow layer. Narrow set of v1 patterns
        # (see workflow.py). Runs each step via supervisor.propose_action,
        # so ActionGateway/PolicyEngine/approvals/audit still apply.
        self.workflow_planner = WorkflowPlanner()
        self.workflow_runner = WorkflowRunner(self.supervisor.propose_action)

    async def submit_voice_or_text_task(self, objective: str, source: str = "text", context: Optional[Dict[str, object]] = None):
        task = await self.supervisor.submit_task(objective=objective, source=source, context=context)

        # 1) Try the bounded workflow planner first. If it matches, we
        #    drive a finite, explicit sequence of structured actions
        #    through the existing supervisor.propose_action path.
        wf_plan = self.workflow_planner.plan(objective)
        if wf_plan is not None:
            workflow = self.workflow_runner.create(task.task_id, objective, wf_plan)
            task.context["workflow"] = workflow.to_dict()
            task.trace.append({
                "event": "workflow.created",
                "workflow": workflow.to_dict(),
            })
            self.event_log.append("workflow.created", {
                "task_id": task.task_id,
                "workflow": workflow.to_dict(),
            })
            task.touch()
            self.workflow_runner.start(workflow)
            task.context["workflow"] = workflow.to_dict()
            task.trace.append({
                "event": f"workflow.{workflow.status}",
                "workflow": workflow.to_dict(),
            })
            task.touch()
            self.event_log.append(f"workflow.{workflow.status}", {
                "task_id": task.task_id,
                "workflow": workflow.to_dict(),
            })
            return task

        # 2) Fall back to the single-step deterministic planner.
        plan = self.planner.plan(
            objective,
            has_browser_context=self.browser_context.has_context(),
        )
        task.context["plan"] = plan.to_dict()
        task.trace.append({"event": "plan.evaluated", "plan": plan.to_dict()})
        task.touch()
        self.event_log.append("plan.evaluated", {
            "task_id": task.task_id,
            "plan": plan.to_dict(),
        })

        if plan.status == MAPPED and plan.capability:
            # Auto-propose through the EXISTING gateway path. Tier 0 runs
            # immediately; Tier 1 conditional runs if confidence is high;
            # Tier 2 queues an approval; blocked patterns are refused.
            # Nothing bypasses ActionGateway or PolicyEngine.
            self._auto_propose_from_plan(task.task_id, plan)

        return task

    # ------------------------------------------------------------------
    # Workflow-aware approval hooks. The HUD/bridge always go through
    # these so a single approval decision both unblocks the supervisor
    # AND advances any owning workflow.
    # ------------------------------------------------------------------
    def approve_and_execute(self, approval_id: str):
        wf = self.workflow_runner.lookup_by_approval(approval_id)
        result = self.supervisor.approve_and_execute(approval_id)
        if wf is not None:
            self.workflow_runner.resume_after_approval(approval_id)
            self.workflow_runner.mark_step_executed(wf, result)
            task = self.supervisor.tasks.get(wf.task_id)
            if wf.status != "failed":
                self.workflow_runner.continue_(wf)
            if task is not None:
                task.context["workflow"] = wf.to_dict()
                task.trace.append({
                    "event": f"workflow.{wf.status}",
                    "workflow": wf.to_dict(),
                })
                task.touch()
                self.event_log.append(f"workflow.{wf.status}", {
                    "task_id": wf.task_id,
                    "workflow": wf.to_dict(),
                })
        return result

    def deny_approval(self, approval_id: str, reason: str = ""):
        wf = self.workflow_runner.lookup_by_approval(approval_id)
        payload = self.supervisor.deny_approval(approval_id, reason=reason)
        if wf is not None:
            self.workflow_runner.halt_after_denial(approval_id, reason=reason)
            task = self.supervisor.tasks.get(wf.task_id)
            if task is not None:
                task.context["workflow"] = wf.to_dict()
                task.trace.append({
                    "event": "workflow.failed",
                    "workflow": wf.to_dict(),
                })
                task.touch()
                self.event_log.append("workflow.failed", {
                    "task_id": wf.task_id,
                    "workflow": wf.to_dict(),
                })
        return payload

    def submit_action(self, proposal: ActionProposal, approved: bool = False):
        return self.supervisor.request_action(proposal, approved=approved)

    # ------------------------------------------------------------------
    def _auto_propose_from_plan(self, task_id: str, plan: PlanResult) -> Dict[str, Any]:
        proposal = ActionProposal(
            task_id=task_id,
            capability=plan.capability or "",
            intent=f"auto-planned: {plan.matched_rule or plan.capability}",
            parameters=dict(plan.parameters),
            requested_by="planner",
            evidence=["planner", plan.matched_rule or "unknown-rule",
                      plan.rationale or ""],
            confidence=float(plan.confidence),
            dry_run=False,
        )
        try:
            outcome = self.supervisor.propose_action(proposal)
        except Exception as exc:  # pragma: no cover — defensive
            task = self.supervisor.tasks.get(task_id)
            if task is not None:
                task.trace.append({
                    "event": "plan.proposal_failed",
                    "error": str(exc),
                    "capability": plan.capability,
                })
                task.touch()
            return {"status": "error", "error": str(exc)}
        # Record the linkage so the HUD can show "auto-planned → <capability>".
        task = self.supervisor.tasks.get(task_id)
        if task is not None:
            task.context["planAction"] = {
                "actionId": proposal.action_id,
                "capability": proposal.capability,
                "status": outcome.get("status"),
                "autoProposed": True,
            }
            task.touch()
        return outcome
