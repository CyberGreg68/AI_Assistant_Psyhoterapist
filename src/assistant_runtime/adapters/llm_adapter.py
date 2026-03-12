from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error
from urllib import request


@dataclass(slots=True)
class GenerationRequest:
    conversation_id: str
    lang: str
    prompt: str
    system_prompt: str
    model: str
    max_tokens: int


@dataclass(slots=True)
class GenerationResponse:
    text: str
    model: str
    finish_reason: str | None = None
    raw_payload: dict[str, Any] | None = None


@dataclass(slots=True)
class LLMServiceError(Exception):
    message: str
    error_type: str
    status_code: int | None = None
    retryable: bool = False
    response_body: str | None = None

    def __str__(self) -> str:
        return self.message


class LLMAdapter:
    def generate(self, generation_request: GenerationRequest) -> GenerationResponse:
        raise NotImplementedError


@dataclass(slots=True)
class MockLLMAdapter(LLMAdapter):
    prefix: str = "[mock-llm]"

    def generate(self, generation_request: GenerationRequest) -> GenerationResponse:
        return GenerationResponse(
            text=f"{self.prefix} {generation_request.prompt[:160]}".strip(),
            model=generation_request.model,
            finish_reason="stop",
            raw_payload={"source": "mock"},
        )


@dataclass(slots=True)
class OpenAICompatibleLLMAdapter(LLMAdapter):
    endpoint: str
    auth_env_var: str | None
    provider: str = "openai_compatible"
    timeout_seconds: int = 30

    @staticmethod
    def normalize_model_name(model: str) -> str:
        normalized_model = model.strip()
        if normalized_model.startswith("github-copilot/"):
            return normalized_model.split("/", 1)[1]
        return normalized_model

    @staticmethod
    def _base_model_name(model: str) -> str:
        normalized_model = OpenAICompatibleLLMAdapter.normalize_model_name(model)
        if "/" in normalized_model:
            return normalized_model.split("/", 1)[1]
        return normalized_model

    @staticmethod
    def _uses_max_completion_tokens(model: str) -> bool:
        normalized_model = OpenAICompatibleLLMAdapter._base_model_name(model).lower()
        return normalized_model.startswith("gpt-5")

    def build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.provider == "github_models":
            headers["Accept"] = "application/vnd.github+json"
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        if self.auth_env_var:
            token = os.getenv(self.auth_env_var)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return headers

    def is_ready(self) -> dict[str, object]:
        token_present = bool(self.auth_env_var and os.getenv(self.auth_env_var))
        if self.auth_env_var and not token_present:
            return {
                "status": "missing_auth",
                "provider": self.provider,
                "auth_configured": False,
                "endpoint": self.endpoint,
            }
        return {
            "status": "configured",
            "provider": self.provider,
            "auth_configured": token_present or self.auth_env_var is None,
            "endpoint": self.endpoint,
        }

    def _raise_service_error(
        self,
        message: str,
        error_type: str,
        status_code: int | None = None,
        retryable: bool = False,
        response_body: str | None = None,
    ) -> None:
        raise LLMServiceError(
            message=message,
            error_type=error_type,
            status_code=status_code,
            retryable=retryable,
            response_body=response_body,
        )

    def build_payload(self, generation_request: GenerationRequest) -> dict[str, Any]:
        request_model = self.normalize_model_name(generation_request.model)
        uses_gpt5_compat = self._uses_max_completion_tokens(request_model)
        payload = {
            "model": request_model,
            "messages": [
                {"role": "system", "content": generation_request.system_prompt},
                {"role": "user", "content": generation_request.prompt},
            ],
        }
        if not uses_gpt5_compat:
            payload["temperature"] = 0.3

        token_key = "max_completion_tokens" if uses_gpt5_compat else "max_tokens"
        payload[token_key] = generation_request.max_tokens
        return payload

    def generate(self, generation_request: GenerationRequest) -> GenerationResponse:
        payload = json.dumps(self.build_payload(generation_request)).encode("utf-8")
        http_request = request.Request(
            self.endpoint,
            data=payload,
            headers=self.build_headers(),
            method="POST",
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                raw_payload = json.loads(response.read().decode("utf-8") or "{}")
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
            if exc.code in {401, 403}:
                self._raise_service_error(
                    message="LLM authentication failed.",
                    error_type="auth_error",
                    status_code=exc.code,
                    retryable=False,
                    response_body=response_body,
                )
            if exc.code == 429:
                self._raise_service_error(
                    message="LLM request was rate limited.",
                    error_type="rate_limited",
                    status_code=exc.code,
                    retryable=True,
                    response_body=response_body,
                )
            if 500 <= exc.code <= 599:
                self._raise_service_error(
                    message="LLM provider returned a server error.",
                    error_type="server_error",
                    status_code=exc.code,
                    retryable=True,
                    response_body=response_body,
                )
            self._raise_service_error(
                message="LLM request failed.",
                error_type="http_error",
                status_code=exc.code,
                retryable=False,
                response_body=response_body,
            )
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            self._raise_service_error(
                message=f"LLM network error: {reason}",
                error_type="network_error",
                retryable=True,
            )
        except TimeoutError:
            self._raise_service_error(
                message="LLM request timed out.",
                error_type="timeout",
                retryable=True,
            )
        except json.JSONDecodeError as exc:
            self._raise_service_error(
                message=f"LLM response was not valid JSON: {exc}",
                error_type="invalid_response",
                retryable=False,
            )

        choices = raw_payload.get("choices", [])
        if not choices:
            self._raise_service_error(
                message="LLM response did not contain any choices.",
                error_type="invalid_response",
                retryable=False,
                response_body=json.dumps(raw_payload, ensure_ascii=True),
            )

        message = choices[0].get("message", {})
        return GenerationResponse(
            text=str(message.get("content", "")).strip(),
            model=str(raw_payload.get("model") or generation_request.model),
            finish_reason=choices[0].get("finish_reason"),
            raw_payload=raw_payload,
        )