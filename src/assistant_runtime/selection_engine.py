from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from assistant_runtime.content_metadata import content_meta
from assistant_runtime.content_metadata import is_content_enabled


@dataclass(slots=True)
class SelectionRequest:
    intent: str = "support"
    tags: set[str] = field(default_factory=set)
    preferred_tones: set[str] = field(default_factory=lambda: {"n", "w"})
    allowed_uses: set[str] = field(default_factory=lambda: {"c"})
    risk_flags: set[str] = field(default_factory=set)
    age_groups: set[str] = field(default_factory=set)
    literacy_level: str | None = None
    preferred_register: str | None = None
    personas: set[str] = field(default_factory=set)
    response_preferences: dict[str, object] = field(default_factory=dict)
    candidate_ids: set[str] = field(default_factory=set)
    forced_category: str | None = None
    allowed_content_statuses: set[str] = field(default_factory=lambda: {"appr"})
    content_channel: str = "rt"


@dataclass(slots=True)
class PhraseCandidate:
    sort_key: tuple[int, int, int, int, int, int, int]
    category: str
    item: dict[str, Any]
    alignment: dict[str, str]


def _normalize_values(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item is not None}
    return {str(value)}


def _match_status(item_values: Any, requested_values: set[str]) -> tuple[int, str]:
    normalized_item_values = _normalize_values(item_values)
    if not normalized_item_values or not requested_values:
        return 0, "not_applicable"
    if normalized_item_values.intersection(requested_values):
        return 1, "match"
    return -1, "mismatch"


def _profile_score(item: dict[str, Any], request: SelectionRequest) -> tuple[tuple[int, int, int, int], dict[str, str]]:
    age_score, age_status = _match_status(item.get("age"), request.age_groups)
    lit_score, lit_status = _match_status(
        item.get("lit"),
        {request.literacy_level} if request.literacy_level else set(),
    )
    reg_score, reg_status = _match_status(
        item.get("reg"),
        {request.preferred_register} if request.preferred_register else set(),
    )
    persona_score, persona_status = _match_status(item.get("persona"), request.personas)
    alignment = {
        "age": age_status,
        "lit": lit_status,
        "reg": reg_status,
        "persona": persona_status,
    }
    return (age_score, lit_score, reg_score, persona_score), alignment


def _score_item(
    item: dict[str, Any],
    request: SelectionRequest,
    category_priority: int,
) -> tuple[int, int, int, int, int, int, int]:
    tag_hits = len(request.tags.intersection(item.get("tags", [])))
    tone_hits = len(request.preferred_tones.intersection(item.get("rec", [])))
    use_hits = len(request.allowed_uses.intersection(item.get("use", [])))
    effective_priority = int(item.get("pri", category_priority))
    age_score, lit_score, reg_score, persona_score = _profile_score(item, request)[0]
    return (
        -effective_priority,
        -age_score,
        -lit_score,
        -reg_score,
        -persona_score,
        -tag_hits,
        -(tone_hits + use_hits),
    )


def _build_delivery_preferences(request: SelectionRequest) -> dict[str, object]:
    preferences = dict(request.response_preferences)
    if "tts_speed" not in preferences and request.age_groups.intersection({"child", "senior"}):
        preferences["tts_speed"] = "slow"
    return preferences


def _pick_phrase(item: dict[str, Any], request: SelectionRequest) -> dict[str, Any]:
    for phrase in item.get("pp", []):
        if phrase.get("t") in request.preferred_tones:
            return phrase
    return item["pp"][0]


def rank_phrase_candidates(bundle: Any, request: SelectionRequest) -> list[PhraseCandidate]:
    categories = bundle.manifest["category_order"]
    if "cri" in request.tags or "crisis" in request.risk_flags:
        categories = [item for item in categories if item["name"] == "crisis"]
    if request.forced_category:
        categories = [item for item in categories if item["name"] == request.forced_category]

    candidates: list[PhraseCandidate] = []
    for category in categories:
        category_name = category["name"]
        category_priority = int(category["default_priority"])
        for item in bundle.categories.get(category_name, []):
            if request.candidate_ids and item["id"] not in request.candidate_ids:
                continue
            if request.allowed_uses and not request.allowed_uses.intersection(item.get("use", [])):
                continue
            if not is_content_enabled(
                item,
                allowed_statuses=request.allowed_content_statuses,
                channel=request.content_channel,
            ):
                continue
            _, alignment = _profile_score(item, request)
            candidates.append(
                PhraseCandidate(
                    sort_key=_score_item(item, request, category_priority),
                    category=category_name,
                    item=item,
                    alignment=alignment,
                )
            )

    return sorted(candidates, key=lambda entry: entry.sort_key)


def list_phrase_candidates(
    bundle: Any,
    request: SelectionRequest,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    candidates = rank_phrase_candidates(bundle, request)
    if limit is not None:
        candidates = candidates[:limit]

    delivery_preferences = _build_delivery_preferences(request)
    serialized: list[dict[str, Any]] = []
    for candidate in candidates:
        phrase = _pick_phrase(candidate.item, request)
        serialized.append(
            {
                "category": candidate.category,
                "item_id": candidate.item["id"],
                "text": phrase["txt"],
                "tone": phrase.get("t"),
                "length": phrase.get("l"),
                "tags": list(candidate.item.get("tags", [])),
                "content_meta": content_meta(candidate.item),
                "profile_alignment": candidate.alignment,
                "delivery_preferences": dict(delivery_preferences),
            }
        )
    return serialized


def select_phrase(bundle: Any, request: SelectionRequest) -> dict[str, Any]:
    candidates = rank_phrase_candidates(bundle, request)
    if not candidates:
        raise LookupError("No phrase candidates matched the current request.")

    candidate = candidates[0]
    phrase = _pick_phrase(candidate.item, request)
    delivery_preferences = _build_delivery_preferences(request)
    return {
        "category": candidate.category,
        "item_id": candidate.item["id"],
        "text": phrase["txt"],
        "tone": phrase.get("t"),
        "length": phrase.get("l"),
        "content_meta": content_meta(candidate.item),
        "profile_alignment": candidate.alignment,
        "delivery_preferences": delivery_preferences,
    }
