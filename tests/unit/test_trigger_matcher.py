from types import SimpleNamespace

from assistant_runtime.core.selection_engine import SelectionRequest
from assistant_runtime.pipeline.analysis_pipeline import AnalysisResult
from assistant_runtime.trigger_matcher import match_trigger


def test_match_trigger_uses_lexical_evidence_beyond_strict_regex() -> None:
    bundle = SimpleNamespace(
        triggers={
            "cri": [
                {
                    "id": "pt_tr_900",
                    "m": {"t": "regex", "p": r"\\bkrizis\\b"},
                    "ex": ["nem bírom tovább", "nagyon félek most"],
                    "tags": ["cri", "saf"],
                    "prio": 1,
                    "safety": "hard_handoff",
                    "cat": "cri",
                    "cand": ["cri_001"],
                    "meta": {"status": "appr", "enabled_in": ["rt"]},
                }
            ]
        }
    )
    analysis = AnalysisResult(
        intent="emotional_support",
        sentiment="negative",
        tags={"cri", "saf"},
        risk_flags={"crisis"},
    )
    request = SelectionRequest(tags={"cri", "saf"}, risk_flags={"crisis"})

    matched = match_trigger(bundle, "Ezt már nem birom tovabb, nagyon felek most.", analysis, request)

    assert matched is not None
    assert matched.trigger["id"] == "pt_tr_900"


def test_match_trigger_does_not_fire_on_weak_overlap_only() -> None:
    bundle = SimpleNamespace(
        triggers={
            "emp": [
                {
                    "id": "pt_tr_901",
                    "m": {"t": "regex", "p": r"\\bgrounding\\b"},
                    "ex": ["grounding gyakorlat", "stabilizáló légzés"],
                    "tags": ["emp"],
                    "prio": 2,
                    "safety": "none",
                    "cat": "emp",
                    "cand": ["emp_001"],
                    "meta": {"status": "appr", "enabled_in": ["rt"]},
                }
            ]
        }
    )
    analysis = AnalysisResult(intent="support", sentiment="neutral", tags=set(), risk_flags=set())
    request = SelectionRequest()

    matched = match_trigger(bundle, "A mai beszélgetés hosszú volt, de rendben vagyok.", analysis, request)

    assert matched is None