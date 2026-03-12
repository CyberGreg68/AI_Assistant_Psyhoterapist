from pathlib import Path

from assistant_runtime.live.admin_api import health_payload
from assistant_runtime.live.runtime_service import RuntimeService
from assistant_runtime.ops.document_ingest import collect_local_document_paths
from assistant_runtime.ops.operations_snapshot import build_operations_snapshot


def test_live_namespace_exposes_runtime_and_health() -> None:
    payload = health_payload(Path.cwd())

    assert payload["status"] == "ok"
    assert RuntimeService


def test_ops_namespace_exposes_ingest_and_snapshot(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "sample.md").write_text("# Sample\n\nLocal ops content.", encoding="utf-8")

    paths = collect_local_document_paths([docs_dir])
    snapshot = build_operations_snapshot(Path.cwd() / "config")

    assert len(paths) == 1
    assert snapshot["pipeline"]["stages"]