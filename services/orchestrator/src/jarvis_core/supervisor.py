from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .blackboard import Blackboard
from .event_log import SignedEventLog
from .gateway import ActionGateway
from .memory import MemoryStore
from .models import ActionProposal, ActionResult, TaskRecord, TaskStatus
from .reflection import Reflector
from .subagents import AgentOutput, default_subagents


class SupervisorRuntime:
    """Owns task state, subagent coordination, and capability execution."""

    def __init__(self, gateway: ActionGateway, memory: MemoryStore, event_log: SignedEventLog) -> None:
        self.gateway = gateway
        self.memory = memory
        self.event_log = event_log
        self.reflector = Reflector(memory)
        self.tasks: Dict[str, TaskRecord] = {}
        # Indexes for first-class approval + action result tracking.
        self.pending_approvals: Dict[str, Dict[str, object]] = {}
        # action_id -> ActionProposal (for later execution after approval)
        self.pending_proposals: Dict[str, ActionProposal] = {}
        # action_id -> ActionResult
        self.action_results: Dict[str, ActionResult] = {}
        # task_id -> set of (event-derived dedup keys) the reflector has
        # already filed for this task. Stops the same lesson from being
        # re-proposed after every action when the reflector runs more
        # than once during a task's lifetime.
        self._reflected_keys: Dict[str, set] = {}
        # Optional callback invoked at every task-mutation boundary so a
        # disk-backed history layer can persist a redacted snapshot.
        # See ``api.LocalSupervisorAPI._record_task_history``. None when
        # no history layer is wired (tests, headless usage).
        self._history_recorder: Optional[Callable[[TaskRecord], None]] = None

    def set_history_recorder(
        self, recorder: Optional[Callable[[TaskRecord], None]]
    ) -> None:
        """Register (or clear) the history recorder callback.

        The recorder is called *after* a task is mutated and the audit
        log entry is appended, so on-disk history always reflects state
        already committed to the signed log. Recorder errors must not
        propagate — they're captured by the recorder itself and exposed
        on history health metadata.
        """
        self._history_recorder = recorder

    def notify_task_changed(self, task: TaskRecord) -> None:
        """Invoke the history recorder for ``task``, swallowing failures.

        Call this from every task-mutation site (submit, propose,
        approve/deny, action result, workflow transition, memory
        lifecycle when reflected on the trace). Failure to persist must
        not block the supervisor's primary path; the recorder is
        responsible for surfacing the error via history health.
        """
        recorder = self._history_recorder
        if recorder is None:
            return
        try:
            recorder(task)
        except Exception:
            # Defence in depth — the recorder already wraps its own
            # body, but we never let a persistence mishap take down
            # an action that was already committed to the audit log.
            pass

    async def submit_task(self, objective: str, source: str = "text", context: Optional[Dict[str, object]] = None) -> TaskRecord:
        task = TaskRecord(objective=objective, source=source, status=TaskStatus.RUNNING, context=dict(context or {}))
        self.tasks[task.task_id] = task
        self.event_log.append("task.created", task.to_dict())

        blackboard = Blackboard(task.task_id)
        outputs = await asyncio.gather(*(agent.run(objective, task.task_id, blackboard) for agent in default_subagents()))

        task.plan = self._extract_plan(outputs)
        task.trace.extend(self._trace_from_outputs(outputs))
        task.context["blackboard"] = blackboard.snapshot()
        task.status = TaskStatus.BLOCKED if task.approvals else TaskStatus.RUNNING
        task.touch()
        self.event_log.append("task.analyzed", {"task_id": task.task_id, "trace": task.trace, "plan": task.plan})
        self.notify_task_changed(task)
        return task

    def request_action(self, proposal: ActionProposal, approved: bool = False) -> ActionResult:
        task = self.tasks[proposal.task_id]
        proposed = self.gateway.propose_action(proposal)
        approval = self.gateway.require_approval(proposed)
        if approval:
            task.approvals.append(approval)
            task.status = TaskStatus.BLOCKED
            task.trace.append({"event": "approval.requested", "approval": approval.to_dict()})
            task.touch()
            self.pending_approvals[approval.approval_id] = {
                "approval": approval,
                "action_id": proposal.action_id,
                "task_id": task.task_id,
            }
            self.pending_proposals[proposal.action_id] = proposal
        result = self.gateway.execute(proposed, approved=approved)
        self.action_results[proposal.action_id] = result
        if result.status == "executed":
            self.gateway.verify(result)
            task.trace.append({"event": "action.executed", "result": result.to_dict()})
            task.status = TaskStatus.RUNNING
            task.touch()
            self._curate_lessons(task, result)
            # Executed → approval consumed (if there was one).
            self._consume_approval_for_action(proposal.action_id)
        elif result.status == "blocked":
            task.trace.append({"event": "action.blocked", "result": result.to_dict()})
            task.touch()
            self._curate_lessons(task, result)
        else:
            # "failed" — surface the failure on the trace and let the
            # reflector file a tool-reliability candidate.
            task.trace.append({"event": "action.executed", "result": result.to_dict()})
            task.touch()
            self._curate_lessons(task, result)
        self.notify_task_changed(task)
        return result

    # ------------------------------------------------------------------
    # First-class proposal / approval / denial workflow.
    # ------------------------------------------------------------------

    def propose_action(self, proposal: ActionProposal) -> Dict[str, object]:
        """Evaluate policy and either queue for approval or execute immediately.

        Returns a dict describing the outcome: the decision, any approval
        request that was raised, and (if auto-executed) the ActionResult.
        """
        if proposal.task_id not in self.tasks:
            raise KeyError(f"Unknown task_id: {proposal.task_id}")
        task = self.tasks[proposal.task_id]
        proposed = self.gateway.propose_action(proposal)
        decision = proposed.decision

        approval = self.gateway.require_approval(proposed)
        if approval:
            task.approvals.append(approval)
            task.status = TaskStatus.BLOCKED
            task.trace.append({"event": "approval.requested", "approval": approval.to_dict()})
            task.touch()
            self.pending_approvals[approval.approval_id] = {
                "approval": approval,
                "action_id": proposal.action_id,
                "task_id": task.task_id,
            }
            self.pending_proposals[proposal.action_id] = proposal
            status = "blocked" if decision.blocked else "awaiting_approval"
            self.notify_task_changed(task)
            return {"status": status, "decision": decision.to_dict(),
                    "approval": approval.to_dict(), "action_id": proposal.action_id}

        # Tier 0 / high-confidence Tier 1 → execute now.
        result = self.gateway.execute(proposed, approved=False)
        self.action_results[proposal.action_id] = result
        if result.status == "executed":
            self.gateway.verify(result)
            task.trace.append({"event": "action.executed", "result": result.to_dict()})
            task.touch()
            self._curate_lessons(task, result)
        elif result.status == "blocked":
            task.trace.append({"event": "action.blocked", "result": result.to_dict()})
            task.touch()
            self._curate_lessons(task, result)
        else:
            task.trace.append({"event": "action.executed", "result": result.to_dict()})
            task.touch()
            self._curate_lessons(task, result)
        self.notify_task_changed(task)
        return {"status": result.status, "decision": decision.to_dict(),
                "result": result.to_dict(), "action_id": proposal.action_id}

    def approve_and_execute(self, approval_id: str) -> ActionResult:
        """Execute a previously-queued proposal after user approval."""
        entry = self.pending_approvals.get(approval_id)
        if entry is None:
            raise KeyError(f"Unknown approval_id: {approval_id}")
        action_id = entry["action_id"]
        proposal = self.pending_proposals.get(action_id)
        if proposal is None:
            raise KeyError(f"No proposal found for approval {approval_id}")

        task = self.tasks[entry["task_id"]]
        proposed = self.gateway.propose_action(proposal)
        result = self.gateway.execute(proposed, approved=True)
        self.action_results[action_id] = result

        if result.status == "executed":
            self.gateway.verify(result)
            task.trace.append({"event": "action.executed", "result": result.to_dict()})
            task.status = TaskStatus.RUNNING
            task.touch()
            self._curate_lessons(task, result)
        elif result.status == "blocked":
            task.trace.append({"event": "action.blocked", "result": result.to_dict()})
            task.touch()

        self._consume_approval(approval_id)
        self.notify_task_changed(task)
        return result

    def deny_approval(self, approval_id: str, reason: str = "") -> Dict[str, object]:
        entry = self.pending_approvals.get(approval_id)
        if entry is None:
            raise KeyError(f"Unknown approval_id: {approval_id}")
        approval = entry["approval"]
        task = self.tasks[entry["task_id"]]

        denial_payload = {
            "approval_id": approval_id,
            "action_id": entry["action_id"],
            "task_id": entry["task_id"],
            "capability": approval.capability,
            "reason": reason or "Denied by user.",
        }
        task.trace.append({"event": "approval.denied", "approval": denial_payload})
        # Status stays BLOCKED until user submits something else; conservative default.
        task.touch()
        self.event_log.append("approval.denied", denial_payload)
        self._consume_approval(approval_id)
        self.notify_task_changed(task)
        return denial_payload

    def list_pending_approvals(self) -> List[Dict[str, object]]:
        return [entry["approval"].to_dict() for entry in self.pending_approvals.values()]

    def get_action_result(self, action_id: str) -> Optional[ActionResult]:
        return self.action_results.get(action_id)

    def latest_action_result(self) -> Optional[ActionResult]:
        if not self.action_results:
            return None
        # Dicts preserve insertion order; return the most recently stored.
        return next(reversed(self.action_results.values()))

    def _consume_approval(self, approval_id: str) -> None:
        entry = self.pending_approvals.pop(approval_id, None)
        if entry:
            # Drop the linked proposal too; also remove the approval from the task record.
            self.pending_proposals.pop(entry["action_id"], None)
            task = self.tasks.get(entry["task_id"])
            if task:
                task.approvals = [a for a in task.approvals if a.approval_id != approval_id]
                if not task.approvals and task.status == TaskStatus.BLOCKED:
                    task.status = TaskStatus.RUNNING
                task.touch()

    def _consume_approval_for_action(self, action_id: str) -> None:
        matching = [aid for aid, entry in self.pending_approvals.items()
                    if entry["action_id"] == action_id]
        for approval_id in matching:
            self._consume_approval(approval_id)

    def cancel_task(self, task_id: str) -> TaskRecord:
        task = self.tasks[task_id]
        task.status = TaskStatus.CANCELLED
        task.touch()
        self.event_log.append("task.cancelled", task.to_dict())
        self.notify_task_changed(task)
        return task

    def inspect_task(self, task_id: str) -> Dict[str, object]:
        return self.tasks[task_id].to_dict()

    def resume_task(self, task_id: str) -> TaskRecord:
        task = self.tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.touch()
        self.event_log.append("task.resumed", task.to_dict())
        self.notify_task_changed(task)
        return task

    def fetch_trace(self, task_id: str) -> List[Dict[str, object]]:
        return list(self.tasks[task_id].trace)

    def fetch_memory_candidates(self) -> List[Dict[str, object]]:
        return self.memory.list(status="candidate")

    def _extract_plan(self, outputs: List[AgentOutput]) -> List[Dict[str, object]]:
        for output in outputs:
            if output.agent == "planner":
                steps = output.payload["steps"]
                return [{"id": index + 1, "step": step} for index, step in enumerate(steps)]
        return []

    def _trace_from_outputs(self, outputs: List[AgentOutput]) -> List[Dict[str, object]]:
        return [
            {
                "event": "subagent.completed",
                "agent": output.agent,
                "status": output.status,
                "summary": output.summary,
            }
            for output in outputs
        ]

    def _curate_lessons(self, task: TaskRecord, result: ActionResult) -> None:
        """Run the end-of-step reflection pass.

        We invoke the Reflector after every executed action and after
        any blocking event so a long task can accumulate proposals as
        it goes. The Reflector itself is idempotent across calls (it
        returns the *current* set of safe lessons given the current
        trace); we de-dup by (kind, dedup_key) on this side so the
        same lesson is filed at most once per task.
        """
        proposed = self.reflector.reflect_on_task(task)
        if not proposed:
            return
        seen = self._reflected_keys.setdefault(task.task_id, set())
        fresh: List[Dict[str, object]] = []
        for memory in proposed:
            key = (memory.get("kind"), memory.get("memory_id"))
            if key in seen:
                continue
            seen.add(key)
            fresh.append(memory)
        for memory in fresh:
            task.trace.append({"event": "lesson.proposed", "memory": memory})
            self.event_log.append("lesson.proposed", memory)

    # ------------------------------------------------------------------
    # Memory lifecycle wrappers — the bridge calls these so every
    # status change lands on the signed audit log alongside the action
    # decisions.
    # ------------------------------------------------------------------

    def approve_memory(self, memory_id: str) -> Dict[str, object]:
        row = self.memory.approve(memory_id)
        self.event_log.append("memory.approved", row)
        return row

    def reject_memory(self, memory_id: str, reason: str = "") -> Dict[str, object]:
        row = self.memory.reject(memory_id, reason=reason)
        self.event_log.append("memory.rejected", row)
        return row

    def expire_memory(self, memory_id: str, reason: str = "") -> Dict[str, object]:
        row = self.memory.expire(memory_id, reason=reason)
        self.event_log.append("memory.expired", row)
        return row

