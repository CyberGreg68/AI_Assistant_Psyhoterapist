import json
from pathlib import Path

import scripts.check_sensitive_diff as sensitive_diff


def test_changed_sensitive_files_filters_by_manifest_policy() -> None:
    changed = [
        "locales/hu/phrases/01_cri_phrases.hu.jsonc",
        "locales/hu/phrases/10_enc_phrases.hu.jsonc",
    ]
    sensitive = sensitive_diff.changed_sensitive_files(changed, ["hu"])
    assert "locales/hu/phrases/01_cri_phrases.hu.jsonc" in sensitive
    assert "locales/hu/phrases/10_enc_phrases.hu.jsonc" not in sensitive


def test_files_missing_review_detects_unannotated_items(tmp_path: Path) -> None:
    sample_path = tmp_path / "sample.hu.jsonc"
    sample_path.write_text(
        json.dumps([
            {
                "id": "cri_001",
                "pri": 1,
                "rec": ["n"],
                "use": ["c"],
                "tags": ["cri"],
                "pp": [{"txt": "sample", "t": "n", "l": "s"}],
            }
        ]),
        encoding="utf-8",
    )

    original_root = sensitive_diff.ROOT
    sensitive_diff.ROOT = tmp_path
    try:
        missing = sensitive_diff.files_missing_review([sample_path.name])
    finally:
        sensitive_diff.ROOT = original_root

    assert sample_path.name in missing