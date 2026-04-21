from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict, List

from .blackboard import Blackboard
from .models import ActionProposal


@dataclass(slots=True)
class AgentOutput:
    agent: str
    status: str
    summary: str
    payload: Dict[str, object]


class BaseSubagent:
    name: str

    async def run(self, objective: str, task_id: str, blackboard: Blackboard) -> AgentOutput:
        raise NotImplementedError


class PlannerSubagent(BaseSubagent):
    name = "planner"

    async def run(self, objective: str, task_id: str, blackboard: Blackboard) -> AgentOutput:
        plan = [
            "Clarify user intent and affected targets.",
            "Gather evidence and prerequisites.",
            "Score risk and request approval if needed.",
            "Execute the smallest safe action set.",
            "Verify outcomes and propose lessons.",
        ]
        blackboard.publish(self.name, "plan", {"steps": plan})
        return AgentOutput(agent=self.name, status="ok", summary="Generated guarded execution plan.", payload={"steps": plan})


class ResearcherSubagent(BaseSubagent):
    name = "researcher"

    async def run(self, objective: str, task_id: str, blackboard: Blackboard) -> AgentOutput:
        evidence = [
            f"Objective received: {objective}",
            "No external browsing performed in scaffold mode.",
            "Capability use must remain inside configured scopes.",
        ]
        blackboard.publish(self.name, "evidence", {"items": evidence})
        return AgentOutput(agent=self.name, status="ok", summary="Collected local evidence snapshot.", payload={"items": evidence})


class SecuritySentinelSubagent(BaseSubagent):
    name = "security_sentinel"

    async def run(self, objective: str, task_id: str, blackboard: Blackboard) -> AgentOutput:
        warnings = [
            "Treat install, credential, and destructive actions as Tier 2 or Tier 3.",
            "Require explicit approval if confidence is low or the target is ambiguous.",
        ]
        blackboard.publish(self.name, "security", {"warnings": warnings})
        return AgentOutput(agent=self.name, status="ok", summary="Generated security guidance.", payload={"warnings": warnings})


class VerifierSubagent(BaseSubagent):
    name = "verifier"

    async def run(self, objective: str, task_id: str, blackboard: Blackboard) -> AgentOutput:
        checks = [
            "Validate prerequisites before execution.",
            "Confirm output state after execution.",
            "Emit recovery notes on partial failure.",
        ]
        blackboard.publish(self.name, "verification", {"checks": checks})
        return AgentOutput(agent=self.name, status="ok", summary="Prepared preflight and postflight checks.", payload={"checks": checks})


def default_subagents() -> List[BaseSubagent]:
    return [PlannerSubagent(), ResearcherSubagent(), SecuritySentinelSubagent(), VerifierSubagent()]

