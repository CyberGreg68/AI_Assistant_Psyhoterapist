from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(slots=True)
class AuditLogger:
    base_dir: Path
    secret: str | None = None

    def __post_init__(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if self.secret is None:
            self.secret = os.getenv("AUDIT_LOG_SECRET")

    def append_event(
        self,
        *,
        stream: str,
        event_type: str,
        actor: dict[str, Any],
        subject: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        file_path = self.base_dir / f"{stream}.jsonl"
        prev_hash = self._last_chain_hash(file_path)
        event = {
            "event_id": str(uuid4()),
            "recorded_at": datetime.now(UTC).isoformat(),
            "stream": stream,
            "event_type": event_type,
            "actor": actor,
            "subject": subject,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        chain_hash = hashlib.sha256(_canonical_json(event).encode("utf-8")).hexdigest()
        event["chain_hash"] = chain_hash
        if self.secret:
            event["signature"] = hmac.new(
                self.secret.encode("utf-8"),
                chain_hash.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
        with file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event

    def _last_chain_hash(self, file_path: Path) -> str | None:
        if not file_path.exists():
            return None
        lines = file_path.read_text(encoding="utf-8").splitlines()
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            chain_hash = payload.get("chain_hash")
            if chain_hash:
                return str(chain_hash)
        return None