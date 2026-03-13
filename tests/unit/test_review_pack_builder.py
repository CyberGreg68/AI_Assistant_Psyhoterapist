from pathlib import Path

from assistant_runtime.adapters.stt_adapter import Transcript
from assistant_runtime.ops.review_pack_builder import build_review_candidate_pack
from assistant_runtime.ops.review_pack_builder import collect_review_source_paths


def test_collect_review_source_paths_discovers_documents_and_audio(tmp_path: Path) -> None:
    docs_dir = tmp_path / "source"
    docs_dir.mkdir()
    (docs_dir / "notes.md").write_text("# Notes\n\nSupportive local notes.", encoding="utf-8")
    (docs_dir / "session.wav").write_bytes(b"RIFF....")
    (docs_dir / "ignore.bin").write_bytes(b"nope")

    paths = collect_review_source_paths([docs_dir])

    assert [path.name for path in paths] == ["notes.md", "session.wav"]


def test_build_review_candidate_pack_generates_review_candidates_from_text_and_audio(tmp_path: Path) -> None:
    docs_dir = tmp_path / "source"
    docs_dir.mkdir()
    (docs_dir / "guide.txt").write_text(
        "Teljesen érthető, hogy ez most nagyon nehéz. Mit érzel most a legerősebben?",
        encoding="utf-8",
    )
    audio_path = docs_dir / "session.wav"
    audio_path.write_bytes(b"RIFF....")

    class _StubSTTAdapter:
        def transcribe(self, audio_path: Path) -> Transcript:
            assert audio_path.name == "session.wav"
            return Transcript(
                text="Nem akarok élni, és nagyon félek most. Kérek segítséget.",
                source="stub_stt",
                confidence=0.91,
            )

    payload = build_review_candidate_pack(
        "review_demo",
        source_paths=[docs_dir],
        stt_adapter=_StubSTTAdapter(),
    )

    assert len(payload["source_documents"]) == 2
    assert payload["knowledge_enrichment"]["knowledge_snippets"]
    assert payload["review_candidates"]["phrase_candidates"]
    assert payload["review_candidates"]["trigger_candidates"]
    assert any(
        candidate["category"] == "crisis"
        for candidate in payload["review_candidates"]["trigger_candidates"]
    )
    assert any(
        document.get("transcript_source") == "stub_stt"
        for document in payload["source_documents"]
    )


def test_build_review_candidate_pack_filters_html_navigation_boilerplate(tmp_path: Path) -> None:
    html_path = tmp_path / "nimh_like.html"
    html_path.write_text(
        """
        <html><body>
          <div>Skip to main content</div>
          <div>Mental Health Information</div>
          <div>Get Involved</div>
          <h1>Caring for Your Mental Health</h1>
          <p>Self-care can support treatment and recovery if you have a mental illness.</p>
          <p>Seek professional help if distressing symptoms last two weeks or more.</p>
          <h2>Disclaimer</h2>
          <div>Policies and Notices</div>
        </body></html>
        """,
        encoding="utf-8",
    )

    payload = build_review_candidate_pack(
        "review_html_demo",
        source_paths=[html_path],
        lang="en",
    )

    phrase_texts = [candidate["draft_text"] for candidate in payload["review_candidates"]["phrase_candidates"]]
    snippet_texts = [snippet["text"] for snippet in payload["knowledge_enrichment"]["knowledge_snippets"]]

    assert phrase_texts
    assert any("Self-care can support treatment and recovery" in text for text in phrase_texts)
    assert all("Get Involved" not in text for text in phrase_texts)
    assert all("Policies and Notices" not in text for text in snippet_texts)