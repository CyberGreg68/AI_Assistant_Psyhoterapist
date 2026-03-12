import json
from pathlib import Path

from assistant_runtime.profiles.registry import load_profile_registry
from assistant_runtime.routing.contact_router import build_after_hours_contact_plan


def test_contact_router_prefers_assistant_then_clinician(tmp_path: Path) -> None:
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
                        "history_policy": {
                                "allow_history_context": False,
                            "history_scope": "none",
                                "auto_prefill_demographics": True
                        },
                        "emergency_contacts": []
                    }
                ],
                "clinicians": [
                    {
                        "clinician_id": "c-1",
                        "practice_id": "practice-1",
                        "display_name": "Dr. Example",
                        "role": "psychologist",
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
                        "display_name": "On-call assistant",
                        "coverage_windows": ["after_hours"],
                        "contact_channels": [
                            {
                                "channel_type": "secure_chat",
                                "target": "assistant-chat",
                                "purpose": "first-line triage",
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
    plan = build_after_hours_contact_plan("p-1", registry, severity="high")
    assert plan.steps[0].recipient_type == "assistant"
    assert any(step.recipient_type == "clinician" for step in plan.steps)
