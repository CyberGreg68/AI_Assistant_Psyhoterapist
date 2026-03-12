from __future__ import annotations

from dataclasses import asdict
import os
from pathlib import Path
from typing import Any

from assistant_runtime.adapters.stt_adapter import MockSTTAdapter
from assistant_runtime.adapters.stt_adapter import TextPassthroughSTTAdapter
from assistant_runtime.content_metadata import content_meta
from assistant_runtime.config.loader import load_llm_endpoint
from assistant_runtime.config.loader import load_tts_endpoint
from assistant_runtime.core.operations_snapshot import build_operations_snapshot
from assistant_runtime.live.runtime_service import RuntimeService
from assistant_runtime.serialization import normalize_for_json


def health_payload(project_root: Path | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"status": "ok"}
    if project_root is None:
        return payload

    endpoint = load_llm_endpoint(project_root / "config")
    auth_env_var = endpoint.auth_env_var
    payload["llm"] = {
        "provider": endpoint.provider,
        "url": endpoint.url,
        "default_model": endpoint.default_model,
        "auth_env_var": auth_env_var,
        "auth_configured": bool(auth_env_var and os.getenv(auth_env_var)),
    }
    tts_endpoint = load_tts_endpoint(project_root / "config")
    payload["tts"] = {
        "provider": tts_endpoint.provider,
        "url": tts_endpoint.url,
        "api_format": tts_endpoint.api_format,
        "voice": tts_endpoint.voice,
        "auth_env_var": tts_endpoint.auth_env_var,
        "auth_configured": bool(tts_endpoint.auth_env_var and os.getenv(tts_endpoint.auth_env_var)),
    }
    return payload


def operations_payload(project_root: Path) -> dict[str, object]:
    return build_operations_snapshot(project_root / "config")


def _find_trigger(service: RuntimeService, trigger_id: str | None) -> dict[str, Any] | None:
    if not trigger_id:
        return None
    for short_code, items in service.bundle.triggers.items():
        for item in items:
            if item.get("id") == trigger_id:
                return {"short_code": short_code, **item}
    return None


def _find_phrase_item(service: RuntimeService, category_name: str | None, item_id: str | None) -> dict[str, Any] | None:
    if not category_name or not item_id:
        return None
    for item in service.bundle.categories.get(category_name, []):
        if item.get("id") == item_id:
            return item
    return None


def _build_debug_payload(service: RuntimeService, result: Any) -> dict[str, object]:
    trigger = _find_trigger(service, result.matched_trigger_id)
    selection = result.selection or {}
    phrase_item = _find_phrase_item(service, selection.get("category"), selection.get("item_id"))
    identity = result.patient_identity or {}
    memory_key = str(identity.get("memory_key") or result.conversation_id)
    conversation_history = service.get_conversation_history(memory_key)
    conversation_summary = service.get_conversation_summary(memory_key)
    selected_variant_index = None
    if phrase_item is not None:
        for index, variant in enumerate(phrase_item.get("pp", [])):
            if variant.get("txt") == selection.get("text"):
                selected_variant_index = index
                break

    explanation: list[str] = []
    explanation.append(
        f"Intent: {result.analysis.intent}; tags: {', '.join(sorted(result.analysis.tags)) or 'none'}; risk: {', '.join(sorted(result.analysis.risk_flags)) or 'none'}."
    )
    if trigger is not None:
        explanation.append(
            f"Matched trigger {trigger['id']} in {trigger.get('cat')} with fallback {trigger.get('fb')} and candidates {', '.join(trigger.get('cand', [])) or 'none'}."
        )
    else:
        explanation.append("No trigger matched; selection came from the normal phrase search path.")

    if selection.get("category") == "generative_fallback":
        explanation.append("No phrase candidate survived selection, so the runtime used generative fallback.")
    elif phrase_item is not None:
        explanation.append(
            f"Selected phrase {phrase_item['id']} from category {selection.get('category')} with tags {', '.join(phrase_item.get('tags', [])) or 'none'}."
        )
    else:
        explanation.append("A selection was returned, but no phrase item metadata was resolved for debug output.")

    if result.hybrid_selection is not None:
        explanation.append(
            f"Hybrid selection status: {result.hybrid_selection.get('status')} via {result.hybrid_selection.get('strategy')}."
        )
    if result.knowledge_context:
        explanation.append(
            "Knowledge snippets: "
            + ", ".join(str(item.get("id")) for item in result.knowledge_context)
        )
    if conversation_summary.get("turn_count"):
        explanation.append(
            f"Conversation summary: {conversation_summary.get('summary_text')}"
        )

    if result.handoff_triggered:
        explanation.append("Handoff was triggered because the risk rules marked the message as requiring escalation.")
    else:
        explanation.append("No handoff was triggered for this turn.")

    payload = {
        "explanation": explanation,
        "analysis": asdict(result.analysis),
        "matched_trigger": None,
        "selected_phrase": None,
        "route_decisions": result.route_decisions,
        "patient_identity": result.patient_identity,
        "hybrid_selection": result.hybrid_selection,
        "knowledge_context": result.knowledge_context,
        "online_stage_status": service.get_online_stage_statuses(),
        "conversation_history": conversation_history,
        "conversation_summary": conversation_summary,
        "generation_request": result.generation_request,
        "handoff_triggered": result.handoff_triggered,
    }

    if trigger is not None:
        payload["matched_trigger"] = {
            "id": trigger.get("id"),
            "short_code": trigger.get("short_code"),
            "category": trigger.get("cat"),
            "tags": trigger.get("tags", []),
            "content_meta": content_meta(trigger),
            "safety": trigger.get("safety"),
            "fallback": trigger.get("fb"),
            "candidate_ids": trigger.get("cand", []),
            "examples": trigger.get("ex", []),
        }

    if phrase_item is not None:
        payload["selected_phrase"] = {
            "id": phrase_item.get("id"),
            "category": selection.get("category"),
            "tags": phrase_item.get("tags", []),
            "content_meta": content_meta(phrase_item),
            "priority": phrase_item.get("pri"),
            "register": phrase_item.get("reg"),
            "literacy": phrase_item.get("lit"),
            "age": phrase_item.get("age", []),
            "variant_count": len(phrase_item.get("pp", [])),
            "selected_variant_index": selected_variant_index,
            "variants": [variant.get("txt") for variant in phrase_item.get("pp", [])],
        }

    return normalize_for_json(payload)


