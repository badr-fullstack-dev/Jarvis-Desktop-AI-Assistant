from __future__ import annotations

from typing import Dict

from ..models import ActionProposal, ActionResult
from .base import CapabilityAdapter


class ApplicationCapability(CapabilityAdapter):
    name = "applications"

    def supports(self, capability: str) -> bool:
        return capability.startswith("app.")

    def execute(self, proposal: ActionProposal) -> ActionResult:
        app_name = proposal.parameters.get("name", "unknown")
        summary = f"Prepared application action '{proposal.capability}' for {app_name}."
        output = {"application": app_name, "mode": "stub", "dry_run": proposal.dry_run}
        return ActionResult(proposal=proposal, status="executed", summary=summary, output=output)

    def verify(self, proposal: ActionProposal, result: ActionResult) -> Dict[str, Any]:
        return {"ok": True, "checked": ["application.target"], "mode": "stub"}

