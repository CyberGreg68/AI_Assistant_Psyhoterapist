from __future__ import annotations

from collections import Counter
from datetime import UTC
from datetime import datetime
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from pypdf import PdfReader


SUPPORTED_DOCUMENT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".jsonl",
    ".html",
    ".htm",
    ".csv",
    ".tsv",
    ".xml",
    ".docx",
    ".pdf",
}

STOPWORDS = {
    "about",
    "again",
    "also",
    "amikor",
    "az",
    "azzal",
    "being",
    "clinical",
    "content",
    "dass",
    "dem",
    "der",
    "die",
    "egy",
    "einer",
    "eine",
    "es",
    "és",
    "for",
    "from",
    "hogy",
    "into",
    "ist",
    "kell",
    "local",
    "mert",
    "mit",
    "most",
    "nicht",
    "oder",
    "review",
    "sein",
    "therapy",
    "therapie",
    "therapeutic",
    "und",
    "van",
    "vagy",
    "with",
}


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "noscript"}:
            self._ignored_depth += 1
            return
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if tag in {"p", "div", "section", "article", "li"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        stripped = data.strip()
        if stripped:
            self.parts.append(stripped)

    def get_text(self) -> str:
        return re.sub(r"\n{2,}", "\n", "\n".join(self.parts)).strip()


def _read_xml_text(file_path: Path) -> str:
    try:
        root = ET.fromstring(file_path.read_text(encoding="utf-8", errors="ignore"))
    except ET.ParseError:
        return file_path.read_text(encoding="utf-8", errors="ignore")
    values = [text.strip() for text in root.itertext() if text and text.strip()]
    return "\n".join(values)


def _read_docx_text(file_path: Path) -> str:
    with ZipFile(file_path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    values = [text.strip() for text in root.itertext() if text and text.strip()]
    return "\n".join(values)


def _read_pdf_text(file_path: Path) -> str:
    reader = PdfReader(str(file_path))
    values: list[str] = []
    for page in reader.pages:
        extracted = page.extract_text() or ""
        stripped = extracted.strip()
        if stripped:
            values.append(stripped)
    return "\n\n".join(values)


def collect_local_document_paths(
    source_paths: list[Path],
    *,
    recursive: bool = True,
    include_globs: list[str] | None = None,
) -> list[Path]:
    discovered: list[Path] = []
    patterns = include_globs or ["*"]
    for source_path in source_paths:
        if source_path.is_file() and source_path.suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS:
            discovered.append(source_path)
            continue
        if not source_path.is_dir():
            continue
        iterator = source_path.rglob if recursive else source_path.glob
        for pattern in patterns:
            for candidate in iterator(pattern):
                if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_DOCUMENT_EXTENSIONS:
                    discovered.append(candidate)
    unique_paths: list[Path] = []
    seen: set[Path] = set()
    for path in sorted(discovered):
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique_paths.append(resolved)
    return unique_paths


def read_document_text(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return file_path.read_text(encoding="utf-8", errors="ignore")
    if suffix == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return json.dumps(payload, ensure_ascii=False, indent=2)
    if suffix == ".jsonl":
        rows = [json.loads(line) for line in file_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return json.dumps(rows, ensure_ascii=False, indent=2)
    if suffix in {".html", ".htm"}:
        parser = _HTMLTextExtractor()
        parser.feed(file_path.read_text(encoding="utf-8", errors="ignore"))
        return parser.get_text()
    if suffix == ".xml":
        return _read_xml_text(file_path)
    if suffix == ".docx":
        return _read_docx_text(file_path)
    if suffix == ".pdf":
        return _read_pdf_text(file_path)
    if suffix in {".csv", ".tsv"}:
        delimiter = "\t" if suffix == ".tsv" else ","
        lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(re.sub(re.escape(delimiter), " | ", line) for line in lines)
    return file_path.read_text(encoding="utf-8", errors="ignore")


def normalize_ingest_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def split_text_into_chunks(text: str, *, min_chars: int = 140, max_chars: int = 480) -> list[str]:
    paragraphs = [
        normalize_ingest_text(paragraph)
        for paragraph in re.split(r"\n\s*\n", text)
        if normalize_ingest_text(paragraph)
    ]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            sentences = [piece.strip() for piece in re.split(r"(?<=[.!?])\s+", paragraph) if piece.strip()]
            for sentence in sentences:
                if len(sentence) > max_chars:
                    chunks.extend(sentence[index:index + max_chars] for index in range(0, len(sentence), max_chars))
                    continue
                if current and len(current) + 1 + len(sentence) > max_chars:
                    chunks.append(current)
                    current = sentence
                else:
                    current = f"{current} {sentence}".strip()
            continue
        if current and len(current) + 1 + len(paragraph) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = f"{current} {paragraph}".strip()
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if len(chunk) >= min_chars]


def extract_topic_hints(texts: list[str], limit: int = 10) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        for token in re.findall(r"[a-zA-ZáéíóöőúüűÁÉÍÓÖŐÚÜŰ]{4,}", text.casefold()):
            if token in STOPWORDS:
                continue
            counter[token] += 1
    return [token for token, _ in counter.most_common(limit)]


def build_external_knowledge_pack(
    pack_id: str,
    *,
    document_paths: list[Path],
    max_snippets: int = 40,
) -> dict[str, object]:
    source_documents: list[dict[str, object]] = []
    raw_chunks: list[tuple[str, Path]] = []
    for document_path in document_paths:
        text = read_document_text(document_path)
        normalized_text = normalize_ingest_text(text)
        chunks = split_text_into_chunks(text)
        if not chunks and normalized_text:
            chunks = [normalized_text]
        source_documents.append(
            {
                "path": str(document_path),
                "extension": document_path.suffix.lower(),
                "char_count": len(normalized_text),
                "chunk_count": len(chunks),
            }
        )
        for chunk in chunks:
            raw_chunks.append((chunk, document_path))
            if len(raw_chunks) >= max_snippets:
                break
        if len(raw_chunks) >= max_snippets:
            break

    topics = extract_topic_hints([chunk for chunk, _ in raw_chunks])
    knowledge_snippets = []
    for index, (chunk, source_path) in enumerate(raw_chunks, start=1):
        knowledge_snippets.append(
            {
                "id": f"{pack_id}_kb_{index:03d}",
                "text": chunk,
                "source_path": str(source_path),
                "topics": topics[:6],
                "categories": ["external_reference"],
                "allowed_stages": ["phrase_selection", "generative_fallback"],
                "review_status": "needs_review",
                "meta": {
                    "src": "lit",
                    "status": "rev",
                    "enabled_in": ["rv", "tst"],
                    "pack_id": pack_id,
                    "origin_ref": str(source_path),
                },
            }
        )

    return {
        "pack_id": pack_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": {
            "document_paths": [str(path) for path in document_paths],
            "document_count": len(document_paths),
        },
        "topic_hints": topics,
        "source_documents": source_documents,
        "knowledge_enrichment": {
            "knowledge_snippets": knowledge_snippets,
        },
    }