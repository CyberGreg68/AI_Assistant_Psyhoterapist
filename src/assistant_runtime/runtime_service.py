from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import secrets
import time
from typing import Iterable

from assistant_runtime.adapters.factory import build_llm_adapter
from assistant_runtime.adapters.factory import build_stt_adapter
from assistant_runtime.adapters.factory import build_tts_adapter
from assistant_runtime.adapters.handoff_client import CrisisHandoffClient
from assistant_runtime.adapters.handoff_client import HandoffRequest
from assistant_runtime.audit_logger import AuditLogger
from assistant_runtime.adapters.llm_adapter import GenerationRequest
from assistant_runtime.adapters.llm_adapter import LLMAdapter
from assistant_runtime.adapters.llm_adapter import LLMServiceError
from assistant_runtime.adapters.llm_adapter import OpenAICompatibleLLMAdapter
from assistant_runtime.adapters.stt_adapter import STTAdapter
from assistant_runtime.adapters.tts_adapter import SynthesizedAudio
from assistant_runtime.adapters.tts_adapter import TTSAdapter
from assistant_runtime.config.loader import load_cache_settings
from assistant_runtime.config.loader import load_llm_endpoint
from assistant_runtime.config.loader import load_latency_masking_settings
from assistant_runtime.config.loader import load_model_routing_settings
from assistant_runtime.config.loader import load_runtime_settings
from assistant_runtime.config.loader import load_token_limits
from assistant_runtime.core.latency_masking import build_latency_preamble
from assistant_runtime.knowledge_base import load_knowledge_snippets
from assistant_runtime.knowledge_base import load_knowledge_snippets_from_payload
from assistant_runtime.knowledge_base import retrieve_knowledge_snippets
from assistant_runtime.manifest_loader import load_bundle
from assistant_runtime.core.model_router import StageRouteDecision
from assistant_runtime.core.model_router import get_stage_definition
from assistant_runtime.core.model_router import choose_stage_route
from assistant_runtime.pipeline.analysis_pipeline import AnalysisResult
from assistant_runtime.pipeline.analysis_pipeline import analyze_text
from assistant_runtime.pipeline.risk_rules import requires_handoff
from assistant_runtime.profiles.models import PatientProfile
from assistant_runtime.profiles.registry import load_profile_registry
from assistant_runtime.profiles.registry import ProfileRegistry
from assistant_runtime.profiles.registry import summarize_patient_context
from assistant_runtime.routing.contact_router import ContactPlan
from assistant_runtime.routing.contact_router import build_after_hours_contact_plan
from assistant_runtime.core.selection_engine import list_phrase_candidates
from assistant_runtime.core.selection_engine import SelectionRequest
from assistant_runtime.core.selection_engine import select_phrase
from assistant_runtime.session_memory import ConversationIdentity
from assistant_runtime.session_memory import ConversationMemoryStore
from assistant_runtime.session_memory import ConversationTurn
from assistant_runtime.trigger_matcher import CATEGORY_NAME_BY_SHORT
from assistant_runtime.trigger_matcher import fallback_category_name
from assistant_runtime.trigger_matcher import match_trigger


@dataclass(slots=True)
class RuntimeResult:
    conversation_id: str
    conversation_summary: dict[str, object] | None
    patient_identity: dict[str, object] | None
    analysis: AnalysisResult
    selection: dict
    handoff_triggered: bool
    patient_context: dict | None = None
    contact_plan: ContactPlan | None = None
    matched_trigger_id: str | None = None
    route_decisions: dict[str, dict[str, object]] | None = None
    generation_request: dict[str, object] | None = None
    latency_preamble: str = ""
    hybrid_selection: dict[str, object] | None = None
    knowledge_context: list[dict[str, object]] | None = None


def _route_to_dict(decision: StageRouteDecision) -> dict[str, object]:
    return {
        "stage": decision.stage,
        "selected_mode": decision.selected_mode,
        "selected_model": decision.selected_model,
        "fallback_mode": decision.fallback_mode,
        "trigger_reasons": list(decision.trigger_reasons),
    }


def _build_generation_prompt(
    text: str,
    analysis: AnalysisResult,
    patient: PatientProfile | None,
    knowledge_context: list[dict[str, object]] | None,
    history_summary: dict[str, object] | None,
    profile_overrides: dict[str, object] | None = None,
) -> str:
    prompt_parts = [
        "Nyelv: hu",
        f"Felhasznaloi uzenet: {text}",
        f"Intent: {analysis.intent}",
        f"Kockazati jelzesek: {', '.join(sorted(analysis.risk_flags)) or 'nincs'}",
        "Adj rovid, empatikus, biztonsagos valaszt. Ha krizisre utal valami, biztonsagi kovetkezo lepest javasolj.",
    ]
    if patient is not None and patient.communication_profile.preferred_register:
        prompt_parts.append(
            f"Preferalt nyelvi regiszter: {patient.communication_profile.preferred_register}"
        )
    elif isinstance(profile_overrides, dict) and profile_overrides.get("preferred_register"):
        prompt_parts.append(
            f"Preferalt nyelvi regiszter: {profile_overrides['preferred_register']}"
        )
    if patient is not None and patient.communication_profile.literacy_level:
        prompt_parts.append(
            f"Olvasasi/megertesi szint: {patient.communication_profile.literacy_level}"
        )
    elif isinstance(profile_overrides, dict) and profile_overrides.get("literacy_level"):
        prompt_parts.append(
            f"Olvasasi/megertesi szint: {profile_overrides['literacy_level']}"
        )
    if history_summary:
        prompt_parts.append(
            f"Conversation summary: {history_summary.get('summary_text', 'No recent conversation summary.')}"
        )
        prompt_parts.append(
            "Structured conversation state: "
            + json.dumps(history_summary.get("active_summary", {}), ensure_ascii=True)
        )
    if knowledge_context:
        prompt_parts.append("Hasznalhato helyi tudaselemek:")
        for snippet in knowledge_context:
            prompt_parts.append(
                json.dumps(
                    {
                        "kb_id": snippet["id"],
                        "text": snippet["text"],
                        "topics": snippet.get("topics", []),
                    },
                    ensure_ascii=True,
                )
            )
    return "\n".join(prompt_parts)


