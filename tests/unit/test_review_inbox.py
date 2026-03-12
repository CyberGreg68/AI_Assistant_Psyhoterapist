import json
from pathlib import Path

from assistant_runtime.ops.review_inbox import process_review_inbox


def test_process_review_inbox_builds_pack_and_updates_state(tmp_path: Path) -> None:
    inbox_dir = tmp_path / "incoming"
    inbox_dir.mkdir()
    batch_dir = inbox_dir / "batch_a"
    batch_dir.mkdir()
    (batch_dir / "note.txt").write_text(
        "Teljesen érthető, hogy ez most nagyon nehéz. Mit érzel most a legerősebben?",
        encoding="utf-8",
    )

    output_dir = tmp_path / "review_packs"
    state_path = tmp_path / "state" / "review_state.json"
    project_root = tmp_path / "project"
    (project_root / "data" / "runtime_state" / "audit").mkdir(parents=True)

    results = process_review_inbox(
        project_root,
        inbox_dir=inbox_dir,
        output_dir=output_dir,
        state_path=state_path,
        pack_prefix="demo",
        actor="tester",
    )

    assert len(results) == 1
    assert results[0].output_path.exists()
    payload = json.loads(results[0].output_path.read_text(encoding="utf-8"))
    assert payload["review_candidates"]["phrase_candidates"]

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert "batch_a" in state["processed_batches"]

    second_pass = process_review_inbox(
        project_root,
        inbox_dir=inbox_dir,
        output_dir=output_dir,
        state_path=state_path,
        pack_prefix="demo",
        actor="tester",
    )
    assert second_pass == []