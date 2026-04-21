from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from .event_log import SignedEventLog
from .gateway import ActionGateway
from .memory import MemoryStore
from .models import ActionProposal
from .policy import PolicyEngine
from .supervisor import SupervisorRuntime


class LocalSupervisorAPI:
    """Convenience API for local tools, tests, and future IPC bridges."""

    def __init__(self, root: Path) -> None:
        root = Path(root)
        config_path = root / "configs" / "policy.default.json"
        runtime_path = root / "runtime"

        self.event_log = SignedEventLog(runtime_path / "events.jsonl", secret="jarvis-local-dev-secret")
        self.memory = MemoryStore(runtime_path / "memory")
        self.policy = PolicyEngine(config_path)
        self.gateway = ActionGateway(self.policy, self.event_log)
        self.supervisor = SupervisorRuntime(self.gateway, self.memory, self.event_log)

    async def submit_voice_or_text_task(self, objective: str, source: str = "text", context: Optional[Dict[str, object]] = None):
        return await self.supervisor.submit_task(objective=objective, source=source, context=context)

    def submit_action(self, proposal: ActionProposal, approved: bool = False):
        return self.supervisor.request_action(proposal, approved=approved)

