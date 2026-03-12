from __future__ import annotations

from pathlib import Path

from assistant_runtime.config.loader import load_access_governance_settings
from assistant_runtime.config.loader import load_latency_masking_settings
from assistant_runtime.config.loader import load_model_routing_settings
from assistant_runtime.config.loader import load_role_channel_matrix


def build_operations_snapshot(config_dir: Path) -> dict[str, object]:
    role_matrix = load_role_channel_matrix(config_dir)
    access = load_access_governance_settings(config_dir)
    model_routing = load_model_routing_settings(config_dir)
    latency = load_latency_masking_settings(config_dir)

    return {
        "roles": {
            "patient": {
                "ingress": role_matrix.patient.ingress,
                "primary": role_matrix.patient.primary,
                "fallback": role_matrix.patient.fallback,
                "constraints": role_matrix.patient.constraints,
                "allowed_channels": access.patient.allowed_channels,
                "required_auth": access.patient.required_auth,
                "audit_events": access.patient.audit_events,
                "escalation_targets": access.patient.escalation_targets,
            },
            "operator": {
                "ingress": role_matrix.operator.ingress,
                "primary": role_matrix.operator.primary,
                "fallback": role_matrix.operator.fallback,
                "constraints": role_matrix.operator.constraints,
                "allowed_channels": access.operator.allowed_channels,
                "required_auth": access.operator.required_auth,
                "audit_events": access.operator.audit_events,
                "escalation_targets": access.operator.escalation_targets,
            },
            "clinical_lead": {
                "ingress": role_matrix.clinical_lead.ingress,
                "primary": role_matrix.clinical_lead.primary,
                "fallback": role_matrix.clinical_lead.fallback,
                "constraints": role_matrix.clinical_lead.constraints,
                "allowed_channels": access.clinical_lead.allowed_channels,
                "required_auth": access.clinical_lead.required_auth,
                "audit_events": access.clinical_lead.audit_events,
                "escalation_targets": access.clinical_lead.escalation_targets,
            },
        },
        "pipeline": {
            "default_mode": model_routing.default_mode,
            "latency_budget_ms": model_routing.latency_budget_ms,
            "stages": [
                {
                    "stage": stage.stage,
                    "primary_mode": stage.primary_mode,
                    "local_model": stage.local_model,
                    "online_model": stage.online_model,
                    "fallback_mode": stage.fallback_mode,
                    "trigger_conditions": stage.trigger_conditions,
                }
                for stage in model_routing.stages
            ],
        },
        "latency_masking": {
            "enabled": latency.enabled,
            "locale": latency.locale,
            "contexts": {
                name: {
                    "max_delay_ms": context.max_delay_ms,
                    "ssml_break_ms": context.ssml_break_ms,
                    "fillers": context.fillers,
                }
                for name, context in latency.contexts.items()
            },
        },
    }