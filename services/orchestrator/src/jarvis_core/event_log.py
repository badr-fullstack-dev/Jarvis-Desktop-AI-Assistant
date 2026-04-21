from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .models import new_id, utc_now


class SignedEventLog:
    """Append-only event log chained by HMAC signatures."""

    def __init__(self, log_path: Path, secret: str):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.secret = secret.encode("utf-8")

    def append(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        previous_signature = self.tail_signature()
        event = {
            "event_id": new_id("event"),
            "event_type": event_type,
            "timestamp": utc_now(),
            "payload": payload,
            "previous_signature": previous_signature,
        }
        event["signature"] = self._sign(event)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")
        return event

    def tail_signature(self) -> str:
        if not self.log_path.exists():
            return "GENESIS"
        lines = [line for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return "GENESIS"
        return json.loads(lines[-1])["signature"]

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def verify_chain(self) -> bool:
        previous = "GENESIS"
        for event in self.read_all():
            if event["previous_signature"] != previous:
                return False
            expected = self._sign(
                {
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "timestamp": event["timestamp"],
                    "payload": event["payload"],
                    "previous_signature": event["previous_signature"],
                }
            )
            if event["signature"] != expected:
                return False
            previous = event["signature"]
        return True

    def _sign(self, event: Dict[str, Any]) -> str:
        body = json.dumps(event, sort_keys=True).encode("utf-8")
        digest = hmac.new(self.secret, body, hashlib.sha256)
        return digest.hexdigest()

