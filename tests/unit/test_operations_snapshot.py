from pathlib import Path

from assistant_runtime.operations_snapshot import build_operations_snapshot


def test_build_operations_snapshot_contains_expected_sections() -> None:
    snapshot = build_operations_snapshot(Path.cwd() / "config")

    assert snapshot["roles"]["patient"]["ingress"]
    assert snapshot["pipeline"]["stages"]
    assert snapshot["latency_masking"]["contexts"]