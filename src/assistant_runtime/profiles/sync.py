from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from assistant_runtime.config.models import ProfilePolicySettings
from assistant_runtime.config.models import ProfileSourceSettings
from assistant_runtime.json_utils import load_json_document
from assistant_runtime.profiles.models import AssistantProfile
from assistant_runtime.profiles.models import ClinicianProfile
from assistant_runtime.profiles.models import CommunicationProfile
from assistant_runtime.profiles.models import ContactChannel
from assistant_runtime.profiles.models import PatientHistoryPolicy
from assistant_runtime.profiles.models import PatientProfile
from assistant_runtime.profiles.registry import ProfileRegistry


def _channel_from_dict(payload: dict) -> ContactChannel:
    return ContactChannel(**payload)


@dataclass(slots=True)
class SyncReport:
    provider: str
    patients_seen: int
    patients_loaded: int
    clinicians_loaded: int
    assistants_loaded: int
    skipped_patients: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _build_communication_profile(
    payload: dict,
    policy_settings: ProfilePolicySettings,
) -> CommunicationProfile:
    communication_payload = dict(payload.get("communication_profile", {}))
    if not communication_payload:
        return CommunicationProfile()

    source = communication_payload.get("source", "explicit")
    consent_granted = bool(
        communication_payload.get("consent_granted", source == "explicit")
    )
    if not consent_granted and not policy_settings.store_communication_profile_without_consent:
        return CommunicationProfile(source=source, consent_granted=False)
    if source == "inferred" and not (
        consent_granted or policy_settings.allow_inference_without_consent
    ):
        return CommunicationProfile(source=source, consent_granted=False)

    return CommunicationProfile(
        age_group=communication_payload.get("age_group"),
        literacy_level=communication_payload.get("literacy_level"),
        preferred_register=communication_payload.get("preferred_register"),
        personas=list(communication_payload.get("personas", [])),
        preferences=dict(communication_payload.get("preferences", {})),
        source=source,
        consent_granted=consent_granted,
    )


class JsonSnapshotProfileSource:
    def __init__(self, project_root: Path, settings: ProfileSourceSettings) -> None:
        self.project_root = project_root
        self.settings = settings

    def _load_records(self, relative_path: str) -> list[dict]:
        file_path = self.project_root / relative_path
        payload = load_json_document(file_path)
        if not isinstance(payload, list):
            raise ValueError(f"Expected a list payload in {file_path}")
        return payload

    def load_patients(self) -> list[dict]:
        return self._load_records(self.settings.json_snapshot.patients_path)

    def load_clinicians(self) -> list[dict]:
        return self._load_records(self.settings.json_snapshot.clinicians_path)

    def load_assistants(self) -> list[dict]:
        return self._load_records(self.settings.json_snapshot.assistants_path)

    def load_assignments(self) -> list[dict]:
        return self._load_records(self.settings.json_snapshot.assignments_path)

    def load_history(self) -> list[dict]:
        return self._load_records(self.settings.json_snapshot.history_path)


def _build_clinicians(records: list[dict]) -> dict[str, ClinicianProfile]:
    clinicians: dict[str, ClinicianProfile] = {}
    for payload in records:
        clinicians[payload["clinician_id"]] = ClinicianProfile(
            clinician_id=payload["clinician_id"],
            practice_id=payload["practice_id"],
            display_name=payload["display_name"],
            role=payload.get("role", "clinician"),
            specialties=list(payload.get("specialties", [])),
            after_hours_opt_in=bool(payload.get("after_hours_opt_in", False)),
            contact_channels=[_channel_from_dict(item) for item in payload.get("contact_channels", [])],
        )
    return clinicians


def _build_assistants(records: list[dict]) -> dict[str, AssistantProfile]:
    assistants: dict[str, AssistantProfile] = {}
    for payload in records:
        assistants[payload["assistant_id"]] = AssistantProfile(
            assistant_id=payload["assistant_id"],
            practice_id=payload["practice_id"],
            display_name=payload["display_name"],
            coverage_windows=list(payload.get("coverage_windows", [])),
            contact_channels=[_channel_from_dict(item) for item in payload.get("contact_channels", [])],
        )
    return assistants


def _index_assignments(records: list[dict]) -> dict[str, str]:
    return {
        payload["patient_id"]: payload["clinician_id"]
        for payload in records
        if payload.get("patient_id") and payload.get("clinician_id")
    }


def _index_history(records: list[dict]) -> dict[str, dict]:
    return {payload["patient_id"]: payload for payload in records if payload.get("patient_id")}


def _patient_language(payload: dict, settings: ProfileSourceSettings) -> str:
    if settings.prefer_patient_language and payload.get("preferred_lang"):
        return payload["preferred_lang"]
    return "hu"


