from pathlib import Path

from assistant_runtime.manifest_loader import load_bundle
from assistant_runtime.pipeline.analysis_pipeline import analyze_text
from assistant_runtime.selection_engine import SelectionRequest
from assistant_runtime.selection_engine import select_phrase


def test_analysis_to_selection_flow_returns_phrase() -> None:
    bundle = load_bundle(Path.cwd(), "hu")
    analysis = analyze_text("Nagyon rosszul erzem magam, szeretnek segitseget kerni.")
    request = SelectionRequest(tags=analysis.tags, risk_flags=analysis.risk_flags)
    result = select_phrase(bundle, request)
    assert result["text"]
    assert result["category"]
