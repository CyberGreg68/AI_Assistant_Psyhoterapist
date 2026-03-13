from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from assistant_runtime.document_ingest import build_external_knowledge_pack
from assistant_runtime.document_ingest import collect_local_document_paths
from assistant_runtime.document_ingest import read_document_text
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


def test_read_document_text_ignores_html_script_and_style_blocks(tmp_path: Path) -> None:
    html_path = tmp_path / "article.html"
    html_path.write_text(
        """
        <html><head><style>body { color: red; }</style><script>window.alert('x');</script></head>
        <body><h1>Grounding</h1><p>Use slow breathing and orientation.</p></body></html>
        """,
        encoding="utf-8",
    )

    extracted = read_document_text(html_path)

    assert "Grounding" in extracted
    assert "Use slow breathing and orientation." in extracted
    assert "window.alert" not in extracted
    assert "color: red" not in extracted


def test_read_document_text_strips_common_html_boilerplate_sections(tmp_path: Path) -> None:
        html_path = tmp_path / "nimh_like.html"
        html_path.write_text(
                """
                <html><body>
                    <a href="#main-content">Skip to main content</a>
                    <div>Here’s how you know</div>
                    <div>An official website of the United States government</div>
                    <h1>Depression</h1>
                    <div>On this page</div>
                    <ul><li>What is depression?</li><li>Find help and support</li></ul>
                    <p>Depression can affect mood, energy, and daily functioning.</p>
                    <p>Talk to a health care provider if symptoms persist or intensify.</p>
                    <h2>Disclaimer</h2>
                    <p>Additional Links</p>
                </body></html>
                """,
                encoding="utf-8",
        )

        extracted = read_document_text(html_path)

        assert "Depression" in extracted
        assert "Depression can affect mood, energy, and daily functioning." in extracted
        assert "Skip to main content" not in extracted
        assert "Here’s how you know" not in extracted
        assert "Additional Links" not in extracted


def test_read_document_text_extracts_docx_paragraphs(tmp_path: Path) -> None:
    docx_path = tmp_path / "guide.docx"
    with ZipFile(docx_path, "w") as archive:
        archive.writestr(
            "word/document.xml",
            """
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>Első bekezdés.</w:t></w:r></w:p>
                <w:p><w:r><w:t>Második bekezdés.</w:t></w:r></w:p>
              </w:body>
            </w:document>
            """,
        )

    extracted = read_document_text(docx_path)

    assert "Első bekezdés." in extracted
    assert "Második bekezdés." in extracted


def test_download_remote_documents_saves_fetched_files(tmp_path: Path, monkeypatch) -> None:
    captured = {}

    class _DummyResponse:
        def __init__(self, body: bytes, content_type: str) -> None:
            self._body = body
            self.headers = {"Content-Type": content_type, "Content-Disposition": 'attachment; filename="source-guide.docx"'}

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
    assert "source-guide" in downloads[0].output_path.name
    assert captured["headers"]["X-test-header"] == "enabled"
    assert captured["headers"]["Cookie"] == "session=abc123"