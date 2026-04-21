from __future__ import annotations

from pathlib import Path
from typing import Dict

from ..models import ActionProposal, ActionResult
from .base import CapabilityAdapter


class FilesystemCapability(CapabilityAdapter):
    name = "filesystem"

    def supports(self, capability: str) -> bool:
        return capability.startswith("filesystem.")

    def execute(self, proposal: ActionProposal) -> ActionResult:
        path = Path(proposal.parameters.get("path", "."))
        summary = f"Prepared filesystem action '{proposal.capability}' for {path}."
        output = {"path": str(path), "exists": path.exists(), "dry_run": proposal.dry_run}
        return ActionResult(proposal=proposal, status="executed", summary=summary, output=output)

    def verify(self, proposal: ActionProposal, result: ActionResult) -> Dict[str, Any]:
        return {"ok": True, "checked": ["path.exists"], "mode": "stub"}

