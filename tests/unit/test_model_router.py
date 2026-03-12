from pathlib import Path

from assistant_runtime.config.loader import load_model_routing_settings
from assistant_runtime.model_router import choose_stage_route


def test_choose_stage_route_defaults_to_local_for_stt() -> None:
    settings = load_model_routing_settings(Path.cwd() / "config")
    decision = choose_stage_route(settings, "stt")

    assert decision.selected_mode == "local"
    assert decision.selected_model == "faster-whisper-small"


def test_choose_stage_route_uses_fallback_when_trigger_condition_matches() -> None:
    settings = load_model_routing_settings(Path.cwd() / "config")
    decision = choose_stage_route(settings, "stt", active_conditions={"cpu_overloaded"})

    assert decision.selected_mode == "online"
    assert "cpu_overloaded" in decision.trigger_reasons