from pathlib import Path

from assistant_runtime.profile_ingest import build_profile_ingest_pack


def test_build_profile_ingest_pack_extracts_candidates(tmp_path: Path) -> None:
    summary = tmp_path / "summary.md"
    transcript = tmp_path / "session.txt"
    audio = tmp_path / "session.wav"

    summary.write_text(
        "A kliensnel visszatero tema a szakitas utani szorongas es az alvasromlas. A szakember gyakran rovid validalassal kezd, majd egy konkret nyitott kerdesre valt.",
        encoding="utf-8",
    )
    transcript.write_text(
        "T: Most az a legfontosabb, hogy egy picit lassitsunk.\n"
        "P: Szakitas utan vagyok es nehezen alszom.\n"
        "T: Mi az, ami estenkent a legerosebben porog benned?\n",
        encoding="utf-8",
    )
    audio.write_bytes(b"RIFFdemo")

    payload = build_profile_ingest_pack(
        "therapist_a",
        summary_files=[summary],
        transcript_files=[transcript],
        audio_files=[audio],
    )

    enrichment = payload["profile_enrichment"]
    assert enrichment["phrase_candidates"]
    assert enrichment["trigger_candidates"]
    assert enrichment["knowledge_snippets"]
    assert enrichment["voice_seed_manifest"][0]["recommended_use"] == "speaker_clone_seed"
    assert enrichment["phrase_candidates"][0]["meta"]["src"] == "trn"
    assert enrichment["knowledge_snippets"][0]["meta"]["status"] == "rev"