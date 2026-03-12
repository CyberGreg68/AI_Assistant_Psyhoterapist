import json
from pathlib import Path

from assistant_runtime.config.loader import load_profile_policy_settings
from assistant_runtime.config.loader import load_profile_source_settings
from assistant_runtime.profiles.sync import export_profile_registry
from assistant_runtime.profiles.sync import sync_profile_registry


def _write_json(path: Path, payload: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_sync_profile_registry_from_json_snapshots(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "profile_policies.json").write_text(
        json.dumps(
            {
                "active_languages": ["hu", "en", "de"],
                "patient_context": {
                    "default_history_scope": "summary",
                    "allow_history_by_default": False,
                    "auto_prefill_demographics": True
                },
                "communication_profile": {
                    "store_without_consent": False,
                    "allow_inference_without_consent": False,
                    "allow_runtime_adaptation_without_consent": False
                },
                "after_hours_routing": {
                    "assistant_first": True,
                    "clinician_notify_on": ["high", "critical"],
                    "max_assistant_wait_minutes": 10
                },
                "clinician_assignment": {
                    "mode": "explicit_assignment",
                    "fallback": "practice_pool"
                }
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "profile_sources.json").write_text(
        json.dumps(
            {
                "provider": "json_snapshot",
                "export_registry_path": "config/profile_registry.generated.jsonc",
                "json_snapshot": {
                    "patients_path": "data_sources/patients.snapshot.json",
                    "clinicians_path": "data_sources/clinicians.snapshot.json",
                    "assistants_path": "data_sources/assistants.snapshot.json",
                    "assignments_path": "data_sources/assignments.snapshot.json",
                    "history_path": "data_sources/patient_history.snapshot.json",
                    "default_timezone": "Europe/Budapest"
                },
                "mapping_policy": {
                    "require_assigned_clinician": True,
                    "prefer_patient_language": True,
                    "history_requires_consent": True
                }
            }
        ),
        encoding="utf-8",
    )

    _write_json(
        tmp_path / "data_sources" / "patients.snapshot.json",
        [
            {
                "patient_id": "p-1",
                "practice_id": "practice-1",
                "preferred_lang": "hu",
                "demographics": {"first_name": "Anna", "age": "68"},
                "communication_profile": {
                    "preferred_register": "plain",
                    "literacy_level": "low",
                    "personas": ["retiree"],
                    "preferences": {"prefer_text": True}
                }
            },
            {
                "patient_id": "p-2",
                "practice_id": "practice-1",
                "preferred_lang": "de"
            }
        ],
    )
    _write_json(
        tmp_path / "data_sources" / "clinicians.snapshot.json",
        [
            {
                "clinician_id": "c-1",
                "practice_id": "practice-1",
                "display_name": "Dr. Example",
                "after_hours_opt_in": True,
                "contact_channels": []
            }
        ],
    )
    _write_json(
        tmp_path / "data_sources" / "assistants.snapshot.json",
        [
            {
                "assistant_id": "a-1",
                "practice_id": "practice-1",
                "display_name": "Assistant Example",
                "coverage_windows": ["weekday_evenings"],
                "contact_channels": []
            }
        ],
    )
    _write_json(
        tmp_path / "data_sources" / "assignments.snapshot.json",
        [{"patient_id": "p-1", "clinician_id": "c-1"}],
    )
    _write_json(
        tmp_path / "data_sources" / "patient_history.snapshot.json",
        [
            {
                "patient_id": "p-1",
                "allow_history_context": True,
                "consent_captured": True,
                "history_scope": "summary",
                "history_summary": "Recent stabilization with mild anxiety."
            }
        ],
    )

    source_settings = load_profile_source_settings(config_dir)
    policy_settings = load_profile_policy_settings(config_dir)

    registry, report = sync_profile_registry(tmp_path, source_settings, policy_settings)

    assert report.patients_seen == 2
    assert report.patients_loaded == 1
    assert report.skipped_patients == ["p-2"]
    assert registry.get_patient("p-1") is not None
    assert registry.get_patient("p-1").history_summary == "Recent stabilization with mild anxiety."
    assert registry.get_patient("p-1").communication_profile.preferred_register == "plain"


def test_export_profile_registry_writes_jsonc_header(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "profile_policies.json").write_text(
        json.dumps(
            {
                "active_languages": ["hu"],
                "patient_context": {
                    "default_history_scope": "summary",
                    "allow_history_by_default": False,
                    "auto_prefill_demographics": True
                },
                "communication_profile": {
                    "store_without_consent": False,
                    "allow_inference_without_consent": False,
                    "allow_runtime_adaptation_without_consent": False
                },
                "after_hours_routing": {
                    "assistant_first": True,
                    "clinician_notify_on": ["critical"],
                    "max_assistant_wait_minutes": 10
                },
                "clinician_assignment": {
                    "mode": "explicit_assignment",
                    "fallback": "practice_pool"
                }
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "profile_sources.json").write_text(
        json.dumps(
            {
                "provider": "json_snapshot",
                "export_registry_path": "config/profile_registry.generated.jsonc",
                "json_snapshot": {
                    "patients_path": "data_sources/patients.snapshot.json",
                    "clinicians_path": "data_sources/clinicians.snapshot.json",
                    "assistants_path": "data_sources/assistants.snapshot.json",
                    "assignments_path": "data_sources/assignments.snapshot.json",
                    "history_path": "data_sources/patient_history.snapshot.json",
                    "default_timezone": "Europe/Budapest"
                },
                "mapping_policy": {
                    "require_assigned_clinician": False,
                    "prefer_patient_language": True,
                    "history_requires_consent": False
                }
            }
        ),
        encoding="utf-8",
    )

    _write_json(
        tmp_path / "data_sources" / "patients.snapshot.json",
        [{"patient_id": "p-1", "practice_id": "practice-1", "preferred_lang": "hu"}],
    )
    _write_json(tmp_path / "data_sources" / "clinicians.snapshot.json", [])
    _write_json(tmp_path / "data_sources" / "assistants.snapshot.json", [])
    _write_json(tmp_path / "data_sources" / "assignments.snapshot.json", [])
    _write_json(tmp_path / "data_sources" / "patient_history.snapshot.json", [])

    source_settings = load_profile_source_settings(config_dir)
    policy_settings = load_profile_policy_settings(config_dir)
    registry, _ = sync_profile_registry(tmp_path, source_settings, policy_settings)
    output_path = tmp_path / source_settings.export_registry_path

    export_profile_registry(registry, output_path)

    exported = output_path.read_text(encoding="utf-8")
    assert exported.startswith("/* Generated by scripts/sync_profile_registry.py.")
    assert '"patient_id": "p-1"' in exported
    assert '"communication_profile": {' in exported


def test_sync_profile_registry_drops_inferred_style_without_consent(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "profile_policies.json").write_text(
        json.dumps(
            {
                "active_languages": ["hu"],
                "patient_context": {
                    "default_history_scope": "summary",
                    "allow_history_by_default": False,
                    "auto_prefill_demographics": True
                },
                "communication_profile": {
                    "store_without_consent": False,
                    "allow_inference_without_consent": False,
                    "allow_runtime_adaptation_without_consent": False
                },
                "after_hours_routing": {
                    "assistant_first": True,
                    "clinician_notify_on": ["critical"],
                    "max_assistant_wait_minutes": 10
                },
                "clinician_assignment": {
                    "mode": "explicit_assignment",
                    "fallback": "practice_pool"
                }
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "profile_sources.json").write_text(
        json.dumps(
            {
                "provider": "json_snapshot",
                "export_registry_path": "config/profile_registry.generated.jsonc",
                "json_snapshot": {
                    "patients_path": "data_sources/patients.snapshot.json",
                    "clinicians_path": "data_sources/clinicians.snapshot.json",
                    "assistants_path": "data_sources/assistants.snapshot.json",
                    "assignments_path": "data_sources/assignments.snapshot.json",
                    "history_path": "data_sources/patient_history.snapshot.json",
                    "default_timezone": "Europe/Budapest"
                },
                "mapping_policy": {
                    "require_assigned_clinician": False,
                    "prefer_patient_language": True,
                    "history_requires_consent": False
                }
            }
        ),
        encoding="utf-8",
    )

    _write_json(
        tmp_path / "data_sources" / "patients.snapshot.json",
        [
            {
                "patient_id": "p-1",
                "practice_id": "practice-1",
                "preferred_lang": "hu",
                "communication_profile": {
                    "preferred_register": "plain",
                    "literacy_level": "low",
                    "source": "inferred",
                    "consent_granted": False
                }
            }
        ],
    )
    _write_json(tmp_path / "data_sources" / "clinicians.snapshot.json", [])
    _write_json(tmp_path / "data_sources" / "assistants.snapshot.json", [])
    _write_json(tmp_path / "data_sources" / "assignments.snapshot.json", [])
    _write_json(tmp_path / "data_sources" / "patient_history.snapshot.json", [])

    source_settings = load_profile_source_settings(config_dir)
    policy_settings = load_profile_policy_settings(config_dir)
    registry, _ = sync_profile_registry(tmp_path, source_settings, policy_settings)

    patient = registry.get_patient("p-1")
    assert patient is not None
    assert patient.communication_profile.preferred_register is None
    assert patient.communication_profile.consent_granted is False