def sync_profile_registry(
    project_root: Path,
    source_settings: ProfileSourceSettings,
    policy_settings: ProfilePolicySettings,
) -> tuple[ProfileRegistry, SyncReport]:
    if source_settings.provider != "json_snapshot":
        raise ValueError(f"Unsupported profile source provider: {source_settings.provider}")

    source = JsonSnapshotProfileSource(project_root, source_settings)
    clinician_records = source.load_clinicians()
    assistant_records = source.load_assistants()
    patient_records = source.load_patients()
    assignments = _index_assignments(source.load_assignments())
    history_index = _index_history(source.load_history())

    clinicians = _build_clinicians(clinician_records)
    assistants = _build_assistants(assistant_records)
    patients: dict[str, PatientProfile] = {}
    skipped_patients: list[str] = []
    warnings: list[str] = []

    for payload in patient_records:
        patient_id = payload["patient_id"]
        assigned_clinician_id = payload.get("assigned_clinician_id") or assignments.get(patient_id)
        if source_settings.require_assigned_clinician and not assigned_clinician_id:
            skipped_patients.append(patient_id)
            warnings.append(f"Skipped patient without clinician assignment: {patient_id}")
            continue

        history_payload = history_index.get(patient_id, {})
        allow_history_context = bool(
            history_payload.get("allow_history_context", policy_settings.allow_history_by_default)
        )
        if source_settings.history_requires_consent and not history_payload.get("consent_captured", False):
            allow_history_context = False

        patients[patient_id] = PatientProfile(
            patient_id=patient_id,
            practice_id=payload["practice_id"],
            assigned_clinician_id=assigned_clinician_id,
            preferred_lang=_patient_language(payload, source_settings),
            timezone=payload.get(
                "timezone",
                source_settings.json_snapshot.default_timezone,
            ),
            demographics=dict(payload.get("demographics", {})),
            history_policy=PatientHistoryPolicy(
                allow_history_context=allow_history_context,
                history_scope=history_payload.get(
                    "history_scope",
                    policy_settings.default_history_scope,
                ),
                auto_prefill_demographics=policy_settings.auto_prefill_demographics,
            ),
            history_summary=(
                history_payload.get("history_summary", "") if allow_history_context else ""
            ),
            emergency_contacts=[
                _channel_from_dict(item) for item in payload.get("emergency_contacts", [])
            ],
            communication_profile=_build_communication_profile(payload, policy_settings),
        )
        if assigned_clinician_id and assigned_clinician_id not in clinicians:
            warnings.append(
                f"Patient {patient_id} references unknown clinician {assigned_clinician_id}"
            )

    registry = ProfileRegistry(patients=patients, clinicians=clinicians, assistants=assistants)
    report = SyncReport(
        provider=source_settings.provider,
        patients_seen=len(patient_records),
        patients_loaded=len(patients),
        clinicians_loaded=len(clinicians),
        assistants_loaded=len(assistants),
        skipped_patients=skipped_patients,
        warnings=warnings,
    )
    return registry, report


def export_profile_registry(registry: ProfileRegistry, output_path: Path) -> None:
    payload = {
        "patients": [
            {
                "patient_id": patient.patient_id,
                "practice_id": patient.practice_id,
                "assigned_clinician_id": patient.assigned_clinician_id,
                "preferred_lang": patient.preferred_lang,
                "timezone": patient.timezone,
                "demographics": patient.demographics,
                "history_policy": {
                    "allow_history_context": patient.history_policy.allow_history_context,
                    "history_scope": patient.history_policy.history_scope,
                    "auto_prefill_demographics": patient.history_policy.auto_prefill_demographics,
                },
                "history_summary": patient.history_summary,
                "emergency_contacts": [
                    {
                        "channel_type": channel.channel_type,
                        "target": channel.target,
                        "purpose": channel.purpose,
                        "priority": channel.priority,
                        "after_hours": channel.after_hours,
                        "automated": channel.automated,
                    }
                    for channel in patient.emergency_contacts
                ],
                "communication_profile": {
                    "age_group": patient.communication_profile.age_group,
                    "literacy_level": patient.communication_profile.literacy_level,
                    "preferred_register": patient.communication_profile.preferred_register,
                    "personas": patient.communication_profile.personas,
                    "preferences": patient.communication_profile.preferences,
                    "source": patient.communication_profile.source,
                    "consent_granted": patient.communication_profile.consent_granted,
                },
            }
            for patient in registry.patients.values()
        ],
        "clinicians": [
            {
                "clinician_id": clinician.clinician_id,
                "practice_id": clinician.practice_id,
                "display_name": clinician.display_name,
                "role": clinician.role,
                "specialties": clinician.specialties,
                "after_hours_opt_in": clinician.after_hours_opt_in,
                "contact_channels": [
                    {
                        "channel_type": channel.channel_type,
                        "target": channel.target,
                        "purpose": channel.purpose,
                        "priority": channel.priority,
                        "after_hours": channel.after_hours,
                        "automated": channel.automated,
                    }
                    for channel in clinician.contact_channels
                ],
            }
            for clinician in registry.clinicians.values()
        ],
        "assistants": [
            {
                "assistant_id": assistant.assistant_id,
                "practice_id": assistant.practice_id,
                "display_name": assistant.display_name,
                "coverage_windows": assistant.coverage_windows,
                "contact_channels": [
                    {
                        "channel_type": channel.channel_type,
                        "target": channel.target,
                        "purpose": channel.purpose,
                        "priority": channel.priority,
                        "after_hours": channel.after_hours,
                        "automated": channel.automated,
                    }
                    for channel in assistant.contact_channels
                ],
            }
            for assistant in registry.assistants.values()
        ],
    }
    header = "/* Generated by scripts/sync_profile_registry.py. Do not edit manually. */\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(header + json.dumps(payload, indent=2), encoding="utf-8")