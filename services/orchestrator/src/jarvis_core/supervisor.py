from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Dict, List, Optional

from .blackboard import Blackboard
from .event_log import SignedEventLog
from .gateway import ActionGateway
from .memory import MemoryStore
from .models import ActionProposal, ActionResult, TaskRecord, TaskStatus
from .subagents import AgentOutput, default_subagents


class SupervisorRuntime:
    """Owns task state, subagent coordination, and capability execution."""

    def __init__(self, gateway: ActionGateway, memory: MemoryStore, event_log: SignedEventLog) -> None:
        self.gateway = gateway
        self.memory = memory
        self.event_log = event_log
        self.tasks: Dict[str, TaskRecord] = {}

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
        result = self.gateway.execute(proposed, approved=approved)
        if result.status == "executed":
            self.gateway.verify(result)
            task.trace.append({"event": "action.executed", "result": result.to_dict()})
            task.status = TaskStatus.RUNNING
            task.touch()
            self._curate_lessons(task, result)
        return result

    def cancel_task(self, task_id: str) -> TaskRecord:
        task = self.tasks[task_id]
        task.status = TaskStatus.CANCELLED
        task.touch()
        self.event_log.append("task.cancelled", task.to_dict())
        return task

    def inspect_task(self, task_id: str) -> Dict[str, object]:
        return self.tasks[task_id].to_dict()

    def resume_task(self, task_id: str) -> TaskRecord:
        task = self.tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.touch()
        self.event_log.append("task.resumed", task.to_dict())
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
        lesson = self.memory.propose_lesson(
            summary=f"After {result.proposal.capability}, always verify the target state before reporting success.",
            evidence=[result.summary, result.verification.get("mode", "unknown")],
            trust_score=0.7,
            details={"task_id": task.task_id, "action_id": result.proposal.action_id},
        )
        task.trace.append({"event": "lesson.proposed", "memory": lesson.to_dict()})
        self.event_log.append("lesson.proposed", lesson.to_dict())

