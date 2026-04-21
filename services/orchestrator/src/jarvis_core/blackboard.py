from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass(slots=True)
class Blackboard:
    task_id: str
    notes: List[Dict[str, Any]] = field(default_factory=list)

    def publish(self, lane: str, kind: str, payload: Dict[str, Any]) -> None:
        self.notes.append({"lane": lane, "kind": kind, "payload": payload})

    def snapshot(self) -> List[Dict[str, Any]]:
        return list(self.notes)

