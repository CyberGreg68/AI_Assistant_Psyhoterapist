from __future__ import annotations

from datetime import UTC
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from assistant_runtime.ops.remote_document_ingest import download_remote_documents
from assistant_runtime.ops.review_pack_builder import build_review_candidate_pack


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def _rule_hint_summary(rule_hints: list[dict[str, Any]]) -> dict[str, list[str]]:
    summary: dict[str, list[str]] = {}
    for hint in rule_hints:
        hint_type = str(hint.get("hint_type", "unknown"))
        items = hint.get("items", [])
        rendered: list[str] = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                if "category" in item:
                    rendered.append(f"{item['category']}: {item.get('count', 0)}")
                elif "risk_flag" in item:
                    rendered.append(f"{item['risk_flag']}: {item.get('count', 0)}")
                elif "topic" in item:
                    rendered.append(str(item["topic"]))
        summary[hint_type] = rendered
    return summary


def _build_review_notes(batch_id: str, pack: dict[str, Any]) -> str:
    candidate_block = pack.get("review_candidates", {})
    phrase_candidates = candidate_block.get("phrase_candidates", [])
    trigger_candidates = candidate_block.get("trigger_candidates", [])
    rule_hints = candidate_block.get("rule_hints", [])
    summary = _rule_hint_summary(rule_hints if isinstance(rule_hints, list) else [])
    lines = [
        f"# Literature Batch Review Notes: {batch_id}",
        "",
        "## Snapshot",
        f"- Documents: {len(pack.get('source_documents', []))}",
        f"- Indexed chunks: {len(pack.get('indexed_chunks', []))}",
        f"- Knowledge snippets: {len(pack.get('knowledge_enrichment', {}).get('knowledge_snippets', []))}",
        f"- Phrase candidates: {len(phrase_candidates)}",
        f"- Trigger candidates: {len(trigger_candidates)}",
        "",
        "## Topic Hints",
    ]
    topic_hints = pack.get("topic_hints", [])
    if isinstance(topic_hints, list) and topic_hints:
        lines.extend(f"- {topic}" for topic in topic_hints)
    else:
        lines.append("- No topic hints extracted.")
    lines.extend(["", "## Rule Hints"])
    for hint_type in ["dominant_categories", "risk_flags", "topic_hints"]:
        lines.append(f"### {hint_type}")
        values = summary.get(hint_type, [])
        if values:
          lines.extend(f"- {value}" for value in values)
        else:
          lines.append("- No items.")
        lines.append("")
    lines.extend(
        [
            "## Review Focus",
            "- Validate candidate wording against clinical boundaries before import.",
            "- Confirm crisis-bearing triggers for local detectability and false-positive risk.",
            "- Decide which knowledge snippets remain review-only versus test-ready.",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def build_literature_batch(
    batch_id: str,
    *,
    output_dir: Path,
    source_paths: list[Path] | None = None,
    urls: list[str] | None = None,
    lang: str = "hu",
    download_dir: Path | None = None,
    config_dir: Path | None = None,
    recursive: bool = True,
    include_globs: list[str] | None = None,
    max_snippets: int = 40,
    max_phrase_candidates: int = 48,
    max_trigger_candidates: int = 48,
    bearer_token: str | None = None,
    basic_auth: str | None = None,
    cookie_header: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    effective_source_paths = list(source_paths or [])
    downloaded_rows: list[dict[str, Any]] = []
    if urls:
        target_download_dir = download_dir or (output_dir / "downloads")
        downloads = download_remote_documents(
            urls,
            output_dir=target_download_dir,
            bearer_token=bearer_token,
            basic_auth=basic_auth,
            cookie_header=cookie_header,
            extra_headers=extra_headers,
        )
        for item in downloads:
            effective_source_paths.append(item.output_path)
            downloaded_rows.append(
                {
                    "url": item.url,
                    "download_path": str(item.output_path),
                    "content_type": item.content_type,
                }
            )

    if not effective_source_paths:
        raise ValueError("At least one local source path or URL is required to build a literature batch.")

    pack = build_review_candidate_pack(
        batch_id,
        source_paths=effective_source_paths,
        lang=lang,
        config_dir=config_dir,
        recursive=recursive,
        include_globs=include_globs,
        max_snippets=max_snippets,
        max_phrase_candidates=max_phrase_candidates,
        max_trigger_candidates=max_trigger_candidates,
    )

    doc_by_id = {
        str(item["doc_id"]): item
        for item in pack.get("source_documents", [])
        if isinstance(item, dict) and "doc_id" in item
    }
    path_to_download = {entry["download_path"]: entry for entry in downloaded_rows}
    documents_rows: list[dict[str, Any]] = []
    for item in pack.get("source_documents", []):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        download_entry = path_to_download.get(str(item.get("path")))
        if download_entry:
            row["url"] = download_entry["url"]
            row["content_type"] = download_entry["content_type"]
        documents_rows.append(row)

    chunk_rows: list[dict[str, Any]] = []
    for chunk in pack.get("indexed_chunks", []):
        if not isinstance(chunk, dict):
            continue
        row = dict(chunk)
        source_doc = doc_by_id.get(str(chunk.get("doc_id")), {})
        if isinstance(source_doc, dict):
            row["source_extension"] = source_doc.get("extension")
        chunk_rows.append(row)

    knowledge_rows = [
        item for item in pack.get("knowledge_enrichment", {}).get("knowledge_snippets", []) if isinstance(item, dict)
    ]
    phrase_rows = [
        item for item in pack.get("review_candidates", {}).get("phrase_candidates", []) if isinstance(item, dict)
    ]
    trigger_rows = [
        item for item in pack.get("review_candidates", {}).get("trigger_candidates", []) if isinstance(item, dict)
    ]
    rule_hints = [
        item for item in pack.get("review_candidates", {}).get("rule_hints", []) if isinstance(item, dict)
    ]

    manifest = {
        "batch_id": batch_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": {
            "local_paths": [str(path) for path in source_paths or []],
            "urls": list(urls or []),
            "resolved_paths": pack.get("sources", {}).get("resolved_paths", []),
            "downloaded_files": downloaded_rows,
        },
        "counts": {
            "documents": len(documents_rows),
            "chunks": len(chunk_rows),
            "knowledge_snippets": len(knowledge_rows),
            "phrase_candidates": len(phrase_rows),
            "trigger_candidates": len(trigger_rows),
            "rule_hints": len(rule_hints),
        },
        "lang": lang,
        "topic_hints": pack.get("topic_hints", []),
    }

    _write_json(output_dir / "manifest.json", manifest)
    _write_jsonl(output_dir / "documents.jsonl", documents_rows)
    _write_jsonl(output_dir / "chunks.jsonl", chunk_rows)
    _write_jsonl(output_dir / "knowledge_snippets.jsonl", knowledge_rows)
    _write_jsonl(output_dir / "phrase_candidates.jsonl", phrase_rows)
    _write_jsonl(output_dir / "trigger_candidates.jsonl", trigger_rows)
    _write_json(output_dir / "rule_hints.json", {"rule_hints": rule_hints})
    (output_dir / "review_notes.md").write_text(_build_review_notes(batch_id, pack), encoding="utf-8")

    return {
        "manifest": manifest,
        "pack": pack,
        "output_dir": str(output_dir),
    }