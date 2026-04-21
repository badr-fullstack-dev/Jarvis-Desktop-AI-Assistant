from __future__ import annotations

from typing import Any, Dict

from ..models import ActionProposal, ActionResult
from .base import CapabilityAdapter


class BrowserCapability(CapabilityAdapter):
    name = "browser"

    def supports(self, capability: str) -> bool:
        return capability.startswith("browser.")

    def execute(self, proposal: ActionProposal) -> ActionResult:
        target = proposal.parameters.get("url", "about:blank")
        summary = f"Prepared browser action '{proposal.capability}' for {target}."
        output = {"target": target, "mode": "stub", "dry_run": proposal.dry_run}
        return ActionResult(proposal=proposal, status="executed", summary=summary, output=output)

    def verify(self, proposal: ActionProposal, result: ActionResult) -> Dict[str, Any]:
        return {"ok": True, "checked": ["capability.route", "target.present"], "mode": "stub"}