def _resolve_generation_model(
    selected_model: str | None,
    endpoint: object,
) -> str:
    override = os.getenv("LLM_MODEL_OVERRIDE")
    if override:
        return OpenAICompatibleLLMAdapter.normalize_model_name(override)

    resolved = selected_model
    aliases = getattr(endpoint, "model_aliases", {}) or {}
    if resolved in aliases:
        resolved = aliases[resolved]

    if resolved:
        resolved = OpenAICompatibleLLMAdapter.normalize_model_name(resolved)

    return resolved or getattr(endpoint, "default_model", None) or "unknown"


def _load_default_profile_registry(project_root: Path) -> ProfileRegistry | None:
    config_dir = project_root / "config"
    for file_name in ("profile_registry.generated.jsonc", "profile_registry.example.jsonc"):
        file_path = config_dir / file_name
        if not file_path.exists():
            continue
        try:
            registry = load_profile_registry(file_path)
        except Exception:
            continue
        if registry.patients or file_name.endswith("generated.jsonc"):
            return registry
    return None


def _normalize_identity_value(value: object) -> str | None:
    if value is None:
        return None
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value).strip())
    normalized = normalized.strip("-")
    if not normalized:
        return None
    return normalized[:80]


def _resolve_patient_identity(
    conversation_id: str,
    patient_id: str | None,
    patient_identity: dict[str, object] | None,
    registry: ProfileRegistry | None,
) -> ConversationIdentity:
    normalized_patient_id = _normalize_identity_value(patient_id)
    browser_patient_key = None
    if isinstance(patient_identity, dict):
        browser_patient_key = _normalize_identity_value(patient_identity.get("browser_patient_key"))

    if normalized_patient_id:
        verified_patient = bool(registry and registry.get_patient(normalized_patient_id))
        identity_mode = "registered_patient" if verified_patient else "external_patient"
        return ConversationIdentity(
            memory_key=(f"patient:{normalized_patient_id}" if verified_patient else f"external:{normalized_patient_id}"),
            identity_mode=identity_mode,
            source="patient_id",
            persistence_enabled=True,
            resolved_patient_id=normalized_patient_id,
            verified_patient=verified_patient,
            browser_patient_key=browser_patient_key,
        )

    if browser_patient_key:
        return ConversationIdentity(
            memory_key=f"browser:{browser_patient_key}",
            identity_mode="browser_patient_key",
            source="browser_patient_key",
            persistence_enabled=True,
            browser_patient_key=browser_patient_key,
        )

    return ConversationIdentity(
        memory_key=f"conversation:{conversation_id}",
        identity_mode="conversation_only",
        source="conversation_id",
        persistence_enabled=False,
    )


def _should_persist_text_excerpt(
    resolved_identity: ConversationIdentity,
    patient_identity: dict[str, object] | None,
) -> bool:
    if isinstance(patient_identity, dict) and "consent_to_store_excerpt" in patient_identity:
        return bool(patient_identity.get("consent_to_store_excerpt"))
    return resolved_identity.identity_mode == "registered_patient"


def _format_recent_history(history: list[ConversationTurn]) -> str:
    if not history:
        return "No recent turns."
    lines: list[str] = []
    for turn in history[-6:]:
        suffix = ""
        if turn.item_id:
            suffix = f" [item_id={turn.item_id}]"
        lines.append(f"- {turn.role}: {turn.text}{suffix}")
    return "\n".join(lines)


def _build_candidate_selection_prompt(
    text: str,
    analysis: AnalysisResult,
    candidates: list[dict[str, object]],
    history: list[ConversationTurn],
    history_summary: dict[str, object] | None,
    patient: PatientProfile | None,
    profile_overrides: dict[str, object] | None,
    trigger_id: str | None,
    knowledge_context: list[dict[str, object]] | None,
) -> str:
    prompt_parts = [
        "Valassz egyetlen phrase candidate_id-t a megadott helyi jeloltek kozul.",
        "Csak a felsorolt candidate_id-k egyiket valaszthatod.",
        "Ha a knowledge snippetek kozul valamelyik kulonosen relevans, adj vissza legfeljebb 2 kb_id-t is.",
        "Ha van ertelmes alternativa, keruld a kozeli elozo asszisztens item_id megismetleset.",
        "A valaszod szigoruan JSON legyen ebben a formatumban: {\"candidate_id\":\"...\",\"kb_ids\":[\"...\"],\"reason\":\"...\"}.",
        f"Felhasznaloi uzenet: {text}",
        f"Intent: {analysis.intent}",
        f"Risk flags: {', '.join(sorted(analysis.risk_flags)) or 'none'}",
        f"Tags: {', '.join(sorted(analysis.tags)) or 'none'}",
        f"Matched trigger: {trigger_id or 'none'}",
        f"Conversation summary: {(history_summary or {}).get('summary_text', 'No recent conversation summary.')}",
        "Structured conversation state:",
        json.dumps((history_summary or {}).get("active_summary", {}), ensure_ascii=True),
        "Recent history:",
        _format_recent_history(history),
        "Candidates:",
    ]
    if patient is not None and patient.history_policy.allow_history_context and patient.history_summary:
        prompt_parts.append(f"Patient history summary: {patient.history_summary}")
    elif isinstance(profile_overrides, dict) and profile_overrides.get("history_summary"):
        prompt_parts.append(f"Patient history summary: {profile_overrides['history_summary']}")
    if knowledge_context:
        prompt_parts.append("Knowledge snippets:")
        for snippet in knowledge_context:
            prompt_parts.append(
                json.dumps(
                    {
                        "kb_id": snippet["id"],
                        "text": snippet["text"],
                        "topics": snippet.get("topics", []),
                        "categories": snippet.get("categories", []),
                    },
                    ensure_ascii=True,
                )
            )

    for candidate in candidates:
        prompt_parts.append(
            json.dumps(
                {
                    "candidate_id": candidate["item_id"],
                    "category": candidate["category"],
                    "text": candidate["text"],
                    "tags": candidate.get("tags", []),
                    "tone": candidate.get("tone"),
                    "profile_alignment": candidate.get("profile_alignment", {}),
                },
                ensure_ascii=True,
            )
        )
    return "\n".join(prompt_parts)


