from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from assistant_runtime.content_metadata import is_content_enabled
from assistant_runtime.pipeline.analysis_pipeline import AnalysisResult
from assistant_runtime.core.selection_engine import SelectionRequest


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
    score: tuple[int, int, int, int, int, int, int, int, int]
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


def _tokenize_text(value: str) -> set[str]:
    folded = _fold_text(value).casefold()
    return {token for token in re.findall(r"[\w]+", folded, flags=re.UNICODE) if len(token) >= 3}


def _literal_trigger_texts(trigger: dict[str, Any]) -> list[str]:
    values: list[str] = []
    matcher = trigger.get("m", {})
    for item in trigger.get("ex", []):
        candidate = str(item).strip()
        if candidate and candidate not in values:
            values.append(candidate)
    if matcher.get("t") == "exact":
        candidate = str(matcher.get("p", "")).strip()
        if candidate and candidate not in values:
            values.append(candidate)
    return values


def _lexical_evidence(trigger: dict[str, Any], text: str) -> int:
    candidates = _literal_trigger_texts(trigger)
    if not candidates:
        return 0

    folded_text = _fold_text(text).casefold()
    text_tokens = _tokenize_text(text)
    best_score = 0
    for candidate in candidates:
        folded_candidate = _fold_text(candidate).casefold()
        if folded_candidate and folded_candidate in folded_text:
            return 4
        candidate_tokens = _tokenize_text(candidate)
        if candidate_tokens and text_tokens:
            overlap = len(candidate_tokens.intersection(text_tokens))
            coverage = overlap / max(len(candidate_tokens), 1)
            if coverage >= 0.85:
                best_score = max(best_score, 3)
            elif coverage >= 0.6:
                best_score = max(best_score, 2)
            elif overlap:
                best_score = max(best_score, 1)
        similarity = SequenceMatcher(None, folded_candidate, folded_text).ratio()
        if similarity >= 0.92:
            best_score = max(best_score, 3)
        elif similarity >= 0.82:
            best_score = max(best_score, 2)
    return best_score


def _contextual_evidence(trigger: dict[str, Any], analysis: AnalysisResult) -> int:
    score = min(len(set(trigger.get("tags", [])).intersection(analysis.tags)), 2)
    safety = str(trigger.get("safety", "none"))
    if analysis.risk_flags and safety in {"monitor", "escalate", "hard_handoff"}:
        score += 1
    if trigger.get("cat") == "cri" and "crisis" in analysis.risk_flags:
        score += 1
    return score


def _match_strength(trigger: dict[str, Any], text: str, analysis: AnalysisResult) -> tuple[int, int, int, int]:
    matcher = trigger.get("m", {})
    match_type = matcher.get("t")
    lowered_text = text.casefold()
    folded_text = _fold_text(text).casefold()
    strict_score = 0

    if match_type == "regex":
        strict_score = 3 if _regex_matches(str(matcher.get("p", "")), text) else 0
    elif match_type == "exact":
        pattern = str(matcher.get("p", "")).casefold()
        examples = {str(example).casefold() for example in trigger.get("ex", [])}
        folded_pattern = _fold_text(str(matcher.get("p", ""))).casefold()
        folded_examples = {_fold_text(str(example)).casefold() for example in trigger.get("ex", [])}
        strict_score = 4 if lowered_text == pattern or lowered_text in examples or folded_text == folded_pattern or folded_text in folded_examples else 0
    elif match_type == "intent":
        strict_score = 2 if matcher.get("i") == analysis.intent else 0
    elif match_type == "sentiment":
        strict_score = 2 if matcher.get("s") == _sentiment_code(analysis.sentiment) else 0
    elif match_type == "hybrid":
        if matcher.get("p") and _regex_matches(str(matcher.get("p", "")), text):
            strict_score += 2
        if matcher.get("i") and matcher.get("i") == analysis.intent:
            strict_score += 1
        if matcher.get("s") and matcher.get("s") == _sentiment_code(analysis.sentiment):
            strict_score += 1

    lexical_score = _lexical_evidence(trigger, text)
    contextual_score = _contextual_evidence(trigger, analysis)
    confidence_bonus = int(round(float(trigger.get("ct", {}).get("m", 0.0)) * 2)) if isinstance(trigger.get("ct"), dict) else 0
    return strict_score, lexical_score, contextual_score, confidence_bonus


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
            strict_score, lexical_score, contextual_score, confidence_bonus = _match_strength(trigger, text, analysis)
            combined_strength = (strict_score * 4) + (lexical_score * 2) + contextual_score + confidence_bonus
            if combined_strength < 4:
                continue
            age_score, lit_score, reg_score, persona_score = _profile_score(trigger, request)
            score = (
                -combined_strength,
                -strict_score,
                -lexical_score,
                -contextual_score,
                -safety_weight.get(trigger.get("safety", "none"), 0),
                -int(trigger.get("prio", 5)),
                -confidence_bonus,
                -age_score,
                -lit_score,
                -(reg_score + persona_score),
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