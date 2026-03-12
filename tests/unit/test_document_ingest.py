from pathlib import Path
from types import SimpleNamespace

from assistant_runtime.document_ingest import build_external_knowledge_pack
from assistant_runtime.document_ingest import collect_local_document_paths
from assistant_runtime.remote_document_ingest import download_remote_documents


def test_collect_local_document_paths_discovers_supported_files(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "guide.md").write_text("# Guide\n\nHelpful local guidance.", encoding="utf-8")
    (docs_dir / "notes.txt").write_text("Short notes.", encoding="utf-8")
    (docs_dir / "ignore.bin").write_bytes(b"nope")

    paths = collect_local_document_paths([docs_dir])

    assert [path.name for path in paths] == ["guide.md", "notes.txt"]


def test_build_external_knowledge_pack_extracts_review_ready_snippets(tmp_path: Path) -> None:
    html_path = tmp_path / "article.html"
    html_path.write_text(
        """
        <html><body>
          <h1>Grounding and pacing</h1>
          <p>Grounding exercises can reduce overwhelm during a difficult therapy session by slowing attention and naming the immediate environment in a structured way.</p>
          <p>Therapists can introduce brief sensory anchors, breathing cues, and one-step reflection prompts to stabilize the conversation before deeper exploration.</p>
        </body></html>
        """,
        encoding="utf-8",
    )

    payload = build_external_knowledge_pack("local_pack", document_paths=[html_path])
    snippets = payload["knowledge_enrichment"]["knowledge_snippets"]

    assert snippets
    assert snippets[0]["meta"]["src"] == "lit"
    assert snippets[0]["meta"]["status"] == "rev"
    assert snippets[0]["meta"]["enabled_in"] == ["rv", "tst"]
    assert snippets[0]["source_path"].endswith("article.html")
    assert "Grounding exercises can reduce overwhelm" in snippets[0]["text"]


def test_download_remote_documents_saves_fetched_files(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    class _DummyResponse:
        def __init__(self, body: bytes, content_type: str) -> None:
            self._body = body
            self.headers = {"Content-Type": content_type}

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            return None

    def _fake_urlopen(http_request, timeout=30):
        captured["headers"] = dict(http_request.headers)
        return _DummyResponse(b"<html><body>remote guidance</body></html>", "text/html; charset=utf-8")

    monkeypatch.setattr("assistant_runtime.remote_document_ingest.request.urlopen", _fake_urlopen)

    downloads = download_remote_documents(
        ["https://example.invalid/article"],
        output_dir=tmp_path,
        extra_headers={"X-Test-Header": "enabled"},
        cookie_header="session=abc123",
    )

    assert len(downloads) == 1
    assert downloads[0].output_path.exists()
    assert downloads[0].output_path.suffix == ".html"
    assert captured["headers"]["X-test-header"] == "enabled"
    assert captured["headers"]["Cookie"] == "session=abc123"