from pathlib import Path

import scripts.check_legacy_data_empty as legacy_guard


def test_legacy_data_guard_detects_files(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "data" / "phrases" / "hu"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "01_crisis.hu.json").write_text("[]", encoding="utf-8")

    original_root = legacy_guard.ROOT
    original_legacy_dir = legacy_guard.LEGACY_DIR
    legacy_guard.ROOT = tmp_path
    legacy_guard.LEGACY_DIR = tmp_path / "data" / "phrases"
    try:
        files = legacy_guard.find_legacy_files()
    finally:
        legacy_guard.ROOT = original_root
        legacy_guard.LEGACY_DIR = original_legacy_dir

    assert len(files) == 1
