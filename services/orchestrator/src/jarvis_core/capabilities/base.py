from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

from ..models import ActionProposal, ActionResult


class CapabilityAdapter(ABC):
    name: str

    @abstractmethod
    def supports(self, capability: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def execute(self, proposal: ActionProposal) -> ActionResult:
        raise NotImplementedError

    @abstractmethod
    def verify(self, proposal: ActionProposal, result: ActionResult) -> Dict[str, Any]:
        raise NotImplementedError

