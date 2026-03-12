import json
from pathlib import Path

from assistant_runtime.profiles.registry import load_profile_registry
from assistant_runtime.profiles.registry import summarize_patient_context


def test_load_profile_registry_and_patient_context(tmp_path: Path) -> None:
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
                        "demographics": {"first_name": "Anna", "age": "16"},
                        "communication_profile": {
                            "preferred_register": "youth",
                            "literacy_level": "medium",
                            "personas": ["student"],
                            "preferences": {"tts_speed": "normal"}
                        },
                        "history_policy": {
                            "allow_history_context": True,
                            "history_scope": "summary",
                            "auto_prefill_demographics": True
                        },
                        "history_summary": "Brief prior summary.",
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
                        "contact_channels": []
                    }
                ],
                "assistants": []
            }
        ),
        encoding="utf-8",
    )

    registry = load_profile_registry(registry_path)
    patient = registry.get_patient("p-1")
    assert patient is not None
    context = summarize_patient_context(patient)
    assert context["assigned_clinician_id"] == "c-1"
    assert context["history_summary"] == "Brief prior summary."
    assert context["communication_profile"]["preferred_register"] == "youth"
    assert context["communication_profile"]["personas"] == ["student"]