def process_text_payload(service: RuntimeService, payload: dict[str, Any]) -> dict[str, object]:
    result = service.process_text(
        payload["text"],
        conversation_id=payload.get("conversation_id", "admin-api"),
        patient_id=payload.get("patient_id"),
        patient_identity=payload.get("patient_identity"),
        active_conditions=set(payload.get("active_conditions", [])),
        prefer_online=bool(payload.get("prefer_online", False)),
        latency_context=payload.get("latency_context"),
        latency_elapsed_ms=int(payload.get("latency_elapsed_ms", 0)),
        latency_channel=str(payload.get("latency_channel", "chat")),
        profile_overrides=payload.get("profile_overrides"),
    )
    response_payload = normalize_for_json(asdict(result))
    if payload.get("debug"):
        response_payload["debug"] = _build_debug_payload(service, result)
    if payload.get("synthesize_speech"):
        response_payload["tts"] = normalize_for_json(
            service.synthesize_response_audio(
                response_payload["selection"]["text"],
                delivery_preferences=response_payload["selection"].get("delivery_preferences"),
                active_conditions=set(payload.get("active_conditions", [])),
                prefer_online=bool(payload.get("prefer_online", False)),
            )
        )
    return response_payload


def process_audio_payload(service: RuntimeService, payload: dict[str, Any]) -> dict[str, object]:
    result = service.process_audio(
        Path(payload["audio_path"]),
        conversation_id=payload.get("conversation_id", "admin-api"),
        patient_id=payload.get("patient_id"),
        patient_identity=payload.get("patient_identity"),
        active_conditions=set(payload.get("active_conditions", [])),
        prefer_online=bool(payload.get("prefer_online", False)),
        latency_context=payload.get("latency_context"),
        latency_elapsed_ms=int(payload.get("latency_elapsed_ms", 0)),
        latency_channel=str(payload.get("latency_channel", "chat")),
        profile_overrides=payload.get("profile_overrides"),
    )
    response_payload = normalize_for_json(asdict(result))
    if payload.get("synthesize_speech"):
        response_payload["tts"] = normalize_for_json(
            service.synthesize_response_audio(
                response_payload["selection"]["text"],
                delivery_preferences=response_payload["selection"].get("delivery_preferences"),
                active_conditions=set(payload.get("active_conditions", [])),
                prefer_online=bool(payload.get("prefer_online", False)),
            )
        )
    return response_payload


def process_audio_upload_payload(
    service: RuntimeService,
    payload: dict[str, Any],
    uploaded_audio_path: Path,
) -> dict[str, object]:
    transcript_text = str(payload.get("transcript_text") or "").strip()
    adapter = TextPassthroughSTTAdapter() if transcript_text else MockSTTAdapter()
    audio_path = uploaded_audio_path
    transcript_path = None
    if transcript_text:
        transcript_path = uploaded_audio_path.with_suffix(".txt")
        transcript_path.write_text(transcript_text, encoding="utf-8")
        audio_path = transcript_path
    try:
        result = service.process_audio(
            audio_path,
            adapter=adapter,
            conversation_id=payload.get("conversation_id", "admin-api"),
            patient_id=payload.get("patient_id"),
            patient_identity=payload.get("patient_identity"),
            active_conditions=set(payload.get("active_conditions", [])),
            prefer_online=bool(payload.get("prefer_online", False)),
            latency_context=payload.get("latency_context"),
            latency_elapsed_ms=int(payload.get("latency_elapsed_ms", 0)),
            latency_channel=str(payload.get("latency_channel", "chat")),
            profile_overrides=payload.get("profile_overrides"),
        )
        response_payload = normalize_for_json(asdict(result))
        response_payload["uploaded_audio"] = {
            "audio_path": str(uploaded_audio_path),
            "transcript_override_used": bool(transcript_text),
        }
        if payload.get("debug"):
            response_payload["debug"] = _build_debug_payload(service, result)
        if payload.get("synthesize_speech"):
            response_payload["tts"] = normalize_for_json(
                service.synthesize_response_audio(
                    response_payload["selection"]["text"],
                    delivery_preferences=response_payload["selection"].get("delivery_preferences"),
                    active_conditions=set(payload.get("active_conditions", [])),
                    prefer_online=bool(payload.get("prefer_online", False)),
                )
            )
        return response_payload
    finally:
        if transcript_path is not None and transcript_path.exists():
            transcript_path.unlink(missing_ok=True)