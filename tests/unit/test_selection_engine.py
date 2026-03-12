from pathlib import Path
from types import SimpleNamespace

from assistant_runtime.manifest_loader import load_bundle
from assistant_runtime.selection_engine import SelectionRequest
from assistant_runtime.selection_engine import select_phrase


def test_crisis_tag_prioritizes_crisis_category() -> None:
    bundle = load_bundle(Path.cwd(), "hu")
    request = SelectionRequest(tags={"cri", "saf"}, risk_flags={"crisis"})
    result = select_phrase(bundle, request)
    assert result["category"] == "crisis"


def test_profile_preferences_prefer_matching_phrase_metadata() -> None:
    bundle = SimpleNamespace(
        manifest={"category_order": [{"name": "empathy", "default_priority": 1}]},
        categories={
            "empathy": [
                {
                    "id": "emp_clinical",
                    "pri": 1,
                    "reg": "clinical",
                    "lit": "high",
                    "age": ["adult"],
                    "rec": ["n"],
                    "use": ["c"],
                    "tags": ["emp"],
                    "pp": [{"txt": "Clinical phrasing.", "t": "n", "l": "m"}],
                },
                {
                    "id": "emp_plain",
                    "pri": 1,
                    "reg": "plain",
                    "lit": "low",
                    "age": ["adult", "senior"],
                    "persona": ["retiree"],
                    "rec": ["w", "n"],
                    "use": ["c"],
                    "tags": ["emp"],
                    "pp": [{"txt": "Plain phrasing.", "t": "w", "l": "s"}],
                },
            ]
        },
    )

    request = SelectionRequest(
        tags={"emp"},
        age_groups={"senior"},
        literacy_level="low",
        preferred_register="plain",
        personas={"retiree"},
    )

    result = select_phrase(bundle, request)

    assert result["item_id"] == "emp_plain"
    assert result["profile_alignment"]["age"] == "match"
    assert result["profile_alignment"]["lit"] == "match"
    assert result["profile_alignment"]["reg"] == "match"


def test_senior_profile_adds_slow_tts_delivery_hint() -> None:
    bundle = SimpleNamespace(
        manifest={"category_order": [{"name": "empathy", "default_priority": 1}]},
        categories={
            "empathy": [
                {
                    "id": "emp_001",
                    "pri": 1,
                    "rec": ["n"],
                    "use": ["c"],
                    "tags": ["emp"],
                    "pp": [{"txt": "Supportive phrasing.", "t": "n", "l": "s"}],
                }
            ]
        },
    )

    result = select_phrase(bundle, SelectionRequest(tags={"emp"}, age_groups={"senior"}))

    assert result["delivery_preferences"]["tts_speed"] == "slow"


def test_unapproved_phrase_is_filtered_by_default() -> None:
    bundle = SimpleNamespace(
        manifest={"category_order": [{"name": "empathy", "default_priority": 1}]},
        categories={
            "empathy": [
                {
                    "id": "emp_review",
                    "pri": 1,
                    "rec": ["n"],
                    "use": ["c"],
                    "tags": ["emp"],
                    "meta": {"src": "trn", "status": "rev", "enabled_in": ["rv", "tst"]},
                    "pp": [{"txt": "Review only phrasing.", "t": "n", "l": "s"}],
                },
                {
                    "id": "emp_live",
                    "pri": 1,
                    "rec": ["n"],
                    "use": ["c"],
                    "tags": ["emp"],
                    "meta": {"src": "dev", "status": "appr", "enabled_in": ["rt", "rv", "tst"]},
                    "pp": [{"txt": "Approved phrasing.", "t": "n", "l": "s"}],
                },
            ]
        },
    )

    result = select_phrase(bundle, SelectionRequest(tags={"emp"}))

    assert result["item_id"] == "emp_live"
    assert result["content_meta"]["status"] == "appr"


def test_review_phrase_can_be_enabled_in_test_channel() -> None:
    bundle = SimpleNamespace(
        manifest={"category_order": [{"name": "empathy", "default_priority": 1}]},
        categories={
            "empathy": [
                {
                    "id": "emp_review",
                    "pri": 1,
                    "rec": ["n"],
                    "use": ["c"],
                    "tags": ["emp"],
                    "meta": {"src": "trn", "status": "rev", "enabled_in": ["rv", "tst"]},
                    "pp": [{"txt": "Review only phrasing.", "t": "n", "l": "s"}],
                }
            ]
        },
    )

    result = select_phrase(
        bundle,
        SelectionRequest(
            tags={"emp"},
            allowed_content_statuses={"appr", "rev", "test", "sugg"},
            content_channel="tst",
        ),
    )

    assert result["item_id"] == "emp_review"
    assert result["content_meta"]["src"] == "trn"
