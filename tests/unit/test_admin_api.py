from pathlib import Path

from assistant_runtime.admin_api import health_payload
from assistant_runtime.admin_api import operations_payload
from assistant_runtime.admin_api import process_audio_upload_payload
from assistant_runtime.admin_api import process_text_payload
from assistant_runtime.runtime_service import RuntimeService


def test_health_payload_is_ok() -> None:
    assert health_payload()["status"] == "ok"


def test_health_payload_exposes_llm_auth_status() -> None:
    payload = health_payload(Path.cwd())

    assert payload["status"] == "ok"
    assert payload["llm"]["provider"] == "github_models"
    assert payload["llm"]["auth_env_var"] == "LLM_API_TOKEN"
    assert payload["llm"]["url"] == "https://models.github.ai/inference/chat/completions"
    assert payload["tts"]["auth_env_var"] == "TTS_API_TOKEN"
    assert payload["tts"]["api_format"] == "json_audio_base64"


def test_operations_payload_contains_pipeline() -> None:
    payload = operations_payload(Path.cwd())

    assert payload["pipeline"]["stages"]


def test_process_text_payload_returns_serializable_result() -> None:
    service = RuntimeService(Path.cwd(), "hu")

    payload = process_text_payload(
        service,
        {
            "text": "Szorongok es szeretnek segitseget kerni.",
            "latency_context": "acknowledge_then_compute",
            "latency_elapsed_ms": 200,
        },
    )

    assert payload["selection"]["text"]
    assert payload["latency_preamble"]


def test_process_text_payload_applies_profile_overrides() -> None:
    service = RuntimeService(Path.cwd(), "hu")

    payload = process_text_payload(
        service,
        {
            "text": "Egyszerűbb nyelven mondd.",
            "profile_overrides": {
                "age_group": "senior",
                "literacy_level": "low",
                "preferred_register": "plain",
                "preferences": {"tts_speed": "fast"},
            },
        },
    )

    assert payload["selection"]["delivery_preferences"]["tts_speed"] == "fast"


def test_process_text_payload_can_return_debug_explanation() -> None:
    service = RuntimeService(Path.cwd(), "hu")

    payload = process_text_payload(
        service,
        {
            "text": "Szakítás után vagyok.",
            "patient_identity": {
                "browser_patient_key": "anon-debug-1",
                "consent_to_store_excerpt": True,
            },
            "debug": True,
        },
    )

    assert "debug" in payload
    assert payload["debug"]["explanation"]
    assert payload["debug"]["matched_trigger"]["id"] == "pt_tr_048"
    assert payload["debug"]["conversation_history"]
    assert payload["debug"]["conversation_summary"]["turn_count"] >= 2
    assert payload["debug"]["patient_identity"]["memory_key"].startswith("browser:")
    assert payload["debug"]["hybrid_selection"]["status"] == "local_only"
    assert "phrase_selection" in payload["debug"]["online_stage_status"]
    assert payload["debug"]["knowledge_context"]
    assert payload["debug"]["selected_phrase"]["content_meta"]["status"] in {"appr", "rev"}
    assert payload["debug"]["matched_trigger"]["content_meta"]["status"] in {"appr", "rev"}


def test_process_text_payload_uses_stable_browser_patient_identity() -> None:
    service = RuntimeService(Path.cwd(), "hu")

    first = process_text_payload(
        service,
        {
            "text": "Nem tudom, hogyan kezdjem.",
            "conversation_id": "browser-a",
            "patient_identity": {
                "browser_patient_key": "anon-stable-1",
                "consent_to_store_excerpt": True,
            },
        },
    )
    second = process_text_payload(
        service,
        {
            "text": "Meg mindig nehez rola beszelni.",
            "conversation_id": "browser-b",
            "patient_identity": {
                "browser_patient_key": "anon-stable-1",
                "consent_to_store_excerpt": True,
            },
            "debug": True,
        },
    )

    assert first["patient_identity"]["memory_key"] == "browser:anon-stable-1"
    assert second["patient_identity"]["memory_key"] == "browser:anon-stable-1"
    assert second["debug"]["conversation_summary"]["turn_count"] >= 4


def test_process_audio_upload_payload_accepts_transcript_override(tmp_path: Path) -> None:
    service = RuntimeService(Path.cwd(), "hu")
    uploaded_audio = tmp_path / "sample.webm"
    uploaded_audio.write_bytes(b"fake-audio")

    payload = process_audio_upload_payload(
        service,
        {
            "transcript_text": "Egyszerubb nyelven mondd.",
            "debug": True,
        },
        uploaded_audio,
    )

    assert payload["selection"]["text"]
    assert payload["uploaded_audio"]["transcript_override_used"] is True
    assert payload["debug"]["conversation_summary"]["turn_count"] >= 2