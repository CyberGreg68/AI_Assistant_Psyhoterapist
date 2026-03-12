from __future__ import annotations

from dataclasses import dataclass, field

from assistant_runtime.pipeline.risk_rules import detect_risk_flags


@dataclass(slots=True)
class AnalysisResult:
    intent: str
    sentiment: str
    tags: set[str] = field(default_factory=set)
    risk_flags: set[str] = field(default_factory=set)
    gist: str = ""


def analyze_text(text: str) -> AnalysisResult:
    lowered = text.casefold()
    tags: set[str] = set()

    if any(token in lowered for token in {"erzem", "felek", "szomoru", "duhos"}):
        tags.update({"emo", "emp"})
    if "miert" in lowered or "hogyan" in lowered:
        tags.add("oq")
    if any(token in lowered for token in {"igen", "nem", "most"}):
        tags.add("inf")

    risk_flags = detect_risk_flags(text)
    if "crisis" in risk_flags:
        tags.update({"cri", "saf", "hand", "imm"})

    if "segitseg" in lowered or "mit tegyek" in lowered:
        intent = "guidance"
    elif tags.intersection({"emo", "emp"}):
        intent = "emotional_support"
    else:
        intent = "support"

    sentiment = "negative" if tags.intersection({"emo", "emp"}) else "neutral"
    gist = text.strip()[:160]
    return AnalysisResult(intent=intent, sentiment=sentiment, tags=tags, risk_flags=risk_flags, gist=gist)
