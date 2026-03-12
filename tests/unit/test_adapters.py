import base64
import json
from pathlib import Path
from types import SimpleNamespace

from assistant_runtime.adapters.factory import build_stt_adapter
from assistant_runtime.adapters.factory import build_tts_adapter
from assistant_runtime.adapters.llm_adapter import GenerationRequest
from assistant_runtime.adapters.llm_adapter import OpenAICompatibleLLMAdapter
from assistant_runtime.adapters.handoff_client import CrisisHandoffClient
from assistant_runtime.adapters.handoff_client import HandoffRequest
from assistant_runtime.adapters.stt_adapter import HttpSTTAdapter
from assistant_runtime.adapters.stt_adapter import TextPassthroughSTTAdapter
from assistant_runtime.adapters.tts_adapter import HttpTTSAdapter


def test_handoff_client_builds_expected_payload() -> None:
    client = CrisisHandoffClient(url="https://example.invalid/handoff", timeout_ms=2500, auth_env_var="TOKEN")
    payload = client.build_payload(
        HandoffRequest(
            conversation_id="conv-1",
            lang="hu",
            transcript="segitseg kell",
            gist="segitseg kell",
            risk_flags=["crisis", "handoff"],
            selected_category="crisis",
        )
    )
    assert payload["conversation_id"] == "conv-1"
    assert payload["selected_category"] == "crisis"


def test_http_stt_adapter_builds_language_header() -> None:
    adapter = HttpSTTAdapter(endpoint="https://example.invalid/stt", auth_env_var=None, language="hu")
    headers = adapter.build_headers()
    assert headers["X-Language"] == "hu"


def test_http_tts_adapter_builds_expected_payload_and_headers(monkeypatch) -> None:
    monkeypatch.setenv("TTS_TOKEN", "secret")
    adapter = HttpTTSAdapter(
        endpoint="https://example.invalid/tts",
        auth_env_var="TTS_TOKEN",
        voice="hu-HU-TestVoice",
    )

    headers = adapter.build_headers()
    payload = adapter.build_payload("Nyugtato valasz.", lang="hu", speed="slow")

    assert headers["Authorization"] == "Bearer secret"
    assert payload["text"] == "Nyugtato valasz."
    assert payload["voice"] == "hu-HU-TestVoice"
    assert payload["speed"] == "slow"


def test_http_tts_adapter_saves_base64_audio_response(tmp_path: Path, monkeypatch) -> None:
    class _DummyResponse:
        def __init__(self) -> None:
            self.headers = {"Content-Type": "application/json"}

        def read(self) -> bytes:
            return json.dumps(
                {
                    "audio_base64": base64.b64encode(b"RIFFfakewave").decode("ascii"),
                    "mime_type": "audio/wav",
                    "voice_name": "Remote Voice",
                }
            ).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    def _fake_urlopen(http_request, timeout=15):
        return _DummyResponse()

    monkeypatch.setattr("assistant_runtime.adapters.tts_adapter.request.urlopen", _fake_urlopen)
    adapter = HttpTTSAdapter(endpoint="https://example.invalid/tts", auth_env_var=None)

    result = adapter.synthesize("Nyugtato valasz.", tmp_path / "tts.wav", lang="hu")

    assert result.audio_path.exists()
    assert result.mime_type == "audio/wav"
    assert result.voice_name == "Remote Voice"


def test_build_tts_adapter_uses_http_when_online_tts_is_available(monkeypatch) -> None:
    monkeypatch.setattr(
        "assistant_runtime.adapters.factory.load_runtime_settings",
        lambda _config_dir: SimpleNamespace(tts_provider="powershell"),
    )
    monkeypatch.setattr(
        "assistant_runtime.adapters.factory.load_model_routing_settings",
        lambda _config_dir: SimpleNamespace(stages=[]),
    )
    monkeypatch.setattr(
        "assistant_runtime.adapters.factory.choose_stage_route",
        lambda *_args, **_kwargs: SimpleNamespace(selected_mode="online"),
    )
    monkeypatch.setattr(
        "assistant_runtime.adapters.factory.load_tts_endpoint",
        lambda _config_dir: SimpleNamespace(
            url="https://example.invalid/tts",
            auth_env_var="TTS_TOKEN",
            provider="openai_compatible",
            timeout_ms=9000,
            api_format="json_audio_base64",
            voice="hu-HU-TestVoice",
        ),
    )

    adapter = build_tts_adapter(Path.cwd() / "config", prefer_online=True)

    assert isinstance(adapter, HttpTTSAdapter)


