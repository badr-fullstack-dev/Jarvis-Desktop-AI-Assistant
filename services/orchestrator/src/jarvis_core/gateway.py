from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

from .capabilities import ApplicationCapability, BrowserCapability, CapabilityAdapter, FilesystemCapability
from .event_log import SignedEventLog
from .models import ActionProposal, ActionResult, ApprovalRequest, RiskDecision, new_id
from .policy import PolicyEngine


@dataclass(slots=True)
class ProposedAction:
    proposal: ActionProposal
    decision: RiskDecision


class ActionGateway:
    """Central policy gate for all tool execution."""

    def __init__(
        self,
        policy_engine: PolicyEngine,
        event_log: SignedEventLog,
        adapters: Iterable[CapabilityAdapter] | None = None,
    ) -> None:
        self.policy_engine = policy_engine
        self.event_log = event_log
        self.adapters = list(adapters or [BrowserCapability(), FilesystemCapability(), ApplicationCapability()])

    def propose_action(self, proposal: ActionProposal) -> ProposedAction:
        decision = self.policy_engine.evaluate(proposal)
        self.event_log.append("action.proposed", {"proposal": proposal.to_dict(), "decision": decision.to_dict()})
        return ProposedAction(proposal=proposal, decision=decision)

    def risk_score(self, proposal: ActionProposal) -> RiskDecision:
        return self.policy_engine.evaluate(proposal)

    def require_approval(self, proposed: ProposedAction) -> ApprovalRequest | None:
        if proposed.decision.blocked or proposed.decision.requires_approval:
            title = f"{proposed.proposal.capability} request"
            approval = ApprovalRequest(
                approval_id=new_id("approval"),
                task_id=proposed.proposal.task_id,
                action_id=proposed.proposal.action_id,
                capability=proposed.proposal.capability,
                risk_tier=proposed.decision.risk_tier,
                reason=proposed.decision.reason,
                title=title,
                preview=proposed.proposal.parameters,
            )
            self.event_log.append("approval.requested", approval.to_dict())
            return approval
        return None

    def execute(self, proposed: ProposedAction, approved: bool = False) -> ActionResult:
        if proposed.decision.blocked and not approved:
            result = ActionResult(
                proposal=proposed.proposal,
                status="blocked",
                summary=proposed.decision.reason,
                output={"reason": "blocked_by_policy"},
            )
            self.event_log.append("action.blocked", result.to_dict())
            return result
        if proposed.decision.requires_approval and not approved:
            result = ActionResult(
                proposal=proposed.proposal,
                status="awaiting_approval",
                summary=proposed.decision.reason,
                output={"reason": "approval_required"},
            )
            self.event_log.append("action.paused", result.to_dict())
            return result

        adapter = self._adapter_for(proposed.proposal.capability)
        result = adapter.execute(proposed.proposal)
        result.verification = adapter.verify(proposed.proposal, result)
        self.event_log.append("action.executed", result.to_dict())
        return result

    def verify(self, result: ActionResult) -> Dict[str, object]:
        self.event_log.append("action.verified", {"action_id": result.proposal.action_id, "verification": result.verification})
        return result.verification

    def rollback_hint(self, proposal: ActionProposal) -> str:
        if proposal.capability.startswith("filesystem."):
            return "Keep backups and store the original path before any write or move action."
        if proposal.capability.startswith("app.install"):
            return "Prepare an uninstall path and restore point before installation."
        return "Record enough state to let a human reverse the action safely."

    def _adapter_for(self, capability: str) -> CapabilityAdapter:
        for adapter in self.adapters:
            if adapter.supports(capability):
                return adapter
        raise KeyError(f"No adapter registered for {capability}")

