from __future__ import annotations

import unicodedata


CRISIS_KEYWORDS = {
    "ongyilkos",
    "meghalni",
    "artani magamnak",
    "nem akarok elni",
    "bantson",
    "veszelyben vagyok",
}


def _fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def detect_risk_flags(text: str) -> set[str]:
    lowered = text.casefold()
    folded = _fold_text(lowered)
    flags = set()
    if any(keyword in lowered or keyword in folded for keyword in CRISIS_KEYWORDS):
        flags.add("crisis")
        flags.add("handoff")
    return flags


def requires_handoff(risk_flags: set[str]) -> bool:
    return "crisis" in risk_flags or "handoff" in risk_flags
