from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .models import ActionProposal, RiskDecision


@dataclass(slots=True)
class PolicyEntry:
    capability: str
    tier: int
    scopes: list[str]


class PolicyEngine:
    """Loads policy configuration and produces risk decisions."""

    def __init__(self, config_path: Path):
        self.config_path = Path(config_path)
        self._config = self._load()

    def _load(self) -> Dict[str, Any]:
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    @property
    def blocked_patterns(self) -> Iterable[str]:
        return self._config.get("blocked_patterns", [])

    def entry_for(self, capability: str) -> PolicyEntry:
        config = self._config["capabilities"].get(capability)
        if not config:
          raise KeyError(f"Unknown capability: {capability}")
        return PolicyEntry(capability=capability, tier=int(config["tier"]), scopes=list(config["scopes"]))

    def evaluate(self, proposal: ActionProposal) -> RiskDecision:
        entry = self.entry_for(proposal.capability)
        tier_name = f"tier_{entry.tier}"
        tier_config = self._config["risk_tiers"][tier_name]

        blocked = bool(tier_config.get("blocked", False))
        requires_approval = bool(tier_config.get("requires_approval") is True)

        if tier_config.get("requires_approval") == "conditional":
            requires_approval = proposal.confidence < 0.85 or bool(proposal.dry_run)

        text_fragments = [proposal.intent, json.dumps(proposal.parameters, sort_keys=True)]
        joined = " ".join(text_fragments).lower()
        if any(pattern.lower() in joined for pattern in self.blocked_patterns):
            blocked = True

        reason = self._reason(entry.tier, requires_approval, blocked)
        return RiskDecision(
            capability=entry.capability,
            risk_tier=entry.tier,
            requires_approval=requires_approval,
            blocked=blocked,
            reason=reason,
            scopes=entry.scopes,
        )

    @staticmethod
    def _reason(tier: int, requires_approval: bool, blocked: bool) -> str:
        if blocked:
            return f"Tier {tier} capability is blocked by default guarded-autonomy policy."
        if requires_approval:
            return f"Tier {tier} capability requires explicit user approval."
        return f"Tier {tier} capability is allowed to run automatically."

