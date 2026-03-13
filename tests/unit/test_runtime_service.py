import json
import os
from pathlib import Path

from assistant_runtime.adapters.llm_adapter import GenerationRequest
from assistant_runtime.adapters.llm_adapter import GenerationResponse
from assistant_runtime.adapters.tts_adapter import MockTTSAdapter
from assistant_runtime.profiles.registry import load_profile_registry
from assistant_runtime.runtime_service import _should_enable_phrase_rerank
from assistant_runtime.runtime_service import RuntimeService
from assistant_runtime.session_memory import ConversationMemoryStore


class _DummyLLMAdapter:
    def __init__(self, text: str = "Generalt tamogato valasz.") -> None:
        self.requests: list[GenerationRequest] = []
        self.text = text

    def is_ready(self) -> dict[str, object]:
        return {"status": "configured", "auth_configured": True}

    def generate(self, generation_request: GenerationRequest) -> GenerationResponse:
        self.requests.append(generation_request)
        return GenerationResponse(
            text=self.text,
            model=generation_request.model,
            finish_reason="stop",
        )


def test_runtime_service_process_text_returns_selection() -> None:
    service = RuntimeService(Path.cwd(), "hu")
    result = service.process_text("Szorongok es szeretnek segitseget kerni.")
    assert result.selection["text"]
    assert result.handoff_triggered is False


