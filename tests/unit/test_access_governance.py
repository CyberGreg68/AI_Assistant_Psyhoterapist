from pathlib import Path

from assistant_runtime.access_governance import required_audit_events
from assistant_runtime.access_governance import validate_channel_access
from assistant_runtime.config.loader import load_access_governance_settings


def test_validate_channel_access_accepts_clinical_console_for_clinical_lead() -> None:
    settings = load_access_governance_settings(Path.cwd() / "config")

    assert validate_channel_access(settings, "clinical_lead", "clinical_console") is True
    assert validate_channel_access(settings, "patient", "admin_console") is False


def test_required_audit_events_returns_role_specific_events() -> None:
    settings = load_access_governance_settings(Path.cwd() / "config")

    assert "config_changed" in required_audit_events(settings, "operator")