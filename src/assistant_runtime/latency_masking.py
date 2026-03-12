from __future__ import annotations

from dataclasses import dataclass
from xml.sax.saxutils import escape

from assistant_runtime.config.models import LatencyMaskingSettings


@dataclass(slots=True)
class LatencyMaskingHint:
    context: str
    filler: str
    ssml_break_ms: int
    max_delay_ms: int


def choose_latency_hint(
    settings: LatencyMaskingSettings,
    context: str,
    elapsed_ms: int,
    sequence: int = 0,
) -> LatencyMaskingHint | None:
    if not settings.enabled:
        return None
    config = settings.contexts.get(context)
    if config is None or not config.fillers:
        return None
    if elapsed_ms > config.max_delay_ms:
        return None

    filler = config.fillers[sequence % len(config.fillers)]
    return LatencyMaskingHint(
        context=context,
        filler=filler,
        ssml_break_ms=config.ssml_break_ms,
        max_delay_ms=config.max_delay_ms,
    )


def render_chat_preamble(hint: LatencyMaskingHint | None) -> str:
    if hint is None:
        return ""
    return hint.filler


def render_ssml_preamble(hint: LatencyMaskingHint | None) -> str:
    if hint is None:
        return ""
    escaped_text = escape(hint.filler)
    return (
        "<speak version=\"1.0\" xml:lang=\"hu-HU\">"
        f"<p>{escaped_text}<break time=\"{hint.ssml_break_ms}ms\"/></p>"
        "</speak>"
    )


def build_latency_preamble(
    settings: LatencyMaskingSettings,
    context: str,
    elapsed_ms: int,
    channel: str,
    sequence: int = 0,
) -> str:
    hint = choose_latency_hint(settings, context=context, elapsed_ms=elapsed_ms, sequence=sequence)
    if channel == "ssml":
        return render_ssml_preamble(hint)
    return render_chat_preamble(hint)
