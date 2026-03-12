from __future__ import annotations

from dataclasses import dataclass, field

from assistant_runtime.profiles.registry import ProfileRegistry


@dataclass(slots=True)
class ContactRouteStep:
    recipient_type: str
    recipient_id: str
    channel_type: str
    target: str
    purpose: str
    priority: int


@dataclass(slots=True)
class ContactPlan:
    steps: list[ContactRouteStep] = field(default_factory=list)
    escalation_level: str = "low"
    rationale: str = ""


def build_after_hours_contact_plan(
    patient_id: str,
    registry: ProfileRegistry,
    severity: str,
    assistant_first: bool = True,
) -> ContactPlan:
    patient = registry.get_patient(patient_id)
    if patient is None:
        raise LookupError(f"Unknown patient: {patient_id}")

    plan = ContactPlan(escalation_level=severity, rationale="after-hours routing")
    clinician = registry.get_clinician(patient.assigned_clinician_id)
    assistants = [assistant for assistant in registry.assistants.values() if assistant.practice_id == patient.practice_id]

    if assistant_first:
        for assistant in assistants:
            for channel in sorted(assistant.contact_channels, key=lambda item: item.priority):
                if channel.after_hours:
                    plan.steps.append(
                        ContactRouteStep(
                            recipient_type="assistant",
                            recipient_id=assistant.assistant_id,
                            channel_type=channel.channel_type,
                            target=channel.target,
                            purpose=channel.purpose,
                            priority=channel.priority,
                        )
                    )

    if severity in {"high", "critical"} and clinician is not None and clinician.after_hours_opt_in:
        for channel in sorted(clinician.contact_channels, key=lambda item: item.priority):
            if channel.after_hours:
                plan.steps.append(
                    ContactRouteStep(
                        recipient_type="clinician",
                        recipient_id=clinician.clinician_id,
                        channel_type=channel.channel_type,
                        target=channel.target,
                        purpose=channel.purpose,
                        priority=channel.priority,
                    )
                )

    if severity == "critical":
        for channel in sorted(patient.emergency_contacts, key=lambda item: item.priority):
            if channel.after_hours:
                plan.steps.append(
                    ContactRouteStep(
                        recipient_type="emergency_contact",
                        recipient_id=patient.patient_id,
                        channel_type=channel.channel_type,
                        target=channel.target,
                        purpose=channel.purpose,
                        priority=channel.priority,
                    )
                )

    return plan
