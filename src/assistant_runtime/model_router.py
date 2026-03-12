from __future__ import annotations

from dataclasses import dataclass

from assistant_runtime.config.models import ModelRouteStage
from assistant_runtime.config.models import ModelRoutingSettings


@dataclass(slots=True)
class StageRouteDecision:
    stage: str
    selected_mode: str
    selected_model: str | None
    fallback_mode: str | None
    trigger_reasons: list[str]


def get_stage_definition(settings: ModelRoutingSettings, stage: str) -> ModelRouteStage:
    for item in settings.stages:
        if item.stage == stage:
            return item
    raise LookupError(f"Unknown pipeline stage: {stage}")


def choose_stage_route(
    settings: ModelRoutingSettings,
    stage: str,
    active_conditions: set[str] | None = None,
    prefer_online: bool = False,
) -> StageRouteDecision:
    definition = get_stage_definition(settings, stage)
    active_conditions = active_conditions or set()
    matched_conditions = [
        condition for condition in definition.trigger_conditions if condition in active_conditions
    ]

    selected_mode = definition.primary_mode
    if prefer_online and definition.online_model:
        selected_mode = "online"
    elif matched_conditions and definition.fallback_mode:
        selected_mode = definition.fallback_mode

    if selected_mode == "online":
        selected_model = definition.online_model
    elif selected_mode == "local":
        selected_model = definition.local_model
    else:
        selected_model = definition.local_model or definition.online_model

    return StageRouteDecision(
        stage=stage,
        selected_mode=selected_mode,
        selected_model=selected_model,
        fallback_mode=definition.fallback_mode,
        trigger_reasons=matched_conditions,
    )