def _parse_candidate_selection_response(
    response_text: str,
    allowed_candidate_ids: set[str],
    allowed_kb_ids: set[str],
) -> tuple[str | None, str | None, list[str]]:
    normalized = response_text.strip()
    if normalized.startswith("```"):
        normalized = normalized.strip("`")
        if normalized.startswith("json"):
            normalized = normalized[4:].strip()

    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        candidate_id = payload.get("candidate_id")
        reason = payload.get("reason")
        kb_ids = payload.get("kb_ids", [])
        selected_kb_ids = []
        if isinstance(kb_ids, (list, tuple)):
            selected_kb_ids = [
                str(item) for item in kb_ids if isinstance(item, (str, int, float)) and str(item) in allowed_kb_ids
            ]
        if isinstance(candidate_id, str) and candidate_id in allowed_candidate_ids:
            return candidate_id, str(reason) if reason is not None else None, selected_kb_ids

    for candidate_id in allowed_candidate_ids:
        if candidate_id in normalized:
            return candidate_id, None, []
    return None, None, []


def _resolve_knowledge_audiences(
    patient: PatientProfile | None,
    profile_overrides: dict[str, object] | None,
) -> set[str]:
    audiences: set[str] = set()
    if patient is not None:
        inferred_age_group = _infer_age_group(patient)
        if inferred_age_group:
            audiences.add(inferred_age_group)
        audiences.update(patient.communication_profile.personas)
        if patient.communication_profile.literacy_level:
            audiences.add(patient.communication_profile.literacy_level)
        if patient.communication_profile.preferred_register:
            audiences.add(patient.communication_profile.preferred_register)
    if isinstance(profile_overrides, dict):
        for key in ("age_group", "literacy_level", "preferred_register"):
            value = profile_overrides.get(key)
            if value:
                audiences.add(str(value))
        personas = profile_overrides.get("personas")
        if isinstance(personas, (list, tuple, set)):
            audiences.update(str(item) for item in personas if item)
    return audiences


def _select_local_candidate(
    candidates: list[dict[str, object]],
    history: list[ConversationTurn],
) -> tuple[dict[str, object], str]:
    recent_assistant_ids = [turn.item_id for turn in history if turn.role == "assistant" and turn.item_id]
    for candidate in candidates:
        if candidate["item_id"] not in recent_assistant_ids:
            return candidate, "local_recent_rotation"
    return candidates[0], "local_top_ranked"


def _should_enable_phrase_rerank(
    llm_client: LLMAdapter | None,
    route_decision: dict[str, object] | None,
    candidates: list[dict[str, object]],
    online_stage_status: dict[str, object] | None,
    risk_flags: set[str],
) -> bool:
    return bool(
        llm_client is not None
        and route_decision is not None
        and route_decision.get("selected_mode") == "online"
        and route_decision.get("selected_model")
        and len(candidates) > 1
        and not risk_flags.intersection({"crisis"})
        and (online_stage_status or {}).get("status") not in {"cooldown", "missing_auth", "disabled"}
    )


def _cooldown_seconds_for_error(exc: Exception) -> int:
    if isinstance(exc, LLMServiceError):
        if exc.error_type == "auth_error":
            return 300
        if exc.error_type == "rate_limited":
            return 120
        if exc.error_type in {"server_error", "network_error", "timeout"}:
            return 45
    return 30


def _error_details(exc: Exception) -> dict[str, object]:
    if isinstance(exc, LLMServiceError):
        details: dict[str, object] = {
            "error_type": exc.error_type,
            "message": str(exc),
            "retryable": exc.retryable,
        }
        if exc.status_code is not None:
            details["http_status"] = exc.status_code
        return details
    return {
        "error_type": type(exc).__name__,
        "message": str(exc),
        "retryable": False,
    }


def _infer_age_group(patient: PatientProfile) -> str | None:
    if patient.communication_profile.age_group:
        return patient.communication_profile.age_group

    age_group = patient.demographics.get("age_group")
    if age_group:
        return str(age_group)

    age_value = patient.demographics.get("age")
    if age_value is None:
        return None

    try:
        age = int(str(age_value))
    except ValueError:
        return None

    if age < 13:
        return "child"
    if age < 18:
        return "teen"
    if age >= 65:
        return "senior"
    return "adult"


