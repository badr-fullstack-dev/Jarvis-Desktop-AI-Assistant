"""Bounded multi-step workflow orchestration.

A :class:`Workflow` is a *finite*, *ordered*, *explicit* list of
:class:`WorkflowStep` instances. It is NOT an agent loop. There is no
open-ended planning, no retry budget, no crawling. Every step is a
structured :class:`ActionProposal` that still flows through
``SupervisorRuntime.propose_action`` — meaning ActionGateway,
PolicyEngine, approvals, blocked patterns, trace, and signed audit all
continue to apply exactly as they do for single-step tasks.

Scope of v1
-----------
The :class:`WorkflowPlanner` only recognises a narrow set of phrasings.
Anything else returns ``None`` and the caller falls back to the
single-step deterministic planner. Unsupported multi-step phrasings
do NOT get improvised — that is the whole point.

States
------
Workflow-level:
    queued, in_progress, waiting_for_approval, blocked, completed, failed

Step-level:
    pending, running, waiting_for_approval, completed, failed,
    blocked, skipped
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .models import ActionProposal, ActionResult, new_id, utc_now


# ---------------------------------------------------------------------------
# State constants
# ---------------------------------------------------------------------------

WF_QUEUED = "queued"
WF_IN_PROGRESS = "in_progress"
WF_WAITING = "waiting_for_approval"
WF_BLOCKED = "blocked"
WF_COMPLETED = "completed"
WF_FAILED = "failed"

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_WAITING = "waiting_for_approval"
STEP_COMPLETED = "completed"
STEP_FAILED = "failed"
STEP_BLOCKED = "blocked"
STEP_SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class WorkflowStep:
    index: int
    capability: str
    parameters: Dict[str, Any]
    intent: str
    status: str = STEP_PENDING
    action_id: Optional[str] = None
    result_summary: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index,
            "capability": self.capability,
            "parameters": dict(self.parameters),
            "intent": self.intent,
            "status": self.status,
            "actionId": self.action_id,
            "resultSummary": self.result_summary,
            "error": self.error,
        }


@dataclass(slots=True)
class Workflow:
    workflow_id: str
    task_id: str
    objective: str
    pattern_id: str
    steps: List[WorkflowStep]
    status: str = WF_QUEUED
    current_step: int = 0
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    error: Optional[str] = None

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflowId": self.workflow_id,
            "taskId": self.task_id,
            "objective": self.objective,
            "patternId": self.pattern_id,
            "status": self.status,
            "currentStep": self.current_step,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "error": self.error,
            "steps": [s.to_dict() for s in self.steps],
        }


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------

# Minimal URL sniff — reuse a restrained pattern; we only need a yes/no
# for workflow shape matching. The single-step planner performs the
# canonical URL normalisation for the action parameters.
_URL_RE = re.compile(
    r"^(?:https?://[^\s]+"
    r"|(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2}[a-z0-9.-]*(?:/[^\s]*)?)$",
    re.IGNORECASE,
)


def _normalize_url(raw: str) -> str:
    raw = raw.strip().rstrip(".,;")
    if re.match(r"^[a-z][a-z0-9+.-]*://", raw, re.IGNORECASE):
        return raw
    return "https://" + raw


def _is_sandbox_path(p: str) -> bool:
    norm = p.replace("\\", "/").lstrip("./")
    if norm.startswith("runtime/sandbox/") or norm.startswith("sandbox/"):
        return True
    return "/runtime/sandbox/" in norm or "/sandbox/" in norm


@dataclass(slots=True)
class WorkflowPlan:
    pattern_id: str
    steps: List[WorkflowStep]
    rationale: str


class WorkflowPlanner:
    """Recognises a narrow set of bounded multi-step phrasings.

    Returns ``None`` for anything else — the caller then falls back to
    the single-step planner. Never guesses.
    """

    def plan(self, text: str) -> Optional[WorkflowPlan]:
        t = re.sub(r"\s+", " ", (text or "").strip().rstrip(".?!"))
        if not t:
            return None
        for rule in (
            self._open_and_read,
            self._open_and_summarize,
            self._read_then_summarize,
            self._write_then_read,
            self._open_and_focus,
            self._copy_and_notify,
        ):
            plan = rule(t)
            if plan is not None:
                return plan
        return None

    # ------------------------------------------------------------------

    def _open_and_read(self, text: str) -> Optional[WorkflowPlan]:
        m = re.match(
            r"^(?:open|go\s+to|navigate\s+to|visit|browse\s+to)\s+(\S+)\s+and\s+read(?:\s+it)?$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        raw = m.group(1)
        if not _URL_RE.match(raw):
            return None
        url = _normalize_url(raw)
        return WorkflowPlan(
            pattern_id="wf.open_and_read",
            rationale=f"open {url} and read it → navigate then read_page",
            steps=[
                WorkflowStep(index=0, capability="browser.navigate",
                             parameters={"url": url},
                             intent=f"navigate to {url}"),
                WorkflowStep(index=1, capability="browser.read_page",
                             parameters={"url": url},
                             intent=f"read {url}"),
            ],
        )

    def _open_and_summarize(self, text: str) -> Optional[WorkflowPlan]:
        m = re.match(
            r"^(?:open|go\s+to|navigate\s+to|visit|browse\s+to)\s+(\S+)\s+and\s+summari[sz]e(?:\s+it)?$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        raw = m.group(1)
        if not _URL_RE.match(raw):
            return None
        url = _normalize_url(raw)
        return WorkflowPlan(
            pattern_id="wf.open_and_summarize",
            rationale=f"open {url} and summarize it → navigate then summarize",
            steps=[
                WorkflowStep(index=0, capability="browser.navigate",
                             parameters={"url": url},
                             intent=f"navigate to {url}"),
                WorkflowStep(index=1, capability="browser.summarize",
                             parameters={"url": url},
                             intent=f"summarize {url}"),
            ],
        )

    def _read_then_summarize(self, text: str) -> Optional[WorkflowPlan]:
        # "read <url> then summarize this page"
        # "read <url> and then summarize the current page"
        m = re.match(
            r"^(?:read|fetch)\s+(\S+)\s+(?:then|and\s+then)\s+"
            r"summari[sz]e\s+(?:this|the|the\s+current|current)\s+page$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        raw = m.group(1)
        if not _URL_RE.match(raw):
            return None
        url = _normalize_url(raw)
        return WorkflowPlan(
            pattern_id="wf.read_then_summarize",
            rationale=f"read {url} then summarize current page → read_page then summarize(context)",
            steps=[
                WorkflowStep(index=0, capability="browser.read_page",
                             parameters={"url": url},
                             intent=f"read {url}"),
                WorkflowStep(index=1, capability="browser.summarize",
                             parameters={"use_context": True},
                             intent="summarize current page (from context)"),
            ],
        )

    def _write_then_read(self, text: str) -> Optional[WorkflowPlan]:
        # "write <content> to <path> then read it back"
        # "save <content> to <path> and then read it"
        m = re.match(
            r"^(?:write|save|put)\s+(.+?)\s+(?:to|into)\s+(\S+)\s+"
            r"(?:then|and\s+then|and)\s+read(?:\s+it(?:\s+back)?)?$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        content = m.group(1).strip()
        path = m.group(2).strip()
        if len(content) >= 2 and content[0] == content[-1] and content[0] in ("'", '"'):
            content = content[1:-1]
        if not _is_sandbox_path(path):
            return None
        return WorkflowPlan(
            pattern_id="wf.write_then_read",
            rationale=f"write to {path} then read back → filesystem.write then filesystem.read",
            steps=[
                WorkflowStep(index=0, capability="filesystem.write",
                             parameters={"path": path, "content": content},
                             intent=f"write {len(content)} chars to {path}"),
                WorkflowStep(index=1, capability="filesystem.read",
                             parameters={"path": path},
                             intent=f"read back {path} for verification"),
            ],
        )


    # ------------------------------------------------------------------
    # Desktop patterns
    # ------------------------------------------------------------------

    # Same allowlist mirror as the planner — name-only; the adapter
    # still enforces the real check.
    _APP_ALIASES: Dict[str, str] = {
        "notepad": "notepad", "calculator": "calculator", "calc": "calc",
        "explorer": "explorer", "file explorer": "explorer", "files": "explorer",
        "paint": "mspaint", "mspaint": "mspaint", "ms paint": "mspaint",
    }

    def _open_and_focus(self, text: str) -> Optional[WorkflowPlan]:
        # "open notepad then focus it" / "launch notepad and then focus it"
        m = re.match(
            r"^(?:open|launch|start|run)\s+(?:the\s+)?([a-z][a-z \-]*?)\s+"
            r"(?:then|and(?:\s+then)?)\s+focus(?:\s+it)?$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        name_raw = m.group(1).strip().lower()
        app = self._APP_ALIASES.get(name_raw)
        if app is None:
            return None
        return WorkflowPlan(
            pattern_id="wf.open_and_focus",
            rationale=f"open {app} then focus → app.launch then app.focus",
            steps=[
                WorkflowStep(index=0, capability="app.launch",
                             parameters={"name": app},
                             intent=f"launch {app}"),
                WorkflowStep(index=1, capability="app.focus",
                             parameters={"name": app},
                             intent=f"focus {app}"),
            ],
        )

    def _copy_and_notify(self, text: str) -> Optional[WorkflowPlan]:
        # "copy <text> to clipboard then notify me" /
        # "copy <text> to clipboard and notify me saying <msg>"
        m = re.match(
            r"^(?:copy|put)\s+(.+?)\s+(?:to|on|into)\s+(?:the\s+|my\s+)?clipboard\s+"
            r"(?:then|and(?:\s+then)?)\s+notify(?:\s+me)?"
            r"(?:\s+(?:saying|with|that\s+says)\s+(.+?))?$",
            text, re.IGNORECASE,
        )
        if not m:
            return None
        content = m.group(1).strip()
        if len(content) >= 2 and content[0] == content[-1] and content[0] in ("'", '"'):
            content = content[1:-1]
        message = (m.group(2) or "Copied to clipboard.").strip()
        if len(message) >= 2 and message[0] == message[-1] and message[0] in ("'", '"'):
            message = message[1:-1]
        if not content:
            return None
        return WorkflowPlan(
            pattern_id="wf.copy_and_notify",
            rationale="copy <text> then notify → desktop.clipboard_write then desktop.notify",
            steps=[
                WorkflowStep(index=0, capability="desktop.clipboard_write",
                             parameters={"text": content},
                             intent=f"copy {len(content)} chars to clipboard"),
                WorkflowStep(index=1, capability="desktop.notify",
                             parameters={"title": "Jarvis", "message": message},
                             intent=f"notify: {message[:60]}"),
            ],
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

# Type alias for the supervisor callback we need. Avoids a hard import
# and makes the runner easy to unit-test with a fake.
ProposeFn = Callable[[ActionProposal], Dict[str, Any]]
ExecuteAfterApprovalFn = Callable[[str], ActionResult]


class WorkflowRunner:
    """Drives a :class:`Workflow` through the supervisor one step at a time.

    The runner does not execute anything itself. It hands each step to
    ``propose_fn`` (``SupervisorRuntime.propose_action``) and reacts to
    the outcome:

    * ``executed``            → step completed, advance.
    * ``awaiting_approval``   → workflow pauses, remember the approval.
    * ``blocked``             → workflow fails (policy refused).
    * anything else           → workflow fails with the reported reason.

    On a paused workflow, call :meth:`resume_after_approval` with the
    approval_id that has just been granted. Call :meth:`halt_after_denial`
    with a denied approval_id to mark the workflow failed.
    """

    def __init__(
        self,
        propose_fn: ProposeFn,
        *,
        task_id_for: Optional[Callable[[str], str]] = None,
    ) -> None:
        self._propose = propose_fn
        self._task_id_for = task_id_for  # unused reserved hook
        self.workflows: Dict[str, Workflow] = {}
        # Map action_id → workflow_id so approval/deny callbacks can
        # find which workflow (if any) the action belongs to.
        self._action_to_workflow: Dict[str, str] = {}
        # Map approval_id → workflow_id, populated when a step pauses.
        self._approval_to_workflow: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create(self, task_id: str, objective: str, plan: WorkflowPlan) -> Workflow:
        # Copy steps so callers can't mutate the plan afterwards.
        steps = [
            WorkflowStep(
                index=s.index,
                capability=s.capability,
                parameters=dict(s.parameters),
                intent=s.intent,
                status=STEP_PENDING,
            )
            for s in plan.steps
        ]
        wf = Workflow(
            workflow_id=new_id("workflow"),
            task_id=task_id,
            objective=objective,
            pattern_id=plan.pattern_id,
            steps=steps,
        )
        self.workflows[wf.workflow_id] = wf
        return wf

    def get(self, workflow_id: str) -> Optional[Workflow]:
        return self.workflows.get(workflow_id)

    def latest(self) -> Optional[Workflow]:
        if not self.workflows:
            return None
        return next(reversed(self.workflows.values()))

    def lookup_by_action(self, action_id: str) -> Optional[Workflow]:
        wf_id = self._action_to_workflow.get(action_id)
        return self.workflows.get(wf_id) if wf_id else None

    def lookup_by_approval(self, approval_id: str) -> Optional[Workflow]:
        wf_id = self._approval_to_workflow.get(approval_id)
        return self.workflows.get(wf_id) if wf_id else None

    # ------------------------------------------------------------------
    # Drive
    # ------------------------------------------------------------------

    def start(self, workflow: Workflow) -> Workflow:
        if workflow.status != WF_QUEUED:
            return workflow
        workflow.status = WF_IN_PROGRESS
        workflow.touch()
        return self._drive(workflow)

    def resume_after_approval(self, approval_id: str) -> Optional[Workflow]:
        """Call after ``supervisor.approve_and_execute(approval_id)`` succeeds.

        The supervisor has already executed the paused step. This method
        updates the step record from the resulting ActionResult (looked
        up by the caller) and advances to the next step.
        """
        wf = self.lookup_by_approval(approval_id)
        if wf is None:
            return None
        self._approval_to_workflow.pop(approval_id, None)
        return wf  # caller calls mark_step_executed then continue()

    def halt_after_denial(self, approval_id: str, reason: str = "") -> Optional[Workflow]:
        wf = self.lookup_by_approval(approval_id)
        if wf is None:
            return None
        self._approval_to_workflow.pop(approval_id, None)
        step = wf.steps[wf.current_step]
        step.status = STEP_FAILED
        step.error = f"Approval denied: {reason or 'no reason given'}"
        wf.status = WF_FAILED
        wf.error = step.error
        wf.touch()
        return wf

    def mark_step_executed(self, workflow: Workflow, result: ActionResult) -> Workflow:
        """Record a post-approval executed result onto the current step."""
        step = workflow.steps[workflow.current_step]
        if result.status == "executed":
            step.status = STEP_COMPLETED
            step.result_summary = result.summary
        elif result.status == "blocked":
            step.status = STEP_BLOCKED
            step.error = result.summary
            workflow.status = WF_FAILED
            workflow.error = f"Step {workflow.current_step} blocked by policy: {result.summary}"
            workflow.touch()
            return workflow
        else:
            step.status = STEP_FAILED
            step.error = result.summary or f"status={result.status}"
            workflow.status = WF_FAILED
            workflow.error = step.error
            workflow.touch()
            return workflow
        workflow.touch()
        return workflow

    def continue_(self, workflow: Workflow) -> Workflow:
        """Advance past the current (completed) step and drive the rest."""
        if workflow.status == WF_FAILED:
            return workflow
        workflow.current_step += 1
        workflow.status = WF_IN_PROGRESS
        workflow.touch()
        return self._drive(workflow)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _drive(self, workflow: Workflow) -> Workflow:
        while workflow.current_step < len(workflow.steps):
            step = workflow.steps[workflow.current_step]
            step.status = STEP_RUNNING
            workflow.touch()

            proposal = ActionProposal(
                task_id=workflow.task_id,
                capability=step.capability,
                intent=f"workflow[{workflow.pattern_id}] step {step.index}: {step.intent}",
                parameters=dict(step.parameters),
                requested_by="workflow",
                evidence=["workflow", workflow.pattern_id, step.intent],
                confidence=0.9,
                dry_run=False,
            )
            step.action_id = proposal.action_id
            self._action_to_workflow[proposal.action_id] = workflow.workflow_id

            try:
                outcome = self._propose(proposal)
            except Exception as exc:  # pragma: no cover — defensive
                step.status = STEP_FAILED
                step.error = f"proposal error: {exc}"
                workflow.status = WF_FAILED
                workflow.error = step.error
                workflow.touch()
                return workflow

            status = outcome.get("status")

            if status == "executed":
                result_dict = outcome.get("result") or {}
                step.status = STEP_COMPLETED
                step.result_summary = result_dict.get("summary", "")
                workflow.current_step += 1
                workflow.touch()
                continue

            if status == "awaiting_approval":
                approval = outcome.get("approval") or {}
                approval_id = approval.get("approval_id") or approval.get("approvalId")
                step.status = STEP_WAITING
                if approval_id:
                    self._approval_to_workflow[approval_id] = workflow.workflow_id
                workflow.status = WF_WAITING
                workflow.touch()
                return workflow

            if status == "blocked":
                step.status = STEP_BLOCKED
                decision = outcome.get("decision") or {}
                step.error = decision.get("reason") or "blocked by policy"
                workflow.status = WF_FAILED
                workflow.error = f"Step {step.index} blocked: {step.error}"
                workflow.touch()
                return workflow

            # Any other status (e.g. "failed"): mark the step failed and stop.
            step.status = STEP_FAILED
            step.error = outcome.get("error") or (
                (outcome.get("result") or {}).get("summary")
            ) or f"unexpected status={status!r}"
            workflow.status = WF_FAILED
            workflow.error = step.error
            workflow.touch()
            return workflow

        # All steps consumed without a pause or failure.
        workflow.status = WF_COMPLETED
        workflow.touch()
        return workflow


