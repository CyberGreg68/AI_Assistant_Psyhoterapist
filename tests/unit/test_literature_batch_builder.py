import json
from pathlib import Path

from assistant_runtime.ops.literature_batch_builder import build_literature_batch


def test_build_literature_batch_writes_staging_bundle(tmp_path: Path) -> None:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "notes.md").write_text(
        "Teljesen érthető, hogy ez most nagyon nehéz. Mit érzel most a legerősebben? "
        "Nagyon félek most, és nem bírom tovább ezt a nyomást. Ha azonnali veszélyben vagy, kérj helyi segítséget.",
        encoding="utf-8",
    )
    output_dir = tmp_path / "batch"

    payload = build_literature_batch(
        "lit_demo_001",
        output_dir=output_dir,
        source_paths=[source_dir],
        lang="hu",
    )

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["batch_id"] == "lit_demo_001"
    assert manifest["counts"]["documents"] == 1
    assert manifest["counts"]["phrase_candidates"] >= 1
    assert manifest["counts"]["trigger_candidates"] >= 1
    assert (output_dir / "documents.jsonl").exists()
    assert (output_dir / "chunks.jsonl").exists()
    assert (output_dir / "phrase_candidates.jsonl").exists()
    assert (output_dir / "trigger_candidates.jsonl").exists()
    assert (output_dir / "knowledge_snippets.jsonl").exists()
    assert (output_dir / "rule_hints.json").exists()
    review_notes = (output_dir / "review_notes.md").read_text(encoding="utf-8")
    assert "Literature Batch Review Notes" in review_notes
    assert "Topic Hints" in review_notes
    assert payload["manifest"]["counts"]["chunks"] >= 1