def _build_selection_request(analysis: AnalysisResult, patient: PatientProfile | None = None) -> SelectionRequest:
    request = SelectionRequest(tags=analysis.tags, risk_flags=analysis.risk_flags)
    if patient is None:
        return request

    if not patient.communication_profile.consent_granted and patient.communication_profile.source == "inferred":
        return request

    inferred_age_group = _infer_age_group(patient)
    if inferred_age_group:
        request.age_groups.add(inferred_age_group)
    if patient.communication_profile.literacy_level:
        request.literacy_level = patient.communication_profile.literacy_level
    if patient.communication_profile.preferred_register:
        request.preferred_register = patient.communication_profile.preferred_register
    request.personas.update(patient.communication_profile.personas)
    request.response_preferences.update(patient.communication_profile.preferences)
    return request


def _apply_profile_overrides(request: SelectionRequest, profile_overrides: dict[str, object] | None) -> SelectionRequest:
    if not isinstance(profile_overrides, dict):
        return request

    age_group = profile_overrides.get("age_group")
    if age_group:
        request.age_groups.add(str(age_group))

    literacy_level = profile_overrides.get("literacy_level")
    if literacy_level:
        request.literacy_level = str(literacy_level)

    preferred_register = profile_overrides.get("preferred_register")
    if preferred_register:
        request.preferred_register = str(preferred_register)

    personas = profile_overrides.get("personas")
    if isinstance(personas, (list, tuple, set)):
        request.personas.update(str(item) for item in personas if item)

    preferences = profile_overrides.get("preferences")
    if isinstance(preferences, dict):
        request.response_preferences.update(preferences)

    content_statuses = profile_overrides.get("content_statuses")
    if isinstance(content_statuses, (list, tuple, set)):
        request.allowed_content_statuses = {str(item) for item in content_statuses if item}

    content_channel = profile_overrides.get("content_channel")
    if content_channel:
        request.content_channel = str(content_channel)

    return request


def _apply_runtime_content_defaults(request: SelectionRequest, runtime_settings: object) -> SelectionRequest:
    default_statuses = getattr(runtime_settings, "content_statuses_default", None) or ["appr"]
    request.allowed_content_statuses = {str(item) for item in default_statuses if item}
    content_channel_default = getattr(runtime_settings, "content_channel_default", None)
    if content_channel_default:
        request.content_channel = str(content_channel_default)
    return request


