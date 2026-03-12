from scripts.check_locale_alignment import check_alignment


def test_locale_alignment_passes_for_current_skeletons() -> None:
    assert check_alignment(["hu", "en", "de"]) == []
