from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from assistant_runtime.content_metadata import is_content_enabled
from assistant_runtime.pipeline.analysis_pipeline import AnalysisResult
from assistant_runtime.selection_engine import SelectionRequest


CATEGORY_NAME_BY_SHORT = {
    "cri": "crisis",
    "bd": "boundary",
    "str": "structure",
    "emp": "empathy",
    "oq": "open_questions",
    "cq": "closed_questions",
    "var": "variants",
    "cbt": "cbt_mi_dbt",
    "edu": "psychoeducation",
    "enc": "encouragement",
    "clo": "closing",
    "cult": "cultural",
}

FALLBACK_CATEGORY_BY_BEHAVIOR = {
    "use_variant": "variants",
    "ask_clarifying": "open_questions",
    "escalate": "crisis",
}


@dataclass(slots=True)
class TriggerMatch:
    trigger: dict[str, Any]
    score: tuple[int, int, int, int, int, int, int]
    matched_category: str


def _normalize_values(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item is not None}
    return {str(value)}


def _match_status(item_values: Any, requested_values: set[str]) -> int:
    normalized_item_values = _normalize_values(item_values)
    if not normalized_item_values or not requested_values:
        return 0
    if normalized_item_values.intersection(requested_values):
        return 1
    return -1


def _sentiment_code(sentiment: str) -> str:
    if sentiment == "negative":
        return "neg"
    if sentiment == "positive":
        return "pos"
    return "neu"


def _fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _regex_matches(pattern: str, text: str) -> bool:
    try:
        normalized_pattern = pattern.replace("\\\\", "\\")
        if re.search(normalized_pattern, text, flags=re.IGNORECASE) is not None:
            return True

        folded_pattern = _fold_text(normalized_pattern)
        folded_text = _fold_text(text)
        return re.search(folded_pattern, folded_text, flags=re.IGNORECASE) is not None
    except re.error:
        return False


def _match_strength(trigger: dict[str, Any], text: str, analysis: AnalysisResult) -> int:
    matcher = trigger.get("m", {})
    match_type = matcher.get("t")
    lowered_text = text.casefold()
    folded_text = _fold_text(text).casefold()
    score = 0

    if match_type == "regex":
        score = 3 if _regex_matches(str(matcher.get("p", "")), text) else 0
    elif match_type == "exact":
        pattern = str(matcher.get("p", "")).casefold()
        examples = {str(example).casefold() for example in trigger.get("ex", [])}
        folded_pattern = _fold_text(str(matcher.get("p", ""))).casefold()
        folded_examples = {_fold_text(str(example)).casefold() for example in trigger.get("ex", [])}
        score = 3 if lowered_text == pattern or lowered_text in examples or folded_text == folded_pattern or folded_text in folded_examples else 0
    elif match_type == "intent":
        score = 2 if matcher.get("i") == analysis.intent else 0
    elif match_type == "sentiment":
        score = 2 if matcher.get("s") == _sentiment_code(analysis.sentiment) else 0
    elif match_type == "hybrid":
        if matcher.get("p") and _regex_matches(str(matcher.get("p", "")), text):
            score += 2
        if matcher.get("i") and matcher.get("i") == analysis.intent:
            score += 1
        if matcher.get("s") and matcher.get("s") == _sentiment_code(analysis.sentiment):
            score += 1

    tag_hits = len(set(trigger.get("tags", [])).intersection(analysis.tags))
    return score + min(tag_hits, 2)


def _profile_score(trigger: dict[str, Any], request: SelectionRequest) -> tuple[int, int, int, int]:
    return (
        _match_status(trigger.get("age"), request.age_groups),
        _match_status(trigger.get("lit"), {request.literacy_level} if request.literacy_level else set()),
        _match_status(trigger.get("reg"), {request.preferred_register} if request.preferred_register else set()),
        _match_status(trigger.get("persona"), request.personas),
    )


def match_trigger(bundle: Any, text: str, analysis: AnalysisResult, request: SelectionRequest) -> TriggerMatch | None:
    matches: list[TriggerMatch] = []
    safety_weight = {"hard_handoff": 3, "escalate": 2, "monitor": 1, "none": 0}

    for short_code, items in bundle.triggers.items():
        matched_category = CATEGORY_NAME_BY_SHORT.get(short_code)
        if matched_category is None:
            continue
        for trigger in items:
            if not is_content_enabled(
                trigger,
                allowed_statuses=request.allowed_content_statuses,
                channel=request.content_channel,
            ):
                continue
            strength = _match_strength(trigger, text, analysis)
            if strength <= 0:
                continue
            age_score, lit_score, reg_score, persona_score = _profile_score(trigger, request)
            score = (
                -int(trigger.get("prio", 5)),
                -safety_weight.get(trigger.get("safety", "none"), 0),
                -strength,
                -age_score,
                -lit_score,
                -reg_score,
                -persona_score,
            )
            matches.append(TriggerMatch(trigger=trigger, score=score, matched_category=matched_category))

    if not matches:
        return None

    return sorted(matches, key=lambda item: item.score)[0]


def fallback_category_name(trigger: dict[str, Any]) -> str | None:
    if trigger.get("fb") == "call_llm":
        return None
    if trigger.get("fb") == "ask_clarifying" and trigger.get("safety") in {"monitor", "escalate", "hard_handoff"}:
        return "closed_questions"
    return FALLBACK_CATEGORY_BY_BEHAVIOR.get(trigger.get("fb"))