from __future__ import annotations

import json
from pathlib import Path

from assistant_runtime.config.models import AccessGovernanceSettings
from assistant_runtime.config.models import CacheSettings
from assistant_runtime.config.models import CrisisHandoffConfig
from assistant_runtime.config.models import ContactChannelSettings
from assistant_runtime.config.models import LLMEndpointConfig
from assistant_runtime.config.models import LatencyMaskingContext
from assistant_runtime.config.models import LatencyMaskingSettings
from assistant_runtime.config.models import ModelRouteStage
from assistant_runtime.config.models import ModelRoutingSettings
from assistant_runtime.config.models import JsonSnapshotSourceSettings
from assistant_runtime.config.models import ProfilePolicySettings
from assistant_runtime.config.models import ProfileSourceSettings
from assistant_runtime.config.models import RoleAccessPolicy
from assistant_runtime.config.models import RoleChannelMatrixSettings
from assistant_runtime.config.models import RoleChannelRoute
from assistant_runtime.config.models import RuntimeSettings
from assistant_runtime.config.models import STTEndpointConfig
from assistant_runtime.config.models import TTSEndpointConfig
from assistant_runtime.config.models import TokenLimits


def _load_json(file_path: Path) -> dict:
    with file_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_runtime_settings(config_dir: Path) -> RuntimeSettings:
    payload = _load_json(config_dir / "runtime.json")
    return RuntimeSettings(**payload)


def load_token_limits(config_dir: Path) -> TokenLimits:
    payload = _load_json(config_dir / "token_limits.json")
    return TokenLimits(
        chat_max_input_tokens=payload["chat"]["max_input_tokens"],
        chat_max_output_tokens=payload["chat"]["max_output_tokens"],
        tts_max_chars=payload["tts"]["max_chars"],
        generative_fallback_max_tokens=payload["generative_fallback"]["max_tokens"],
    )


def load_cache_settings(config_dir: Path) -> CacheSettings:
    payload = _load_json(config_dir / "cache.json")
    return CacheSettings(
        variants_ttl_seconds=payload["variants_cache"]["ttl_seconds"],
        variants_max_entries=payload["variants_cache"]["max_entries"],
        session_ttl_seconds=payload["session_cache"]["ttl_seconds"],
    )


def load_crisis_handoff(config_dir: Path) -> CrisisHandoffConfig:
    payload = _load_json(config_dir / "endpoints.json")["crisis_handoff"]
    return CrisisHandoffConfig(**payload)


def load_stt_endpoint(config_dir: Path) -> STTEndpointConfig:
    payload = _load_json(config_dir / "endpoints.json")["stt"]
    return STTEndpointConfig(**payload)


def load_tts_endpoint(config_dir: Path) -> TTSEndpointConfig:
    payload = _load_json(config_dir / "endpoints.json").get("tts", {})
    return TTSEndpointConfig(
        provider=payload.get("provider", "openai_compatible"),
        url=payload.get("url", "mock"),
        timeout_ms=int(payload.get("timeout_ms", 15000)),
        auth_env_var=payload.get("auth_env_var", "TTS_API_TOKEN"),
        api_format=payload.get("api_format", "json_audio_base64"),
        voice=payload.get("voice"),
    )


def load_llm_endpoint(config_dir: Path) -> LLMEndpointConfig:
    payload = _load_json(config_dir / "endpoints.json")["llm"]
    return LLMEndpointConfig(
        provider=payload.get("provider", "openai_compatible"),
        url=payload["url"],
        timeout_ms=int(payload["timeout_ms"]),
        auth_env_var=payload["auth_env_var"],
        api_format=payload["api_format"],
        system_prompt=payload["system_prompt"],
        default_model=payload.get("default_model"),
        model_aliases=dict(payload.get("model_aliases", {})),
    )


def load_profile_policy_settings(config_dir: Path) -> ProfilePolicySettings:
    payload = _load_json(config_dir / "profile_policies.json")
    communication_profile = payload.get("communication_profile", {})
    return ProfilePolicySettings(
        active_languages=list(payload["active_languages"]),
        default_history_scope=payload["patient_context"]["default_history_scope"],
        allow_history_by_default=bool(payload["patient_context"]["allow_history_by_default"]),
        auto_prefill_demographics=bool(payload["patient_context"]["auto_prefill_demographics"]),
        assistant_first_after_hours=bool(payload["after_hours_routing"]["assistant_first"]),
        clinician_notify_on=list(payload["after_hours_routing"]["clinician_notify_on"]),
        store_communication_profile_without_consent=bool(
            communication_profile.get("store_without_consent", False)
        ),
        allow_inference_without_consent=bool(
            communication_profile.get("allow_inference_without_consent", False)
        ),
        allow_runtime_adaptation_without_consent=bool(
            communication_profile.get("allow_runtime_adaptation_without_consent", False)
        ),
    )


