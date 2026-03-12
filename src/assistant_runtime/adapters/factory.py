from __future__ import annotations

from pathlib import Path

from assistant_runtime.adapters.llm_adapter import LLMAdapter
from assistant_runtime.adapters.llm_adapter import MockLLMAdapter
from assistant_runtime.adapters.llm_adapter import OpenAICompatibleLLMAdapter
from assistant_runtime.adapters.stt_adapter import HttpSTTAdapter
from assistant_runtime.adapters.stt_adapter import MockSTTAdapter
from assistant_runtime.adapters.stt_adapter import STTAdapter
from assistant_runtime.adapters.stt_adapter import TextPassthroughSTTAdapter
from assistant_runtime.adapters.tts_adapter import default_powershell_executable
from assistant_runtime.adapters.tts_adapter import HttpTTSAdapter
from assistant_runtime.adapters.tts_adapter import MockTTSAdapter
from assistant_runtime.adapters.tts_adapter import PowerShellSpeechTTSAdapter
from assistant_runtime.adapters.tts_adapter import TTSAdapter
from assistant_runtime.config.loader import load_llm_endpoint
from assistant_runtime.config.loader import load_model_routing_settings
from assistant_runtime.config.loader import load_runtime_settings
from assistant_runtime.config.loader import load_stt_endpoint
from assistant_runtime.config.loader import load_tts_endpoint
from assistant_runtime.core.model_router import choose_stage_route


def build_stt_adapter(
    config_dir: Path,
    active_conditions: set[str] | None = None,
    prefer_online: bool = False,
) -> STTAdapter:
    runtime_settings = load_runtime_settings(config_dir)
    route_settings = load_model_routing_settings(config_dir)
    endpoint = load_stt_endpoint(config_dir)

    if runtime_settings.stt_provider == "mock":
        return MockSTTAdapter()
    if runtime_settings.stt_provider == "text_passthrough":
        return TextPassthroughSTTAdapter()

    decision = choose_stage_route(
        route_settings,
        stage="stt",
        active_conditions=active_conditions,
        prefer_online=prefer_online,
    )
    if decision.selected_mode == "online":
        return HttpSTTAdapter(
            endpoint=endpoint.url,
            auth_env_var=endpoint.auth_env_var,
            language=endpoint.language,
            timeout_seconds=max(1, endpoint.timeout_ms // 1000),
        )

    return TextPassthroughSTTAdapter()


def build_llm_adapter(config_dir: Path) -> LLMAdapter:
    endpoint = load_llm_endpoint(config_dir)

    if endpoint.url == "mock":
        return MockLLMAdapter()

    if endpoint.provider not in {"github_models", "openai_compatible"}:
        raise LookupError(f"Unsupported LLM provider: {endpoint.provider}")

    if endpoint.api_format != "openai_chat_completions":
        raise LookupError(f"Unsupported LLM api format: {endpoint.api_format}")

    return OpenAICompatibleLLMAdapter(
        endpoint=endpoint.url,
        auth_env_var=endpoint.auth_env_var,
        provider=endpoint.provider,
        timeout_seconds=max(1, endpoint.timeout_ms // 1000),
    )


def build_tts_adapter(
    config_dir: Path,
    active_conditions: set[str] | None = None,
    prefer_online: bool = False,
) -> TTSAdapter:
    runtime_settings = load_runtime_settings(config_dir)
    route_settings = load_model_routing_settings(config_dir)
    endpoint = load_tts_endpoint(config_dir)
    provider = getattr(runtime_settings, "tts_provider", None) or "powershell"
    decision = choose_stage_route(
        route_settings,
        stage="tts",
        active_conditions=active_conditions,
        prefer_online=prefer_online,
    )

    if provider == "mock":
        return MockTTSAdapter()
    if provider == "disabled":
        return MockTTSAdapter()
    if decision.selected_mode == "online" and endpoint.url != "mock":
        return HttpTTSAdapter(
            endpoint=endpoint.url,
            auth_env_var=endpoint.auth_env_var,
            provider=endpoint.provider,
            timeout_seconds=max(1, endpoint.timeout_ms // 1000),
            api_format=endpoint.api_format,
            voice=endpoint.voice,
        )
    if provider == "http":
        if endpoint.url == "mock":
            return MockTTSAdapter()
        return HttpTTSAdapter(
            endpoint=endpoint.url,
            auth_env_var=endpoint.auth_env_var,
            provider=endpoint.provider,
            timeout_seconds=max(1, endpoint.timeout_ms // 1000),
            api_format=endpoint.api_format,
            voice=endpoint.voice,
        )

    return PowerShellSpeechTTSAdapter(executable=default_powershell_executable())
