from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Protocol
from urllib import request


@dataclass(slots=True)
class Transcript:
    text: str
    source: str
    confidence: float | None = None


class STTAdapter(Protocol):
    def transcribe(self, audio_path: Path) -> Transcript:
        raise NotImplementedError


class MockSTTAdapter:
    def transcribe(self, audio_path: Path) -> Transcript:
        return Transcript(text=f"mock transcript from {audio_path.name}", source="mock")


@dataclass(slots=True)
class HttpSTTAdapter:
    endpoint: str
    auth_env_var: str | None = None
    language: str = "hu"
    timeout_seconds: int = 30

    def build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/octet-stream", "X-Language": self.language}
        if self.auth_env_var:
            token = os.getenv(self.auth_env_var)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def transcribe(self, audio_path: Path) -> Transcript:
        audio_bytes = audio_path.read_bytes()
        http_request = request.Request(
            self.endpoint,
            data=audio_bytes,
            headers=self.build_headers(),
            method="POST",
        )
        with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
            return Transcript(
                text=payload["text"],
                source=payload.get("source", "http_stt"),
                confidence=payload.get("confidence"),
            )


class TextPassthroughSTTAdapter:
    def transcribe(self, audio_path: Path) -> Transcript:
        return Transcript(text=audio_path.read_text(encoding="utf-8"), source="text_passthrough")

