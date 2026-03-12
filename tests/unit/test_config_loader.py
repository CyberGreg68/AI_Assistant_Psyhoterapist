from pathlib import Path

from assistant_runtime.config.loader import load_latency_masking_settings
from assistant_runtime.config.loader import load_llm_endpoint
from assistant_runtime.config.loader import load_model_routing_settings
from assistant_runtime.config.loader import load_role_channel_matrix
from assistant_runtime.config.loader import load_tts_endpoint


def test_load_role_channel_matrix() -> None:
    config_dir = Path.cwd() / "config"
    settings = load_role_channel_matrix(config_dir)

    assert "web_chat" in settings.patient.ingress
    assert "admin_console" in settings.operator.primary
    assert "clinical_console" in settings.clinical_lead.primary


def test_load_model_routing_settings() -> None:
    config_dir = Path.cwd() / "config"
    settings = load_model_routing_settings(config_dir)

    assert settings.default_mode == "hybrid_local_first"
    assert any(stage.stage == "generative_fallback" for stage in settings.stages)


def test_load_latency_masking_settings() -> None:
    config_dir = Path.cwd() / "config"
    settings = load_latency_masking_settings(config_dir)

    assert settings.enabled is True
    assert "acknowledge_then_compute" in settings.contexts
    assert settings.contexts["network_delay_bridge"].max_delay_ms == 3200


def test_load_llm_endpoint() -> None:
    config_dir = Path.cwd() / "config"
    settings = load_llm_endpoint(config_dir)

    assert settings.provider == "github_models"
    assert settings.api_format == "openai_chat_completions"
    assert settings.default_model == "openai/gpt-4o-mini"
    assert settings.model_aliases["gpt_response_safe"] == "openai/gpt-4o-mini"
    assert settings.url


def test_load_tts_endpoint() -> None:
    config_dir = Path.cwd() / "config"
    settings = load_tts_endpoint(config_dir)

    assert settings.provider == "openai_compatible"
    assert settings.api_format == "json_audio_base64"
    assert settings.voice == "hu-HU-NoemiNeural"
    assert settings.url == "mock"