def test_runtime_service_can_attach_patient_context_and_contact_plan(tmp_path: Path) -> None:
    registry_path = tmp_path / "profiles.jsonc"
    registry_path.write_text(
        json.dumps(
            {
                "patients": [
                    {
                        "patient_id": "p-1",
                        "practice_id": "practice-1",
                        "assigned_clinician_id": "c-1",
                        "preferred_lang": "hu",
                        "timezone": "Europe/Budapest",
                        "demographics": {"age": "71"},
                        "history_policy": {
                                "allow_history_context": True,
                            "history_scope": "summary",
                                "auto_prefill_demographics": True
                        },
                        "communication_profile": {
                            "literacy_level": "low",
                            "preferred_register": "plain",
                            "personas": ["retiree"],
                            "preferences": {"prefer_text": True}
                        },
                        "history_summary": "Prior visit summary.",
                        "emergency_contacts": []
                    }
                ],
                "clinicians": [
                    {
                        "clinician_id": "c-1",
                        "practice_id": "practice-1",
                        "display_name": "Dr. Example",
                        "role": "psychiatrist",
                            "after_hours_opt_in": True,
                        "contact_channels": [
                            {
                                "channel_type": "secure_chat",
                                "target": "clinician-chat",
                                "purpose": "clinical escalation",
                                "priority": 2,
                                    "after_hours": True,
                                    "automated": False
                            }
                        ]
                    }
                ],
                "assistants": [
                    {
                        "assistant_id": "a-1",
                        "practice_id": "practice-1",
                        "display_name": "Assistant",
                        "coverage_windows": ["after_hours"],
                        "contact_channels": [
                            {
                                "channel_type": "secure_chat",
                                "target": "assistant-chat",
                                "purpose": "triage",
                                "priority": 1,
                                    "after_hours": True,
                                    "automated": False
                            }
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry = load_profile_registry(registry_path)
    service = RuntimeService(Path.cwd(), "hu", profile_registry=registry)
    result = service.process_text("Meg akarok meghalni, kerlek segits.", patient_id="p-1")
    assert result.patient_context is not None
    assert result.patient_context["assigned_clinician_id"] == "c-1"
    assert result.patient_context["communication_profile"]["literacy_level"] == "low"
    assert result.selection["profile_alignment"]["lit"] == "match"
    assert result.selection["delivery_preferences"]["tts_speed"] == "slow"
    assert result.contact_plan is not None


def test_runtime_service_uses_trigger_candidates_for_style_adaptation() -> None:
    service = RuntimeService(Path.cwd(), "hu")
    result = service.process_text("Egyszerűbb nyelven mondd.")

    assert result.matched_trigger_id is not None
    assert result.selection["category"] == "cultural"
    assert result.selection["item_id"] in {"cult_002", "cult_004", "cult_006"}


def test_runtime_service_matches_unaccented_breakup_trigger() -> None:
    service = RuntimeService(Path.cwd(), "hu")
    result = service.process_text("Szakitas utan vagyok.")

    assert result.matched_trigger_id == "pt_tr_048"
    assert result.selection["category"] == "open_questions"
    assert result.selection["item_id"] == "oq_007"


def test_runtime_service_can_use_online_reranker_with_recent_history() -> None:
    llm = _DummyLLMAdapter(text='{"candidate_id":"oq_002","kb_ids":["kb_hu_002"],"reason":"Keruljuk az elozo kerdes pontos ismetleset."}')
    service = RuntimeService(Path.cwd(), "hu", llm_client=llm)

    first = service.process_text("Szakitas utan vagyok.", conversation_id="rerank-1")
    second = service.process_text(
        "Szakitas utan vagyok.",
        conversation_id="rerank-1",
        prefer_online=True,
    )

    assert first.selection["item_id"] == "oq_007"
    assert second.selection["item_id"] == "oq_002"
    assert second.hybrid_selection is not None
    assert second.hybrid_selection["status"] == "completed"
    assert second.hybrid_selection["strategy"] == "online_candidate_rerank"
    assert second.hybrid_selection["selected_kb_ids"] == ["kb_hu_002"]
    assert second.conversation_summary is not None
    assert second.conversation_summary["turn_count"] == 4
    assert "Recent assistant items" in second.conversation_summary["summary_text"]
    assert "patient_themes" in second.conversation_summary["active_summary"]
    assert second.conversation_summary["active_summary"]["assistant_items"]
    assert second.conversation_summary["active_summary"]["recent_knowledge_ids"]
    assert "active_tags" in second.conversation_summary["active_summary"]
    assert second.conversation_summary["active_summary"]["recent_response_strategies"]
    assert second.knowledge_context is not None
    assert second.knowledge_context[0]["id"] == "kb_hu_002"
    assert llm.requests[-1].model == "openai/gpt-4o-mini"
    assert "oq_007" in llm.requests[-1].prompt
    assert "Conversation summary:" in llm.requests[-1].prompt
    assert "Knowledge snippets:" in llm.requests[-1].prompt
    assert "Structured conversation state:" in llm.requests[-1].prompt


def test_high_risk_turn_disables_online_phrase_rerank() -> None:
    llm = _DummyLLMAdapter()

    enabled = _should_enable_phrase_rerank(
        llm,
        {"selected_mode": "online", "selected_model": "gpt-4o-mini"},
        [{"item_id": "a"}, {"item_id": "b"}],
        {"status": "configured"},
        {"crisis"},
    )

    assert enabled is False


def test_runtime_service_can_opt_in_review_content_for_testing() -> None:
    service = RuntimeService(Path.cwd(), "hu")
    service.bundle.categories = {
        "empathy": [
            {
                "id": "emp_review_001",
                "pri": 1,
                "rec": ["n"],
                "use": ["c"],
                "tags": ["emp"],
                "meta": {"src": "trn", "status": "rev", "enabled_in": ["rv", "tst"]},
                "pp": [{"txt": "Review phrase.", "t": "n", "l": "s"}],
            }
        ]
    }
    service.bundle.manifest["category_order"] = [{"name": "empathy", "default_priority": 1}]
    service.knowledge_snippets = []

    result = service.process_text(
        "Szorongok es szeretnek segitseget kerni.",
        profile_overrides={
            "content_statuses": ["appr", "rev", "test", "sugg"],
            "content_channel": "tst",
        },
    )

    assert result.selection["item_id"] == "emp_review_001"
    assert result.selection["content_meta"]["status"] == "rev"


def test_runtime_service_reuses_patient_memory_across_conversations(tmp_path: Path) -> None:
    registry_path = tmp_path / "profiles.jsonc"
    registry_path.write_text(
        json.dumps(
            {
                "patients": [
                    {
                        "patient_id": "p-42",
                        "practice_id": "practice-1",
                        "assigned_clinician_id": "c-1",
                        "preferred_lang": "hu",
                        "timezone": "Europe/Budapest",
                        "history_policy": {
                            "allow_history_context": True,
                            "history_scope": "summary",
                            "auto_prefill_demographics": True
                        },
                        "communication_profile": {
                            "literacy_level": "low",
                            "preferred_register": "plain"
                        },
                        "history_summary": "Korabbi szorongasos epizodok.",
                        "emergency_contacts": []
                    }
                ],
                "clinicians": [],
                "assistants": []
            }
        ),
        encoding="utf-8",
    )
    registry = load_profile_registry(registry_path)
    memory_store = ConversationMemoryStore(
        ttl_seconds=3600,
        persistence_path=tmp_path / "conversation_memory.json",
    )
    service = RuntimeService(
        Path.cwd(),
        "hu",
        profile_registry=registry,
        conversation_memory=memory_store,
    )

    first = service.process_text(
        "Szakitas utan vagyok.",
        conversation_id="conv-a",
        patient_id="p-42",
    )
    second = service.process_text(
        "Meg mindig nehez.",
        conversation_id="conv-b",
        patient_id="p-42",
    )

    assert first.patient_identity is not None
    assert first.patient_identity["memory_key"] == "patient:p-42"
    assert second.patient_identity is not None
    assert second.patient_identity["memory_key"] == "patient:p-42"
    assert second.conversation_summary is not None
    assert second.conversation_summary["turn_count"] >= 4
    assert second.conversation_summary["recent_turns"]
    assert second.conversation_summary["active_summary"]["recent_categories"]
    assert (tmp_path / "conversation_memory.json").exists()


def test_runtime_service_reuses_anonymous_subject_memory_across_conversations(tmp_path: Path) -> None:
    memory_store = ConversationMemoryStore(
        ttl_seconds=3600,
        persistence_path=tmp_path / "conversation_memory.json",
    )
    service = RuntimeService(
        Path.cwd(),
        "hu",
        conversation_memory=memory_store,
    )

    first = service.process_text(
        "Nehezen alszom mostanában.",
        conversation_id="anon-a",
        patient_identity={
            "anonymous_subject_key": "anonpt_shared_1",
            "clinician_id": "dr-kovacs",
            "identity_confidence": "clinician_issued_token",
            "consent_to_store_excerpt": True,
        },
    )
    second = service.process_text(
        "Még mindig zaklatott vagyok este.",
        conversation_id="anon-b",
        patient_identity={
            "anonymous_subject_key": "anonpt_shared_1",
            "clinician_id": "dr-kovacs",
            "identity_confidence": "clinician_issued_token",
            "consent_to_store_excerpt": True,
        },
    )

    assert first.patient_identity is not None
    assert first.patient_identity["memory_key"] == "anon:anonpt_shared_1"
    assert second.patient_identity is not None
    assert second.patient_identity["memory_key"] == "anon:anonpt_shared_1"
    assert second.patient_identity["identity_mode"] == "anonymous_subject"
    assert second.patient_identity["clinician_id"] == "dr-kovacs"
    assert second.conversation_summary is not None
    assert second.conversation_summary["turn_count"] >= 4


def test_runtime_service_returns_route_decisions_and_latency_preamble() -> None:
    service = RuntimeService(Path.cwd(), "hu")
    result = service.process_text(
        "Szorongok es szeretnek segitseget kerni.",
        active_conditions={"device_cpu_overloaded"},
        latency_context="acknowledge_then_compute",
        latency_elapsed_ms=200,
    )

    assert result.route_decisions is not None
    assert result.route_decisions["intent_and_risk"]["stage"] == "intent_and_risk"
    assert result.route_decisions["tts"]["selected_mode"] == "online"
    assert result.latency_preamble


def test_runtime_service_process_audio_builds_default_adapter(tmp_path: Path) -> None:
    transcript_path = tmp_path / "transcript.txt"
    transcript_path.write_text("Egyszerűbb nyelven mondd.", encoding="utf-8")

    service = RuntimeService(Path.cwd(), "hu")
    result = service.process_audio(transcript_path)

    assert result.selection["text"]
    assert result.matched_trigger_id is not None
    assert result.route_decisions is not None
    assert result.route_decisions["stt"]["selected_mode"] == "local"


def test_runtime_service_can_synthesize_response_audio(tmp_path: Path) -> None:
    service = RuntimeService(Path.cwd(), "hu")
    service.project_root = tmp_path

    payload = service.synthesize_response_audio(
        "Rovid nyugtato valasz.",
        delivery_preferences={"tts_speed": "slow"},
        adapter=MockTTSAdapter(),
    )

    assert payload["audio_file_name"].endswith(".wav")
    assert payload["speed"] == "slow"
    assert (tmp_path / "data" / "runtime_state" / "generated_audio" / payload["audio_file_name"]).exists()


def test_runtime_service_uses_llm_for_generative_fallback_when_enabled() -> None:
    llm = _DummyLLMAdapter()
    service = RuntimeService(Path.cwd(), "hu", llm_client=llm)
    service.runtime_settings.generative_fallback_enabled = True
    service.bundle.categories = {name: [] for name in service.bundle.categories}

    result = service.process_text("Nem tudom pontosan, hogyan fogalmazzam meg.")

    assert result.selection["category"] == "generative_fallback"
    assert result.selection["text"] == "Generalt tamogato valasz."
    assert result.generation_request is not None
    assert result.generation_request["status"] == "completed"
    assert result.route_decisions is not None
    assert result.route_decisions["generative_fallback"]["selected_model"]
    assert llm.requests


def test_runtime_service_uses_model_override_when_present(monkeypatch) -> None:
    llm = _DummyLLMAdapter()
    service = RuntimeService(Path.cwd(), "hu", llm_client=llm)
    service.runtime_settings.generative_fallback_enabled = True
    service.bundle.categories = {name: [] for name in service.bundle.categories}
    monkeypatch.setenv("LLM_MODEL_OVERRIDE", "gpt-4o-mini")

    service.process_text("Nem tudom pontosan, hogyan fogalmazzam meg.")

    assert llm.requests[-1].model == "gpt-4o-mini"


def test_runtime_service_uses_endpoint_default_model_when_route_model_missing() -> None:
    llm = _DummyLLMAdapter()
    service = RuntimeService(Path.cwd(), "hu", llm_client=llm)
    service.runtime_settings.generative_fallback_enabled = True
    service.bundle.categories = {name: [] for name in service.bundle.categories}
    for stage in service.model_routing_settings.stages:
        if stage.stage == "generative_fallback":
            stage.online_model = None

    service.process_text("Nem tudom pontosan, hogyan fogalmazzam meg.")

    assert llm.requests[-1].model == "openai/gpt-4o-mini"


def test_runtime_service_normalizes_prefixed_model_override(monkeypatch) -> None:
    llm = _DummyLLMAdapter()
    service = RuntimeService(Path.cwd(), "hu", llm_client=llm)
    service.runtime_settings.generative_fallback_enabled = True
    service.bundle.categories = {name: [] for name in service.bundle.categories}
    monkeypatch.setenv("LLM_MODEL_OVERRIDE", "github-copilot/gpt-5-mini")

    service.process_text("Nem tudom pontosan, hogyan fogalmazzam meg.")

    assert llm.requests[-1].model == "gpt-5-mini"


class _FailingLLMAdapter:
    def is_ready(self) -> dict[str, object]:
        return {"status": "configured", "auth_configured": True}

    def generate(self, generation_request: GenerationRequest) -> GenerationResponse:
        raise RuntimeError("simulated upstream failure")


def test_runtime_service_puts_phrase_selection_online_stage_into_cooldown_after_failure() -> None:
    service = RuntimeService(Path.cwd(), "hu", llm_client=_FailingLLMAdapter())

    result = service.process_text(
        "Szakitas utan vagyok.",
        conversation_id="cooldown-1",
        prefer_online=True,
    )

    assert result.hybrid_selection is not None
    assert result.hybrid_selection["status"] == "fallback_local"
    assert result.hybrid_selection["online_stage_status"]["status"] == "cooldown"
    assert result.hybrid_selection["error_type"] == "RuntimeError"
