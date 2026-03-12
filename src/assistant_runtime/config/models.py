from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class TokenLimits:
    chat_max_input_tokens: int
    chat_max_output_tokens: int
    tts_max_chars: int
    generative_fallback_max_tokens: int


@dataclass(slots=True)
class CacheSettings:
    variants_ttl_seconds: int
    variants_max_entries: int
    session_ttl_seconds: int


@dataclass(slots=True)
class CrisisHandoffConfig:
    url: str
    timeout_ms: int
    auth_env_var: str


@dataclass(slots=True)
class STTEndpointConfig:
    url: str
    timeout_ms: int
    auth_env_var: str
    language: str


@dataclass(slots=True)
class TTSEndpointConfig:
    provider: str
    url: str
    timeout_ms: int
    auth_env_var: str
    api_format: str
    voice: str | None = None


@dataclass(slots=True)
class LLMEndpointConfig:
    provider: str
    url: str
    timeout_ms: int
    auth_env_var: str
    api_format: str
    system_prompt: str
    default_model: str | None
    model_aliases: dict[str, str]


@dataclass(slots=True)
class RuntimeSettings:
    active_lang: str
    default_use_case: str
    generative_fallback_enabled: bool
    cache_profile: str
    handoff_on_crisis: bool
    stt_provider: str
    tts_provider: str
    published_bundle_path: str | None
    content_statuses_default: list[str]
    content_channel_default: str


@dataclass(slots=True)
class ProfilePolicySettings:
    active_languages: list[str]
    default_history_scope: str
    allow_history_by_default: bool
    auto_prefill_demographics: bool
    assistant_first_after_hours: bool
    clinician_notify_on: list[str]
    store_communication_profile_without_consent: bool
    allow_inference_without_consent: bool
    allow_runtime_adaptation_without_consent: bool


@dataclass(slots=True)
class ContactChannelSettings:
    patient_access_channels: list[str]
    assistant_channels: list[str]
    clinician_channels: list[str]
    emergency_channels: list[str]


@dataclass(slots=True)
class RoleChannelRoute:
    ingress: list[str]
    primary: list[str]
    fallback: list[str]
    constraints: list[str]


@dataclass(slots=True)
class RoleChannelMatrixSettings:
    patient: RoleChannelRoute
    operator: RoleChannelRoute
    clinical_lead: RoleChannelRoute
    emergency: RoleChannelRoute


@dataclass(slots=True)
class ModelRouteStage:
    stage: str
    primary_mode: str
    local_model: str | None
    online_model: str | None
    fallback_mode: str | None
    trigger_conditions: list[str]


@dataclass(slots=True)
class ModelRoutingSettings:
    default_mode: str
    latency_budget_ms: int
    stages: list[ModelRouteStage]


@dataclass(slots=True)
class LatencyMaskingContext:
    max_delay_ms: int
    fillers: list[str]
    ssml_break_ms: int


@dataclass(slots=True)
class LatencyMaskingSettings:
    enabled: bool
    locale: str
    contexts: dict[str, LatencyMaskingContext]


@dataclass(slots=True)
class RoleAccessPolicy:
    allowed_channels: list[str]
    required_auth: list[str]
    audit_events: list[str]
    escalation_targets: list[str]


@dataclass(slots=True)
class AccessGovernanceSettings:
    patient: RoleAccessPolicy
    operator: RoleAccessPolicy
    clinical_lead: RoleAccessPolicy


@dataclass(slots=True)
class JsonSnapshotSourceSettings:
    patients_path: str
    clinicians_path: str
    assistants_path: str
    assignments_path: str
    history_path: str
    default_timezone: str


@dataclass(slots=True)
class ProfileSourceSettings:
    provider: str
    export_registry_path: str
    require_assigned_clinician: bool
    prefer_patient_language: bool
    history_requires_consent: bool
    json_snapshot: JsonSnapshotSourceSettings
