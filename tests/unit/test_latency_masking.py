from pathlib import Path

from assistant_runtime.config.loader import load_latency_masking_settings
from assistant_runtime.latency_masking import build_latency_preamble
from assistant_runtime.latency_masking import choose_latency_hint
from assistant_runtime.latency_masking import render_ssml_preamble


def test_choose_latency_hint_returns_short_filler_within_budget() -> None:
    settings = load_latency_masking_settings(Path.cwd() / "config")
    hint = choose_latency_hint(settings, "acknowledge_then_compute", elapsed_ms=300, sequence=1)

    assert hint is not None
    assert hint.context == "acknowledge_then_compute"


def test_choose_latency_hint_returns_none_after_budget() -> None:
    settings = load_latency_masking_settings(Path.cwd() / "config")
    hint = choose_latency_hint(settings, "network_delay_bridge", elapsed_ms=5000)

    assert hint is None


def test_render_ssml_preamble_includes_break() -> None:
    settings = load_latency_masking_settings(Path.cwd() / "config")
    hint = choose_latency_hint(settings, "acknowledge_then_compute", elapsed_ms=300)

    ssml = render_ssml_preamble(hint)

    assert "<speak" in ssml
    assert "<break time=" in ssml


def test_build_latency_preamble_returns_chat_text_for_chat_channel() -> None:
    settings = load_latency_masking_settings(Path.cwd() / "config")

    preamble = build_latency_preamble(
        settings,
        context="safety_check_pause",
        elapsed_ms=200,
        channel="chat",
    )

    assert preamble
    assert "<speak" not in preamble