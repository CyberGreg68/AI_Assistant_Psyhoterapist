from scripts.check_manifest_consistency import check_lang


def test_manifest_consistency_for_hungarian_locale() -> None:
    assert check_lang("hu") == []
