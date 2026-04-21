from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List

from .models import MemoryItem


class MemoryStore:
    """Structured memory layers with simple persistence."""

    LAYERS = ("profile", "operational", "lesson", "tool")

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for layer in self.LAYERS:
            path = self._path(layer)
            if not path.exists():
                path.write_text("[]", encoding="utf-8")

    def add(self, item: MemoryItem) -> MemoryItem:
        items = self._read(item.kind)
        items.append(item.to_dict())
        self._write(item.kind, items)
        return item

    def list(self, kind: str | None = None, status: str | None = None) -> List[Dict[str, object]]:
        kinds = [kind] if kind else list(self.LAYERS)
        rows: List[Dict[str, object]] = []
        for layer in kinds:
            rows.extend(self._read(layer))
        if status:
            rows = [row for row in rows if row.get("status") == status]
        return rows

    def propose_lesson(self, summary: str, evidence: Iterable[str], trust_score: float, details: Dict[str, object] | None = None) -> MemoryItem:
        lesson = MemoryItem(
            kind="lesson",
            summary=summary,
            details=details or {},
            evidence=list(evidence),
            trust_score=trust_score,
        )
        return self.add(lesson)

    def _path(self, kind: str) -> Path:
        return self.root / f"{kind}.json"

    def _read(self, kind: str) -> List[Dict[str, object]]:
        return json.loads(self._path(kind).read_text(encoding="utf-8"))

    def _write(self, kind: str, items: List[Dict[str, object]]) -> None:
        self._path(kind).write_text(json.dumps(items, indent=2), encoding="utf-8")

