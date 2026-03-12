from __future__ import annotations

from datetime import UTC
from datetime import datetime
from pathlib import Path
import re
import unicodedata

from assistant_runtime.adapters.factory import build_stt_adapter
from assistant_runtime.adapters.stt_adapter import STTAdapter
from assistant_runtime.ops.document_ingest import collect_local_document_paths
from assistant_runtime.ops.document_ingest import extract_topic_hints
from assistant_runtime.ops.document_ingest import normalize_ingest_text
from assistant_runtime.ops.document_ingest import read_document_text
from assistant_runtime.ops.document_ingest import split_text_into_chunks
from assistant_runtime.pipeline.analysis_pipeline import analyze_text
from assistant_runtime.pipeline.risk_rules import detect_risk_flags


SUPPORTED_AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".m4a",
    ".ogg",
    ".flac",
}

_CATEGORY_BY_TAG_PRIORITY = (
    ("cri", "crisis"),
    ("oq", "open_questions"),
    ("emp", "empathy"),
    ("bd", "boundary"),
    ("str", "structure"),
    ("edu", "psychoeducation"),
    ("enc", "encouragement"),
)

_PHRASE_MARKERS = {
    "kérdezd",
    "kerdezd",
    "érthető",
    "ertheto",
    "hasznos",
    "segít",
    "segit",
    "mondhatod",
    "kérlek",
    "kerlek",
    "fontos",
}

_TRIGGER_MARKERS = {
    "érzem",
    "erzem",
    "vagyok",
    "akarok",
    "félek",
    "felek",
    "nem bírom",
    "nem birom",
    "szeretnék",
    "szeretnek",
    "szégyellem",
    "szegyellem",
    "hiányzik",
    "hianyzik",
}


def collect_review_source_paths(
    source_paths: list[Path],
    *,
    recursive: bool = True,
    include_globs: list[str] | None = None,
) -> list[Path]:
    document_paths = collect_local_document_paths(
        source_paths,
        recursive=recursive,
        include_globs=include_globs,
    )
    discovered = {path.resolve() for path in document_paths}
    patterns = include_globs or ["*"]
    for source_path in source_paths:
        if source_path.is_file() and source_path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
            discovered.add(source_path.resolve())
            continue
        if not source_path.is_dir():
            continue
        iterator = source_path.rglob if recursive else source_path.glob
        for pattern in patterns:
            for candidate in iterator(pattern):
                if candidate.is_file() and candidate.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
                    discovered.add(candidate.resolve())
    return sorted(discovered)


def _fold_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _sentence_candidates(text: str) -> list[str]:
    parts = [
        normalize_ingest_text(piece)
        for piece in re.split(r"(?<=[.!?])\s+", text)
        if normalize_ingest_text(piece)
    ]
    return [piece for piece in parts if 24 <= len(piece) <= 240]


def _infer_category(tags: set[str], intent: str, risk_flags: set[str]) -> str:
    if "crisis" in risk_flags:
        return "crisis"
    for tag, category in _CATEGORY_BY_TAG_PRIORITY:
        if tag in tags:
            return category
    if intent == "guidance":
        return "psychoeducation"
    return "variants"