class RuntimeService:
    def __init__(
        self,
        project_root: Path,
        lang: str,
        handoff_client: CrisisHandoffClient | None = None,
        profile_registry: ProfileRegistry | None = None,
        llm_client: LLMAdapter | None = None,
        conversation_memory: ConversationMemoryStore | None = None,
        audit_logger: AuditLogger | None = None,
    ) -> None:
        self.project_root = project_root
        self.lang = lang
        config_dir = project_root / "config"
        self.runtime_settings = load_runtime_settings(config_dir)
        configured_bundle_path = os.getenv("RUNTIME_BUNDLE_PATH") or self.runtime_settings.published_bundle_path
        self.bundle = load_bundle(project_root, lang, published_bundle_path=configured_bundle_path)
        if self.bundle.knowledge_snippets:
            self.knowledge_snippets = load_knowledge_snippets_from_payload(self.bundle.knowledge_snippets)
        else:
            self.knowledge_snippets = load_knowledge_snippets(project_root, lang)
        self.handoff_client = handoff_client
        self.profile_registry = profile_registry or _load_default_profile_registry(project_root)
        self.token_limits = load_token_limits(config_dir)
        self.cache_settings = load_cache_settings(config_dir)
        self.model_routing_settings = load_model_routing_settings(config_dir)
        self.latency_masking_settings = load_latency_masking_settings(config_dir)
        self.llm_endpoint = load_llm_endpoint(config_dir)
        self.llm_client = llm_client
        persistence_path = None
        if os.getenv("PYTEST_CURRENT_TEST") is None:
            persistence_path = project_root / "data" / "runtime_state" / "conversation_memory.json"
        self.conversation_memory = conversation_memory or ConversationMemoryStore(
            ttl_seconds=self.cache_settings.session_ttl_seconds,
            persistence_path=persistence_path,
        )
        self.audit_logger = audit_logger
        if self.audit_logger is None and os.getenv("PYTEST_CURRENT_TEST") is None:
            self.audit_logger = AuditLogger(project_root / "data" / "runtime_state" / "audit")
        if self.llm_client is None and (
            self.runtime_settings.generative_fallback_enabled
            or any(stage.online_model for stage in self.model_routing_settings.stages)
        ):
            self.llm_client = build_llm_adapter(config_dir)
        self.online_stage_status = self._build_initial_online_stage_status()

    def _build_initial_online_stage_status(self) -> dict[str, dict[str, object]]:
        stages = ("phrase_selection", "generative_fallback")
        status_by_stage: dict[str, dict[str, object]] = {}
        for stage_name in stages:
            stage = next(
                (item for item in self.model_routing_settings.stages if item.stage == stage_name),
                None,
            )
            if stage is None or stage.online_model is None:
                status_by_stage[stage_name] = {
                    "status": "disabled",
                    "configured": False,
                    "selected_model": None,
                    "provider": self.llm_endpoint.provider,
                    "auth_configured": False,
                    "cooldown_until": 0.0,
                }
                continue

            base_status = {
                "status": "configured",
                "configured": True,
                "selected_model": _resolve_generation_model(stage.online_model, self.llm_endpoint),
                "provider": self.llm_endpoint.provider,
                "auth_configured": bool(
                    not self.llm_endpoint.auth_env_var or os.getenv(self.llm_endpoint.auth_env_var)
                ),
                "cooldown_until": 0.0,
                "last_error_type": None,
                "last_http_status": None,
                "last_error_message": None,
                "last_success_at": None,
            }
            if self.llm_client is None:
                base_status["status"] = "disabled"
            elif hasattr(self.llm_client, "is_ready"):
                readiness = getattr(self.llm_client, "is_ready")()
                base_status["status"] = str(readiness.get("status", base_status["status"]))
                base_status["auth_configured"] = bool(
                    readiness.get("auth_configured", base_status["auth_configured"])
                )
            status_by_stage[stage_name] = base_status
        return status_by_stage

    def _get_online_stage_status(self, stage_name: str) -> dict[str, object]:
        status = dict(self.online_stage_status.get(stage_name, {}))
        cooldown_until = float(status.get("cooldown_until") or 0.0)
        if cooldown_until and time.time() < cooldown_until:
            status["status"] = "cooldown"
        return status

    def get_online_stage_statuses(self) -> dict[str, dict[str, object]]:
        return {
            stage_name: self._get_online_stage_status(stage_name)
            for stage_name in self.online_stage_status
        }

    def _mark_online_stage_success(self, stage_name: str, response_model: str | None = None) -> None:
        stage_status = self.online_stage_status.setdefault(stage_name, {})
        stage_status["status"] = "ready"
        stage_status["cooldown_until"] = 0.0
        stage_status["last_error_type"] = None
        stage_status["last_http_status"] = None
        stage_status["last_error_message"] = None
        stage_status["last_success_at"] = time.time()
        if response_model:
            stage_status["selected_model"] = response_model

    def _mark_online_stage_failure(self, stage_name: str, exc: Exception) -> dict[str, object]:
        stage_status = self.online_stage_status.setdefault(stage_name, {})
        details = _error_details(exc)
        cooldown_seconds = _cooldown_seconds_for_error(exc)
        stage_status["status"] = "cooldown"
        stage_status["cooldown_until"] = time.time() + cooldown_seconds
        stage_status["last_error_type"] = details["error_type"]
        stage_status["last_http_status"] = details.get("http_status")
        stage_status["last_error_message"] = details["message"]
        return details

    def get_conversation_history(self, conversation_id: str) -> list[dict[str, object]]:
        return self.conversation_memory.get_recent_turns_payload(conversation_id)

    def get_conversation_summary(self, conversation_id: str) -> dict[str, object]:
        return self.conversation_memory.get_summary(conversation_id)

    def get_conversation_identity(self, conversation_id: str) -> dict[str, object]:
        return self.conversation_memory.get_identity(conversation_id)

    def process_text(
        self,
        text: str,
        conversation_id: str = "local-dev",
        patient_id: str | None = None,
        patient_identity: dict[str, object] | None = None,
        active_conditions: Iterable[str] | None = None,
        prefer_online: bool = False,
        latency_context: str | None = None,
        latency_elapsed_ms: int = 0,
        latency_channel: str = "chat",
        profile_overrides: dict[str, object] | None = None,
    ) -> RuntimeResult:
        analysis = analyze_text(text)
        active_conditions_set = set(active_conditions or [])
        resolved_identity = _resolve_patient_identity(
            conversation_id,
            patient_id,
            patient_identity,
            self.profile_registry,
        )
        patient = None
        resolved_patient_id = resolved_identity.resolved_patient_id
        if resolved_patient_id and self.profile_registry is not None:
            patient = self.profile_registry.get_patient(resolved_patient_id)

        route_decisions = {
            "intent_and_risk": _route_to_dict(
                choose_stage_route(
                    self.model_routing_settings,
                    stage="intent_and_risk",
                    active_conditions=active_conditions_set,
                    prefer_online=prefer_online,
                )
            ),
            "phrase_selection": _route_to_dict(
                choose_stage_route(
                    self.model_routing_settings,
                    stage="phrase_selection",
                    active_conditions=active_conditions_set,
                    prefer_online=prefer_online,
                )
            ),
            "tts": _route_to_dict(
                choose_stage_route(
                    self.model_routing_settings,
                    stage="tts",
                    active_conditions=active_conditions_set,
                    prefer_online=prefer_online,
                )
            ),
        }

        request = _apply_profile_overrides(
            _apply_runtime_content_defaults(_build_selection_request(analysis, patient), self.runtime_settings),
            profile_overrides,
        )
        memory_key = resolved_identity.memory_key
        persist_text_excerpt = _should_persist_text_excerpt(resolved_identity, patient_identity)
        history = self.conversation_memory.get_recent_turns(memory_key)
        history_summary = self.conversation_memory.get_summary(memory_key)
        knowledge_audiences = _resolve_knowledge_audiences(patient, profile_overrides)
        trigger_match = match_trigger(self.bundle, text, analysis, request)
        matched_trigger_id = None
        generation_request = None
        hybrid_selection = None
        phrase_selection_online_status = self._get_online_stage_status("phrase_selection")
        knowledge_context: list[dict[str, object]] = []
        if trigger_match is not None:
            matched_trigger_id = trigger_match.trigger["id"]
            request.candidate_ids = {
                candidate
                for candidate in trigger_match.trigger.get("cand", [])
                if not str(candidate).startswith("MISSING_CANDIDATE:")
            }
            request.forced_category = CATEGORY_NAME_BY_SHORT.get(trigger_match.trigger.get("cat"))

        try:
            candidates = list_phrase_candidates(self.bundle, request, limit=5)
            knowledge_context = retrieve_knowledge_snippets(
                self.knowledge_snippets,
                intent=analysis.intent,
                tags=analysis.tags,
                categories={str(candidate["category"]) for candidate in candidates},
                audiences=knowledge_audiences,
                stage="phrase_selection",
                allowed_statuses=request.allowed_content_statuses,
                channel=request.content_channel,
            )
            selection, local_strategy = _select_local_candidate(candidates, history)
            hybrid_selection = {
                "status": "local_only",
                "strategy": local_strategy,
                "candidate_ids": [candidate["item_id"] for candidate in candidates],
                "selected_candidate_id": selection["item_id"],
                "knowledge_ids": [snippet["id"] for snippet in knowledge_context],
            }
            if _should_enable_phrase_rerank(
                self.llm_client,
                route_decisions.get("phrase_selection"),
                candidates,
                phrase_selection_online_status,
                analysis.risk_flags,
            ):
                selected_model = _resolve_generation_model(
                    str(route_decisions["phrase_selection"]["selected_model"]),
                    self.llm_endpoint,
                )
                selection_prompt = _build_candidate_selection_prompt(
                    text,
                    analysis,
                    candidates,
                    history,
                    history_summary,
                    patient,
                    profile_overrides,
                    matched_trigger_id,
                    knowledge_context,
                )
                hybrid_selection = {
                    "status": "pending",
                    "strategy": "online_candidate_rerank",
                    "candidate_ids": [candidate["item_id"] for candidate in candidates],
                    "selected_candidate_id": selection["item_id"],
                    "selected_model": selected_model,
                    "knowledge_ids": [snippet["id"] for snippet in knowledge_context],
                }
                try:
                    response = self.llm_client.generate(
                        GenerationRequest(
                            conversation_id=memory_key,
                            lang=self.lang,
                            prompt=selection_prompt,
                            system_prompt=self.llm_endpoint.system_prompt,
                            model=selected_model,
                            max_tokens=min(self.token_limits.chat_max_output_tokens, 90),
                        )
                    )
                    chosen_id, reason, selected_kb_ids = _parse_candidate_selection_response(
                        response.text,
                        {str(candidate["item_id"]) for candidate in candidates},
                        {str(snippet["id"]) for snippet in knowledge_context},
                    )
                    if chosen_id:
                        selection = next(
                            candidate for candidate in candidates if candidate["item_id"] == chosen_id
                        )
                        if selected_kb_ids:
                            selected_kb_id_set = set(selected_kb_ids)
                            prioritized_knowledge = [
                                snippet for snippet in knowledge_context if str(snippet["id"]) in selected_kb_id_set
                            ]
                            prioritized_knowledge.extend(
                                snippet
                                for snippet in knowledge_context
                                if str(snippet["id"]) not in selected_kb_id_set
                            )
                            knowledge_context = prioritized_knowledge
                        hybrid_selection["status"] = "completed"
                        hybrid_selection["selected_candidate_id"] = chosen_id
                        hybrid_selection["selected_kb_ids"] = selected_kb_ids
                        hybrid_selection["response_model"] = response.model
                        hybrid_selection["finish_reason"] = response.finish_reason
                        hybrid_selection["online_stage_status"] = self._get_online_stage_status(
                            "phrase_selection"
                        )
                        self._mark_online_stage_success("phrase_selection", response.model)
                        if reason:
                            hybrid_selection["reason"] = reason
                    else:
                        hybrid_selection["status"] = "fallback_local"
                        hybrid_selection["message"] = "Model response did not select a valid candidate."
                except Exception as exc:
                    error_details = self._mark_online_stage_failure("phrase_selection", exc)
                    hybrid_selection["status"] = "fallback_local"
                    hybrid_selection.update(error_details)
                    hybrid_selection["online_stage_status"] = self._get_online_stage_status(
                        "phrase_selection"
                    )
            elif phrase_selection_online_status.get("status") == "cooldown":
                hybrid_selection["status"] = "fallback_local"
                hybrid_selection["strategy"] = "online_candidate_rerank"
                hybrid_selection["message"] = "Online rerank is temporarily cooling down after a prior failure."
                hybrid_selection["online_stage_status"] = phrase_selection_online_status
        except LookupError:
            if trigger_match is not None:
                fallback_request = _apply_profile_overrides(
                    _apply_runtime_content_defaults(_build_selection_request(analysis, patient), self.runtime_settings),
                    profile_overrides,
                )
                fallback_request.forced_category = fallback_category_name(trigger_match.trigger)
                try:
                    fallback_candidates = list_phrase_candidates(self.bundle, fallback_request, limit=5)
                    knowledge_context = retrieve_knowledge_snippets(
                        self.knowledge_snippets,
                        intent=analysis.intent,
                        tags=analysis.tags,
                        categories={str(candidate["category"]) for candidate in fallback_candidates},
                        audiences=knowledge_audiences,
                        stage="phrase_selection",
                        allowed_statuses=request.allowed_content_statuses,
                        channel=request.content_channel,
                    )
                    selection, local_strategy = _select_local_candidate(fallback_candidates, history)
                    hybrid_selection = {
                        "status": "local_only",
                        "strategy": local_strategy,
                        "candidate_ids": [candidate["item_id"] for candidate in fallback_candidates],
                        "selected_candidate_id": selection["item_id"],
                        "knowledge_ids": [snippet["id"] for snippet in knowledge_context],
                    }
                except LookupError:
                    if not self.runtime_settings.generative_fallback_enabled:
                        raise
                    selection = None
            elif not self.runtime_settings.generative_fallback_enabled:
                raise
            else:
                selection = None

            if selection is None:
                if not knowledge_context:
                    forced_categories = {request.forced_category} if request.forced_category else set()
                    knowledge_context = retrieve_knowledge_snippets(
                        self.knowledge_snippets,
                        intent=analysis.intent,
                        tags=analysis.tags,
                        categories=forced_categories,
                        audiences=knowledge_audiences,
                        stage="generative_fallback",
                        allowed_statuses=request.allowed_content_statuses,
                        channel=request.content_channel,
                    )
                fallback_route = choose_stage_route(
                    self.model_routing_settings,
                    stage="generative_fallback",
                    active_conditions=active_conditions_set.union({"no_phrase_candidate"}),
                    prefer_online=prefer_online,
                )
                route_decisions["generative_fallback"] = _route_to_dict(fallback_route)
                generation_request = {
                    "reason": "no_phrase_candidate",
                    "status": "pending",
                    "gist": analysis.gist,
                    "intent": analysis.intent,
                    "risk_flags": sorted(analysis.risk_flags),
                    "selected_mode": fallback_route.selected_mode,
                    "selected_model": fallback_route.selected_model,
                    "max_tokens": self.token_limits.generative_fallback_max_tokens,
                }
                generated_text = ""
                generative_online_status = self._get_online_stage_status("generative_fallback")
                if self.llm_client is None:
                    generation_request["status"] = "unavailable"
                elif generative_online_status.get("status") == "cooldown":
                    generation_request["status"] = "cooldown"
                    generation_request["message"] = "Generative fallback is temporarily cooling down after a prior failure."
                    generation_request["online_stage_status"] = generative_online_status
                else:
                    try:
                        response = self.llm_client.generate(
                            GenerationRequest(
                                conversation_id=memory_key,
                                lang=self.lang,
                                prompt=_build_generation_prompt(
                                    text,
                                    analysis,
                                    patient,
                                    knowledge_context,
                                    history_summary,
                                    profile_overrides,
                                ),
                                system_prompt=self.llm_endpoint.system_prompt,
                                model=_resolve_generation_model(
                                    fallback_route.selected_model,
                                    self.llm_endpoint,
                                ),
                                max_tokens=self.token_limits.generative_fallback_max_tokens,
                            )
                        )
                        generated_text = response.text
                        generation_request["status"] = "completed"
                        generation_request["response_model"] = response.model
                        generation_request["finish_reason"] = response.finish_reason
                        self._mark_online_stage_success("generative_fallback", response.model)
                        generation_request["online_stage_status"] = self._get_online_stage_status(
                            "generative_fallback"
                        )
                    except Exception as exc:
                        error_details = self._mark_online_stage_failure("generative_fallback", exc)
                        generation_request["status"] = "failed"
                        generation_request.update(error_details)
                        generation_request["online_stage_status"] = self._get_online_stage_status(
                            "generative_fallback"
                        )
                selection = {
                    "category": "generative_fallback",
                    "item_id": None,
                    "text": generated_text,
                    "tags": sorted(analysis.tags),
                    "delivery_preferences": {},
                    "profile_alignment": {},
                }
                hybrid_selection = {
                    "status": "generative_fallback",
                    "strategy": "no_phrase_candidate",
                    "candidate_ids": [],
                    "selected_candidate_id": None,
                }
        handoff_triggered = False
        patient_context = None
        contact_plan = None
        latency_preamble = ""

        if latency_context:
            latency_preamble = build_latency_preamble(
                self.latency_masking_settings,
                context=latency_context,
                elapsed_ms=latency_elapsed_ms,
                channel=latency_channel,
            )

        if patient is not None and resolved_patient_id and self.profile_registry is not None:
            patient_context = summarize_patient_context(patient)
            severity = "critical" if requires_handoff(analysis.risk_flags) else "medium"
            contact_plan = build_after_hours_contact_plan(resolved_patient_id, self.profile_registry, severity)

        if requires_handoff(analysis.risk_flags) and self.handoff_client is not None:
            handoff_triggered = True
            self.handoff_client.send(
                HandoffRequest(
                    conversation_id=conversation_id,
                    lang=self.lang,
                    transcript=text,
                    gist=analysis.gist,
                    risk_flags=sorted(analysis.risk_flags),
                    selected_category=selection["category"],
                )
            )

        self.conversation_memory.append(
            memory_key,
            role="user",
            text=text,
            intent=analysis.intent,
            trigger_id=matched_trigger_id,
            tags=sorted(analysis.tags),
            risk_flags=sorted(analysis.risk_flags),
            persist_text=persist_text_excerpt,
            identity=resolved_identity,
        )
        response_strategy = None
        if isinstance(hybrid_selection, dict):
            response_strategy = str(
                hybrid_selection.get("strategy")
                or hybrid_selection.get("status")
                or ""
            ) or None
        elif generation_request is not None:
            response_strategy = "generative_fallback"
        self.conversation_memory.append(
            memory_key,
            role="assistant",
            text=str(selection.get("text", "")),
            item_id=selection.get("item_id"),
            category=selection.get("category"),
            intent=analysis.intent,
            trigger_id=matched_trigger_id,
            tags=sorted(analysis.tags),
            risk_flags=sorted(analysis.risk_flags),
            knowledge_ids=[str(item["id"]) for item in knowledge_context],
            response_strategy=response_strategy,
            persist_text=persist_text_excerpt,
            identity=resolved_identity,
        )
        conversation_summary = self.conversation_memory.get_summary(memory_key)
        if self.audit_logger is not None:
            self.audit_logger.append_event(
                stream="conversation",
                event_type="conversation_turn_processed",
                actor={
                    "role": "runtime_service",
                    "source": "runtime",
                },
                subject={
                    "conversation_id": conversation_id,
                    "memory_key": memory_key,
                    "resolved_patient_id": resolved_patient_id,
                },
                payload={
                    "user_text": text,
                    "assistant_text": str(selection.get("text", "")),
                    "analysis": {
                        "intent": analysis.intent,
                        "tags": sorted(analysis.tags),
                        "risk_flags": sorted(analysis.risk_flags),
                    },
                    "selection": {
                        "category": selection.get("category"),
                        "item_id": selection.get("item_id"),
                        "content_meta": selection.get("content_meta"),
                    },
                    "matched_trigger_id": matched_trigger_id,
                    "knowledge_ids": [str(item["id"]) for item in knowledge_context],
                    "hybrid_selection": hybrid_selection,
                    "handoff_triggered": handoff_triggered,
                },
            )
            if handoff_triggered:
                self.audit_logger.append_event(
                    stream="conversation",
                    event_type="crisis_handoff_triggered",
                    actor={
                        "role": "runtime_service",
                        "source": "runtime",
                    },
                    subject={
                        "conversation_id": conversation_id,
                        "memory_key": memory_key,
                        "resolved_patient_id": resolved_patient_id,
                    },
                    payload={
                        "risk_flags": sorted(analysis.risk_flags),
                        "matched_trigger_id": matched_trigger_id,
                        "selected_category": selection.get("category"),
                    },
                )

        return RuntimeResult(
            conversation_id=conversation_id,
            conversation_summary=conversation_summary,
            patient_identity=self.conversation_memory.get_identity(memory_key),
            analysis=analysis,
            selection=selection,
            handoff_triggered=handoff_triggered,
            patient_context=patient_context,
            contact_plan=contact_plan,
            matched_trigger_id=matched_trigger_id,
            route_decisions=route_decisions,
            generation_request=generation_request,
            latency_preamble=latency_preamble,
            hybrid_selection=hybrid_selection,
            knowledge_context=knowledge_context,
        )

    def process_audio(
        self,
        audio_path: Path,
        adapter: STTAdapter | None = None,
        conversation_id: str = "local-dev",
        patient_id: str | None = None,
        patient_identity: dict[str, object] | None = None,
        active_conditions: Iterable[str] | None = None,
        prefer_online: bool = False,
        latency_context: str | None = None,
        latency_elapsed_ms: int = 0,
        latency_channel: str = "chat",
        profile_overrides: dict[str, object] | None = None,
    ) -> RuntimeResult:
        active_conditions_set = set(active_conditions or [])
        route_decisions = {
            "stt": _route_to_dict(
                choose_stage_route(
                    self.model_routing_settings,
                    stage="stt",
                    active_conditions=active_conditions_set,
                    prefer_online=prefer_online,
                )
            )
        }
        if adapter is None:
            adapter = build_stt_adapter(
                self.project_root / "config",
                active_conditions=active_conditions_set,
                prefer_online=prefer_online,
            )
        transcript = adapter.transcribe(audio_path)
        result = self.process_text(
            transcript.text,
            conversation_id=conversation_id,
            patient_id=patient_id,
            patient_identity=patient_identity,
            active_conditions=active_conditions_set,
            prefer_online=prefer_online,
            latency_context=latency_context,
            latency_elapsed_ms=latency_elapsed_ms,
            latency_channel=latency_channel,
            profile_overrides=profile_overrides,
        )
        if result.route_decisions is None:
            result.route_decisions = route_decisions
        else:
            result.route_decisions = {**route_decisions, **result.route_decisions}
        return result

    def synthesize_response_audio(
        self,
        response_text: str,
        *,
        delivery_preferences: dict[str, object] | None = None,
        active_conditions: Iterable[str] | None = None,
        prefer_online: bool = False,
        adapter: TTSAdapter | None = None,
    ) -> dict[str, object]:
        normalized_text = response_text.strip()[: self.token_limits.tts_max_chars]
        if not normalized_text:
            raise ValueError("Cannot synthesize empty response text.")

        active_conditions_set = set(active_conditions or [])
        route_decision = choose_stage_route(
            self.model_routing_settings,
            stage="tts",
            active_conditions=active_conditions_set,
            prefer_online=prefer_online,
        )
        if adapter is None:
            adapter = build_tts_adapter(
                self.project_root / "config",
                active_conditions=active_conditions_set,
                prefer_online=prefer_online,
            )

        speed = "normal"
        if isinstance(delivery_preferences, dict) and delivery_preferences.get("tts_speed"):
            speed = str(delivery_preferences.get("tts_speed"))

        output_dir = self.project_root / "data" / "runtime_state" / "generated_audio"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"tts_{int(time.time() * 1000)}_{secrets.token_hex(4)}.wav"

        synthesized: SynthesizedAudio = adapter.synthesize(
            normalized_text,
            output_path,
            lang=self.lang,
            speed=speed,
        )
        if route_decision.selected_mode == "online" and synthesized.source != "http_tts":
            route_decision.selected_mode = "local"
            route_decision.selected_model = get_stage_definition(self.model_routing_settings, "tts").local_model
        return {
            "audio_path": str(synthesized.audio_path),
            "audio_file_name": synthesized.audio_path.name,
            "mime_type": synthesized.mime_type,
            "source": synthesized.source,
            "voice_name": synthesized.voice_name,
            "selected_mode": route_decision.selected_mode,
            "selected_model": route_decision.selected_model,
            "fallback_mode": route_decision.fallback_mode,
            "speed": speed,
            "text": normalized_text,
        }