def test_text_passthrough_adapter_reads_text_file(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_text("Ez egy minta transzkript.", encoding="utf-8")
    adapter = TextPassthroughSTTAdapter()
    transcript = adapter.transcribe(file_path)
    assert transcript.text == "Ez egy minta transzkript."


def test_build_stt_adapter_uses_online_http_when_conditions_require_fallback() -> None:
    adapter = build_stt_adapter(Path.cwd() / "config", active_conditions={"cpu_overloaded"})
    assert isinstance(adapter, HttpSTTAdapter)


def test_build_stt_adapter_uses_local_passthrough_by_default() -> None:
    adapter = build_stt_adapter(Path.cwd() / "config")
    assert isinstance(adapter, TextPassthroughSTTAdapter)


def test_openai_compatible_llm_adapter_builds_expected_payload() -> None:
    adapter = OpenAICompatibleLLMAdapter(endpoint="https://example.invalid/chat", auth_env_var=None)

    payload = adapter.build_payload(
        GenerationRequest(
            conversation_id="conv-1",
            lang="hu",
            prompt="Adj rovid valaszt.",
            system_prompt="Legyel rovid.",
            model="gpt-test",
            max_tokens=120,
        )
    )

    assert payload["model"] == "gpt-test"
    assert payload["messages"][0]["role"] == "system"
    assert payload["max_tokens"] == 120


def test_openai_compatible_llm_adapter_adds_github_models_headers() -> None:
    adapter = OpenAICompatibleLLMAdapter(
        endpoint="https://example.invalid/chat",
        auth_env_var=None,
        provider="github_models",
    )

    headers = adapter.build_headers()

    assert headers["Accept"] == "application/vnd.github+json"
    assert headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_openai_compatible_llm_adapter_uses_max_completion_tokens_for_gpt5_models() -> None:
    adapter = OpenAICompatibleLLMAdapter(endpoint="https://example.invalid/chat", auth_env_var=None)

    payload = adapter.build_payload(
        GenerationRequest(
            conversation_id="conv-1",
            lang="hu",
            prompt="Adj rovid valaszt.",
            system_prompt="Legyel rovid.",
            model="gpt-5-mini",
            max_tokens=120,
        )
    )

    assert payload["model"] == "gpt-5-mini"
    assert payload["max_completion_tokens"] == 120
    assert "max_tokens" not in payload
    assert "temperature" not in payload


def test_openai_compatible_llm_adapter_normalizes_github_copilot_model_prefix() -> None:
    adapter = OpenAICompatibleLLMAdapter(endpoint="https://example.invalid/chat", auth_env_var=None)

    payload = adapter.build_payload(
        GenerationRequest(
            conversation_id="conv-1",
            lang="hu",
            prompt="Adj rovid valaszt.",
            system_prompt="Legyel rovid.",
            model="github-copilot/gpt-5-mini",
            max_tokens=120,
        )
    )

    assert payload["model"] == "gpt-5-mini"
    assert payload["max_completion_tokens"] == 120
    assert "temperature" not in payload


def test_openai_compatible_llm_adapter_treats_gpt_5_1_models_as_gpt5_family() -> None:
    adapter = OpenAICompatibleLLMAdapter(endpoint="https://example.invalid/chat", auth_env_var=None)

    payload = adapter.build_payload(
        GenerationRequest(
            conversation_id="conv-1",
            lang="hu",
            prompt="Adj rovid valaszt.",
            system_prompt="Legyel rovid.",
            model="gpt-5.1-mini",
            max_tokens=120,
        )
    )

    assert payload["model"] == "gpt-5.1-mini"
    assert payload["max_completion_tokens"] == 120
    assert "max_tokens" not in payload
    assert "temperature" not in payload


def test_openai_compatible_llm_adapter_treats_vendor_prefixed_gpt5_models_as_gpt5_family() -> None:
    adapter = OpenAICompatibleLLMAdapter(endpoint="https://example.invalid/chat", auth_env_var=None)

    payload = adapter.build_payload(
        GenerationRequest(
            conversation_id="conv-1",
            lang="hu",
            prompt="Adj rovid valaszt.",
            system_prompt="Legyel rovid.",
            model="openai/gpt-5-mini",
            max_tokens=120,
        )
    )

    assert payload["model"] == "openai/gpt-5-mini"
    assert payload["max_completion_tokens"] == 120
    assert "max_tokens" not in payload
