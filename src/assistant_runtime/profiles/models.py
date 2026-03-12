from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ContactChannel:
    channel_type: str
    target: str
    purpose: str
    priority: int
    after_hours: bool = True
    automated: bool = False


@dataclass(slots=True)
class PatientHistoryPolicy:
    allow_history_context: bool
    history_scope: str
    auto_prefill_demographics: bool


@dataclass(slots=True)
class CommunicationProfile:
    age_group: str | None = None
    literacy_level: str | None = None
    preferred_register: str | None = None
    personas: list[str] = field(default_factory=list)
    preferences: dict[str, object] = field(default_factory=dict)
    source: str = "explicit"
    consent_granted: bool = False


@dataclass(slots=True)
class PatientProfile:
    patient_id: str
    practice_id: str
    assigned_clinician_id: str | None
    preferred_lang: str
    timezone: str
    demographics: dict[str, str] = field(default_factory=dict)
    history_policy: PatientHistoryPolicy = field(
        default_factory=lambda: PatientHistoryPolicy(False, "none", True)
    )
    history_summary: str = ""
    emergency_contacts: list[ContactChannel] = field(default_factory=list)
    communication_profile: CommunicationProfile = field(default_factory=CommunicationProfile)


@dataclass(slots=True)
class ClinicianProfile:
    clinician_id: str
    practice_id: str
    display_name: str
    role: str
    specialties: list[str] = field(default_factory=list)
    after_hours_opt_in: bool = False
    contact_channels: list[ContactChannel] = field(default_factory=list)


@dataclass(slots=True)
class AssistantProfile:
    assistant_id: str
    practice_id: str
    display_name: str
    coverage_windows: list[str] = field(default_factory=list)
    contact_channels: list[ContactChannel] = field(default_factory=list)


@dataclass(slots=True)
class ProfilePolicy:
    active_languages: list[str]
    default_history_scope: str
    allow_history_by_default: bool
    assistant_first_after_hours: bool
    clinician_notify_on: list[str]
