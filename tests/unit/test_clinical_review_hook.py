import json

from scripts.clinical_review_hook import missing_review_metadata
from scripts.clinical_review_hook import requires_clinical_review


def test_requires_clinical_review_uses_manifest_policy() -> None:
    flagged = requires_clinical_review(["locales/hu/phrases/01_cri_phrases.hu.jsonc", "locales/hu/phrases/10_enc_phrases.hu.jsonc"])
    assert "locales/hu/phrases/01_cri_phrases.hu.jsonc" in flagged
    assert "locales/hu/phrases/10_enc_phrases.hu.jsonc" not in flagged


def test_missing_review_metadata_detects_items_without_review(tmp_path) -> None:
    sample_path = tmp_path / "sample.jsonc"
    sample_path.write_text(json.dumps([
        {
            "id": "sample_001",
            "pri": 1,
            "rec": ["n"],
            "use": ["c"],
            "tags": ["cri"],
            "pp": [{"txt": "sample", "t": "n", "l": "s"}]
        }
    ]), encoding="utf-8")
    missing = missing_review_metadata([str(sample_path)])
    assert missing[str(sample_path)] == 1


def test_missing_review_metadata_is_empty_for_backfilled_crisis_file() -> None:
    missing = missing_review_metadata(["locales/hu/phrases/01_cri_phrases.hu.jsonc"])
    assert missing == {}