def _looks_like_phrase_candidate(text: str, tags: set[str], risk_flags: set[str]) -> bool:
    lowered = text.casefold()
    if re.search(r"https?://|www\.", lowered):
        return False
    if sum(char.isdigit() for char in text) > max(3, len(text) // 8):
        return False
    if "?" in text:
        return True
    if tags or risk_flags:
        return True
    return any(marker in lowered for marker in _PHRASE_MARKERS)


def _looks_like_trigger_candidate(text: str, risk_flags: set[str]) -> bool:
    lowered = text.casefold()
    if risk_flags:
        return True
    return any(marker in lowered for marker in _TRIGGER_MARKERS)


def _normalized_trigger_forms(text: str) -> list[str]:
    canonical = re.sub(r"[^\w\sáéíóöőúüűÁÉÍÓÖŐÚÜŰ-]", " ", text.casefold())
    canonical = normalize_ingest_text(canonical)
    folded = normalize_ingest_text(_fold_text(canonical).casefold())
    values = [canonical]
    if folded and folded != canonical:
        values.append(folded)
    return values


def _trigger_confidence(text: str, risk_flags: set[str], tags: set[str]) -> float:
    if risk_flags:
        return 0.92
    if "?" in text:
        return 0.58
    if len(tags) >= 2:
        return 0.73
    return 0.66


def _load_source_text(file_path: Path, stt_adapter: STTAdapter | None) -> tuple[str, dict[str, object]]:
    if file_path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS:
        if stt_adapter is None:
            raise ValueError("Audio ingest requires an STT adapter or config directory.")
        transcript = stt_adapter.transcribe(file_path)
        return transcript.text, {
            "asset_kind": "audio",
            "transcript_source": transcript.source,
            "transcript_confidence": transcript.confidence,
        }
    return read_document_text(file_path), {"asset_kind": "document"}


def build_review_candidate_pack(
    pack_id: str,
    *,
    source_paths: list[Path],
    config_dir: Path | None = None,
    stt_adapter: STTAdapter | None = None,
    recursive: bool = True,
    include_globs: list[str] | None = None,
    max_snippets: int = 40,
    max_phrase_candidates: int = 48,
    max_trigger_candidates: int = 48,
) -> dict[str, object]:
    resolved_paths = collect_review_source_paths(
        source_paths,
        recursive=recursive,
        include_globs=include_globs,
    )
    if not resolved_paths:
        raise ValueError("No supported source files were found for review-pack generation.")

    if any(path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS for path in resolved_paths) and stt_adapter is None:
        if config_dir is None:
            raise ValueError("A config directory is required when audio files need STT transcription.")
        stt_adapter = build_stt_adapter(config_dir)

    source_documents: list[dict[str, object]] = []
    indexed_chunks: list[dict[str, object]] = []
    raw_chunks: list[dict[str, object]] = []

    for index, source_path in enumerate(resolved_paths, start=1):
        source_text, extra_metadata = _load_source_text(source_path, stt_adapter)
        normalized_text = normalize_ingest_text(source_text)
        doc_id = f"{pack_id}_doc_{index:03d}"
        chunks = split_text_into_chunks(source_text)
        if not chunks and normalized_text:
            chunks = [normalized_text]
        source_documents.append(
            {
                "doc_id": doc_id,
                "path": str(source_path),
                "extension": source_path.suffix.lower(),
                "char_count": len(normalized_text),
                "chunk_count": len(chunks),
                **extra_metadata,
            }
        )
        for chunk_index, chunk in enumerate(chunks, start=1):
            analysis = analyze_text(chunk)
            chunk_id = f"{doc_id}_chunk_{chunk_index:03d}"
            category = _infer_category(analysis.tags, analysis.intent, analysis.risk_flags)
            chunk_record = {
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "text": chunk,
                "path": str(source_path),
                "intent": analysis.intent,
                "tags": sorted(analysis.tags),
                "risk_flags": sorted(analysis.risk_flags),
                "category_hint": category,
                "gist": analysis.gist,
            }
            indexed_chunks.append(chunk_record)
            raw_chunks.append(chunk_record)

    topic_hints = extract_topic_hints([item["text"] for item in raw_chunks])

    knowledge_snippets: list[dict[str, object]] = []
    for index, chunk in enumerate(raw_chunks[:max_snippets], start=1):
        knowledge_snippets.append(
            {
                "id": f"{pack_id}_kb_{index:03d}",
                "text": chunk["text"],
                "source_path": chunk["path"],
                "topics": topic_hints[:6],
                "categories": [chunk["category_hint"], "external_reference"],
                "allowed_stages": ["phrase_selection", "generative_fallback"],
                "review_status": "needs_review",
                "meta": {
                    "src": "lit",
                    "status": "rev",
                    "enabled_in": ["rv", "tst"],
                    "pack_id": pack_id,
                    "origin_ref": chunk["chunk_id"],
                },
            }
        )

    phrase_candidates: list[dict[str, object]] = []
    trigger_candidates: list[dict[str, object]] = []
    seen_phrase_texts: set[str] = set()
    seen_trigger_texts: set[str] = set()

    for chunk in raw_chunks:
        for sentence in _sentence_candidates(chunk["text"]):
            sentence_analysis = analyze_text(sentence)
            category = _infer_category(
                sentence_analysis.tags,
                sentence_analysis.intent,
                sentence_analysis.risk_flags,
            )
            sentence_key = sentence.casefold()
            if (
                len(phrase_candidates) < max_phrase_candidates
                and sentence_key not in seen_phrase_texts
                and _looks_like_phrase_candidate(sentence, sentence_analysis.tags, sentence_analysis.risk_flags)
            ):
                seen_phrase_texts.add(sentence_key)
                phrase_candidates.append(
                    {
                        "candidate_id": f"phr_cand_{pack_id}_{len(phrase_candidates) + 1:03d}",
                        "lang": "hu",
                        "category": category,
                        "intent": sentence_analysis.intent,
                        "tags": sorted(sentence_analysis.tags),
                        "allowed_uses": ["c"],
                        "suggested_priority": 1 if sentence_analysis.risk_flags else 2,
                        "draft_text": sentence,
                        "rationale": f"Extracted from {Path(chunk['path']).name} and tagged for {category} review.",
                        "source_doc_ids": [chunk["doc_id"]],
                        "source_chunk_ids": [chunk["chunk_id"]],
                        "evidence_level": "source_derived",
                        "safety_flags": sorted(sentence_analysis.risk_flags) or ["manual_review_required"],
                        "review_status": "candidate",
                    }
                )

            if (
                len(trigger_candidates) < max_trigger_candidates
                and sentence_key not in seen_trigger_texts
                and _looks_like_trigger_candidate(sentence, sentence_analysis.risk_flags)
            ):
                seen_trigger_texts.add(sentence_key)
                trigger_tags = set(sentence_analysis.tags)
                if category == "crisis":
                    trigger_tags.update({"cri", "saf"})
                trigger_candidates.append(
                    {
                        "candidate_id": f"trg_cand_{pack_id}_{len(trigger_candidates) + 1:03d}",
                        "lang": "hu",
                        "category": category,
                        "trigger_text": sentence,
                        "normalized_forms": _normalized_trigger_forms(sentence),
                        "matched_tags": sorted(trigger_tags),
                        "suggested_risk_flags": sorted(sentence_analysis.risk_flags),
                        "confidence": _trigger_confidence(
                            sentence,
                            sentence_analysis.risk_flags,
                            sentence_analysis.tags,
                        ),
                        "source_doc_ids": [chunk["doc_id"]],
                        "source_chunk_ids": [chunk["chunk_id"]],
                        "rationale": f"First-person or risk-bearing language extracted from {Path(chunk['path']).name}.",
                        "review_status": "candidate",
                    }
                )

    category_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    for chunk in raw_chunks:
        category = str(chunk["category_hint"])
        category_counts[category] = category_counts.get(category, 0) + 1
        for risk_flag in chunk["risk_flags"]:
            risk_counts[risk_flag] = risk_counts.get(risk_flag, 0) + 1

    rule_hints = [
        {
            "hint_type": "dominant_categories",
            "items": [
                {"category": category, "count": count}
                for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
            ],
        },
        {
            "hint_type": "risk_flags",
            "items": [
                {"risk_flag": risk_flag, "count": count}
                for risk_flag, count in sorted(risk_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
        },
        {
            "hint_type": "topic_hints",
            "items": [{"topic": topic} for topic in topic_hints[:10]],
        },
    ]

    return {
        "pack_id": pack_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": {
            "source_paths": [str(path) for path in source_paths],
            "resolved_paths": [str(path) for path in resolved_paths],
            "document_count": len(source_documents),
        },
        "topic_hints": topic_hints,
        "source_documents": source_documents,
        "indexed_chunks": indexed_chunks,
        "knowledge_enrichment": {
            "knowledge_snippets": knowledge_snippets,
        },
        "review_candidates": {
            "phrase_candidates": phrase_candidates,
            "trigger_candidates": trigger_candidates,
            "rule_hints": rule_hints,
        },
    }