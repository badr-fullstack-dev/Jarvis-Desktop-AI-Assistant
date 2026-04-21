from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class ActionProposal:
    task_id: str
    capability: str
    intent: str
    parameters: Dict[str, Any]
    requested_by: str
    evidence: List[str]
    confidence: float = 0.5
    dry_run: bool = False
    action_id: str = field(default_factory=lambda: new_id("action"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RiskDecision:
    capability: str
    risk_tier: int
    requires_approval: bool
    blocked: bool
    reason: str
    scopes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ActionResult:
    proposal: ActionProposal
    status: str
    summary: str
    output: Dict[str, Any] = field(default_factory=dict)
    verification: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["proposal"] = self.proposal.to_dict()
        return payload


@dataclass(slots=True)
class ApprovalRequest:
    approval_id: str
    task_id: str
    action_id: str
    capability: str
    risk_tier: int
    reason: str
    title: str
    preview: Dict[str, Any]
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryItem:
    kind: str
    summary: str
    details: Dict[str, Any]
    evidence: List[str]
    trust_score: float
    status: str = "candidate"
    review_at: Optional[str] = None
    expires_at: Optional[str] = None
    memory_id: str = field(default_factory=lambda: new_id("memory"))
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TaskRecord:
    objective: str
    source: str
    status: TaskStatus = TaskStatus.PENDING
    context: Dict[str, Any] = field(default_factory=dict)
    task_id: str = field(default_factory=lambda: new_id("task"))
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    plan: List[Dict[str, Any]] = field(default_factory=list)
    approvals: List[ApprovalRequest] = field(default_factory=list)
    trace: List[Dict[str, Any]] = field(default_factory=list)

    def touch(self) -> None:
        self.updated_at = utc_now()

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["approvals"] = [approval.to_dict() for approval in self.approvals]
        return payload

