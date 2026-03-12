from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class CacheEntry:
    value: Any
    expires_at: float


class VariantsCache:
    def __init__(self, ttl_seconds: int = 900, max_entries: int = 256) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._entries: dict[str, CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        if entry.expires_at <= time.time():
            self._entries.pop(key, None)
            return None
        return entry.value

    def set(self, key: str, value: Any) -> None:
        if len(self._entries) >= self.max_entries:
            oldest_key = min(self._entries, key=lambda item: self._entries[item].expires_at)
            self._entries.pop(oldest_key, None)
        self._entries[key] = CacheEntry(value=value, expires_at=time.time() + self.ttl_seconds)
