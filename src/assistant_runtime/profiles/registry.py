from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from assistant_runtime.json_utils import load_json_document
from assistant_runtime.profiles.models import AssistantProfile
from assistant_runtime.profiles.models import ClinicianProfile
from assistant_runtime.profiles.models import CommunicationProfile
from assistant_runtime.profiles.models import ContactChannel
from assistant_runtime.profiles.models import PatientHistoryPolicy
from assistant_runtime.profiles.models import PatientProfile


def _channel_from_dict(payload: dict) -> ContactChannel:
    return ContactChannel(**payload)


def _communication_profile_from_dict(payload: dict) -> CommunicationProfile:
    source = payload.get("source", "explicit")
    return CommunicationProfile(
        age_group=payload.get("age_group"),
        literacy_level=payload.get("literacy_level"),
        preferred_register=payload.get("preferred_register"),
        personas=list(payload.get("personas", [])),
        preferences=dict(payload.get("preferences", {})),
        source=source,
        consent_granted=bool(payload.get("consent_granted", source == "explicit" and bool(payload))),
    )


def _patient_from_dict(payload: dict) -> PatientProfile:
    history_policy = PatientHistoryPolicy(**payload.get("history_policy", {}))
    emergency_contacts = [_channel_from_dict(item) for item in payload.get("emergency_contacts", [])]
    return PatientProfile(
        patient_id=payload["patient_id"],
        practice_id=payload["practice_id"],
        assigned_clinician_id=payload.get("assigned_clinician_id"),
        preferred_lang=payload.get("preferred_lang", "hu"),
        timezone=payload.get("timezone", "UTC"),
        demographics=dict(payload.get("demographics", {})),
        history_policy=history_policy,
        history_summary=payload.get("history_summary", ""),
        emergency_contacts=emergency_contacts,
        communication_profile=_communication_profile_from_dict(payload.get("communication_profile", {})),
    )


def _clinician_from_dict(payload: dict) -> ClinicianProfile:
    channels = [_channel_from_dict(item) for item in payload.get("contact_channels", [])]
    return ClinicianProfile(
        clinician_id=payload["clinician_id"],
        practice_id=payload["practice_id"],
        display_name=payload["display_name"],
        role=payload.get("role", "clinician"),
        specialties=list(payload.get("specialties", [])),
        after_hours_opt_in=bool(payload.get("after_hours_opt_in", False)),
        contact_channels=channels,
    )


def _assistant_from_dict(payload: dict) -> AssistantProfile:
    channels = [_channel_from_dict(item) for item in payload.get("contact_channels", [])]
    return AssistantProfile(
        assistant_id=payload["assistant_id"],
        practice_id=payload["practice_id"],
        display_name=payload["display_name"],
        coverage_windows=list(payload.get("coverage_windows", [])),
        contact_channels=channels,
    )


@dataclass(slots=True)
class ProfileRegistry:
    patients: dict[str, PatientProfile]
    clinicians: dict[str, ClinicianProfile]
    assistants: dict[str, AssistantProfile]

    def get_patient(self, patient_id: str) -> PatientProfile | None:
        return self.patients.get(patient_id)

    def get_clinician(self, clinician_id: str | None) -> ClinicianProfile | None:
        if clinician_id is None:
            return None
        return self.clinicians.get(clinician_id)


def load_profile_registry(file_path: Path) -> ProfileRegistry:
    payload = load_json_document(file_path)
    patients = {item["patient_id"]: _patient_from_dict(item) for item in payload.get("patients", [])}
    clinicians = {
        item["clinician_id"]: _clinician_from_dict(item) for item in payload.get("clinicians", [])
    }
    assistants = {
        item["assistant_id"]: _assistant_from_dict(item) for item in payload.get("assistants", [])
    }
    return ProfileRegistry(patients=patients, clinicians=clinicians, assistants=assistants)


def summarize_patient_context(patient: PatientProfile) -> dict[str, object]:
    return {
        "patient_id": patient.patient_id,
        "assigned_clinician_id": patient.assigned_clinician_id,
        "preferred_lang": patient.preferred_lang,
        "timezone": patient.timezone,
        "demographics": patient.demographics if patient.history_policy.auto_prefill_demographics else {},
        "history_scope": patient.history_policy.history_scope,
        "history_summary": patient.history_summary if patient.history_policy.allow_history_context else "",
        "communication_profile": {
            "age_group": patient.communication_profile.age_group,
            "literacy_level": patient.communication_profile.literacy_level,
            "preferred_register": patient.communication_profile.preferred_register,
            "personas": list(patient.communication_profile.personas),
            "preferences": dict(patient.communication_profile.preferences),
            "source": patient.communication_profile.source,
            "consent_granted": patient.communication_profile.consent_granted,
        },
    }