def load_contact_channel_settings(config_dir: Path) -> ContactChannelSettings:
    payload = _load_json(config_dir / "contact_channels.json")
    return ContactChannelSettings(
        patient_access_channels=list(payload["patient_access_channels"]),
        assistant_channels=list(payload["assistant_channels"]),
        clinician_channels=list(payload["clinician_channels"]),
        emergency_channels=list(payload["emergency_channels"]),
    )


def _load_role_route(payload: dict) -> RoleChannelRoute:
    return RoleChannelRoute(
        ingress=list(payload.get("ingress", [])),
        primary=list(payload.get("primary", [])),
        fallback=list(payload.get("fallback", [])),
        constraints=list(payload.get("constraints", [])),
    )


def load_role_channel_matrix(config_dir: Path) -> RoleChannelMatrixSettings:
    payload = _load_json(config_dir / "role_channel_matrix.json")
    return RoleChannelMatrixSettings(
        patient=_load_role_route(payload["patient"]),
        operator=_load_role_route(payload["operator"]),
        clinical_lead=_load_role_route(payload["clinical_lead"]),
        emergency=_load_role_route(payload["emergency"]),
    )


def load_model_routing_settings(config_dir: Path) -> ModelRoutingSettings:
    payload = _load_json(config_dir / "model_routing.json")
    stages = [
        ModelRouteStage(
            stage=item["stage"],
            primary_mode=item["primary_mode"],
            local_model=item.get("local_model"),
            online_model=item.get("online_model"),
            fallback_mode=item.get("fallback_mode"),
            trigger_conditions=list(item.get("trigger_conditions", [])),
        )
        for item in payload.get("stages", [])
    ]
    return ModelRoutingSettings(
        default_mode=payload["default_mode"],
        latency_budget_ms=int(payload["latency_budget_ms"]),
        stages=stages,
    )


def load_latency_masking_settings(config_dir: Path) -> LatencyMaskingSettings:
    payload = _load_json(config_dir / "latency_masking.json")
    contexts = {
        name: LatencyMaskingContext(
            max_delay_ms=int(item["max_delay_ms"]),
            fillers=list(item.get("fillers", [])),
            ssml_break_ms=int(item.get("ssml_break_ms", 0)),
        )
        for name, item in payload.get("contexts", {}).items()
    }
    return LatencyMaskingSettings(
        enabled=bool(payload.get("enabled", False)),
        locale=payload.get("locale", "hu"),
        contexts=contexts,
    )


def _load_access_policy(payload: dict) -> RoleAccessPolicy:
    return RoleAccessPolicy(
        allowed_channels=list(payload.get("allowed_channels", [])),
        required_auth=list(payload.get("required_auth", [])),
        audit_events=list(payload.get("audit_events", [])),
        escalation_targets=list(payload.get("escalation_targets", [])),
    )


def load_access_governance_settings(config_dir: Path) -> AccessGovernanceSettings:
    payload = _load_json(config_dir / "access_governance.json")
    return AccessGovernanceSettings(
        patient=_load_access_policy(payload["patient"]),
        operator=_load_access_policy(payload["operator"]),
        clinical_lead=_load_access_policy(payload["clinical_lead"]),
    )


def load_profile_source_settings(config_dir: Path) -> ProfileSourceSettings:
    payload = _load_json(config_dir / "profile_sources.json")
    snapshot = payload["json_snapshot"]
    return ProfileSourceSettings(
        provider=payload["provider"],
        export_registry_path=payload["export_registry_path"],
        require_assigned_clinician=bool(payload["mapping_policy"]["require_assigned_clinician"]),
        prefer_patient_language=bool(payload["mapping_policy"]["prefer_patient_language"]),
        history_requires_consent=bool(payload["mapping_policy"]["history_requires_consent"]),
        json_snapshot=JsonSnapshotSourceSettings(
            patients_path=snapshot["patients_path"],
            clinicians_path=snapshot["clinicians_path"],
            assistants_path=snapshot["assistants_path"],
            assignments_path=snapshot["assignments_path"],
            history_path=snapshot["history_path"],
            default_timezone=snapshot["default_timezone"],
        ),
    )
