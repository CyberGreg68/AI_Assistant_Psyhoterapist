from __future__ import annotations


CRISIS_KEYWORDS = {
    "ongyilkos",
    "meghalni",
    "artani magamnak",
    "nem akarok elni",
    "bantson",
    "veszelyben vagyok",
}


def detect_risk_flags(text: str) -> set[str]:
    lowered = text.casefold()
    flags = set()
    if any(keyword in lowered for keyword in CRISIS_KEYWORDS):
        flags.add("crisis")
        flags.add("handoff")
    return flags


def requires_handoff(risk_flags: set[str]) -> bool:
    return "crisis" in risk_flags or "handoff" in risk_flags
