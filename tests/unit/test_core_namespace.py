from pathlib import Path

from assistant_runtime.config.loader import load_latency_masking_settings
from assistant_runtime.config.loader import load_model_routing_settings
from assistant_runtime.core.latency_masking import build_latency_preamble
from assistant_runtime.core.model_router import choose_stage_route
from assistant_runtime.core.runtime_bundle import load_bundle
from assistant_runtime.core.selection_engine import SelectionRequest
from assistant_runtime.core.selection_engine import select_phrase


def test_core_namespace_exposes_shared_selection_logic() -> None:
    bundle = load_bundle(Path.cwd(), "hu")
    result = select_phrase(
        bundle,
        SelectionRequest(tags={"emp"}, allowed_content_statuses={"appr", "rev"}),
    )

    assert result["text"]


def test_core_namespace_exposes_routing_and_latency_helpers() -> None:
    routing = load_model_routing_settings(Path.cwd() / "config")
    latency = load_latency_masking_settings(Path.cwd() / "config")

    decision = choose_stage_route(routing, "stt")
    preamble = build_latency_preamble(latency, "acknowledge_then_compute", 200, "chat")

    assert decision.selected_mode
    assert preamble