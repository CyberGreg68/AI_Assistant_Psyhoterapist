from __future__ import annotations

from assistant_runtime.config.models import AccessGovernanceSettings
from assistant_runtime.config.models import RoleAccessPolicy


ROLE_MAP = {
    "patient": "patient",
    "operator": "operator",
    "clinical_lead": "clinical_lead",
}


def get_role_policy(settings: AccessGovernanceSettings, role: str) -> RoleAccessPolicy:
    if role not in ROLE_MAP:
        raise LookupError(f"Unknown access role: {role}")
    return getattr(settings, ROLE_MAP[role])


def validate_channel_access(settings: AccessGovernanceSettings, role: str, channel: str) -> bool:
    policy = get_role_policy(settings, role)
    return channel in policy.allowed_channels


def required_audit_events(settings: AccessGovernanceSettings, role: str) -> list[str]:
    return list(get_role_policy(settings, role).audit_events)