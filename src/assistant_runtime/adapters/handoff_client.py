from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import request


@dataclass(slots=True)
class HandoffRequest:
    conversation_id: str
    lang: str
    transcript: str
    gist: str
    risk_flags: list[str]
    selected_category: str | None = None


@dataclass(slots=True)
class HandoffResponse:
    status_code: int
    body: dict[str, Any]


@dataclass(slots=True)
class CrisisHandoffClient:
    url: str
    timeout_ms: int
    auth_env_var: str

    def build_payload(self, handoff_request: HandoffRequest) -> dict[str, Any]:
        return {
            "conversation_id": handoff_request.conversation_id,
            "lang": handoff_request.lang,
            "transcript": handoff_request.transcript,
            "gist": handoff_request.gist,
            "risk_flags": handoff_request.risk_flags,
            "selected_category": handoff_request.selected_category,
        }

    def send(self, handoff_request: HandoffRequest) -> HandoffResponse:
        payload = json.dumps(self.build_payload(handoff_request)).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        token = os.getenv(self.auth_env_var)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        http_request = request.Request(self.url, data=payload, headers=headers, method="POST")
        with request.urlopen(http_request, timeout=self.timeout_ms / 1000) as response:
            body = response.read().decode("utf-8")
            return HandoffResponse(status_code=response.status, body=json.loads(body or "{}"))
