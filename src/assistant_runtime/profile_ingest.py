from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, UTC
import json
from pathlib import Path
import re


CLINICIAN_PREFIXES = (
    "t:",
    "therapist:",
    "clinician:",
    "pszichologus:",
    "pszichoterapeuta:",
    "szakember:",
)
PATIENT_PREFIXES = (
    "p:",
    "patient:",
    "client:",
    "paciens:",
)
STOPWORDS = {
    "hogy",
    "vagy",
    "vagyok",
    "lesz",
    "mert",
    "amikor",
    "nagyon",
    "most",
    "ezt",
    "azt",
    "egy",
    "nem",
    "igen",
    "van",
    "volt",
    "minden",
    "kell",
    "az",
    "és",
    "is",
    "de",
}


@dataclass(slots=True)
class TranscriptSegment:
    speaker: str
    text: str
    source_path: str


def _read_text_file(file_path: Path) -> str:
    if file_path.suffix.lower() == ".json":
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        return json.dumps(payload, ensure_ascii=False, indent=2)
    return file_path.read_text(encoding="utf-8")


def _speaker_from_line(line: str) -> tuple[str | None, str]:
    lowered = line.casefold().strip()
    for prefix in CLINICIAN_PREFIXES:
        if lowered.startswith(prefix):
            return "clinician", line.split(":", 1)[1].strip()
    for prefix in PATIENT_PREFIXES:
        if lowered.startswith(prefix.casefold()):
            return "patient", line.split(":", 1)[1].strip()
    return None, line.strip()


def parse_transcript_segments(file_paths: list[Path]) -> list[TranscriptSegment]:
    segments: list[TranscriptSegment] = []
    for file_path in file_paths:
        for raw_line in _read_text_file(file_path).splitlines():
            speaker, text = _speaker_from_line(raw_line)
            if not speaker or not text:
                continue
            segments.append(
                TranscriptSegment(speaker=speaker, text=text, source_path=str(file_path))
            )
    return segments


def _normalize_line(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    return normalized.rstrip(" .,!?:;")


def _shortlist_lines(lines: list[str], minimum_words: int, maximum_words: int, limit: int) -> list[str]:
    unique_lines: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = _normalize_line(line)
        word_count = len(normalized.split())
        if word_count < minimum_words or word_count > maximum_words:
            continue
        folded = normalized.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        unique_lines.append(normalized)
        if len(unique_lines) >= limit:
            break
    return unique_lines


def _paragraphs_from_files(file_paths: list[Path]) -> list[tuple[str, str]]:
    paragraphs: list[tuple[str, str]] = []
    for file_path in file_paths:
        text = _read_text_file(file_path)
        for paragraph in re.split(r"\n\s*\n", text):
            normalized = re.sub(r"\s+", " ", paragraph).strip()
            if 80 <= len(normalized) <= 420:
                paragraphs.append((normalized, str(file_path)))
    return paragraphs


def _extract_topics(texts: list[str], limit: int = 8) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        for token in re.findall(r"[a-zA-ZáéíóöőúüűÁÉÍÓÖŐÚÜŰ]{4,}", text.casefold()):
            if token in STOPWORDS:
                continue
            counter[token] += 1
    return [token for token, _ in counter.most_common(limit)]


def build_profile_ingest_pack(
    profile_id: str,
    *,
    summary_files: list[Path],
    transcript_files: list[Path],
    audio_files: list[Path],
) -> dict[str, object]:
    transcript_segments = parse_transcript_segments(transcript_files)
    clinician_lines = [segment.text for segment in transcript_segments if segment.speaker == "clinician"]
    patient_lines = [segment.text for segment in transcript_segments if segment.speaker == "patient"]
    summary_paragraphs = _paragraphs_from_files(summary_files)
    topic_seed_texts = clinician_lines[:20] + patient_lines[:20] + [paragraph for paragraph, _ in summary_paragraphs]
    topics = _extract_topics(topic_seed_texts)

    phrase_candidates = [
        {
            "id": f"{profile_id}_phrase_{index + 1:03d}",
            "text": line,
            "speaker": "clinician",
            "topics": topics[:4],
            "review_status": "needs_review",
            "meta": {
                "src": "trn",
                "status": "rev",
                "enabled_in": ["rv", "tst"],
                "profile_id": profile_id,
            },
        }
        for index, line in enumerate(_shortlist_lines(clinician_lines, minimum_words=4, maximum_words=24, limit=12))
    ]

    trigger_candidates = [
        {
            "id": f"{profile_id}_trigger_{index + 1:03d}",
            "example": line,
            "speaker": "patient",
            "topics": topics[:4],
            "review_status": "needs_review",
            "meta": {
                "src": "trn",
                "status": "rev",
                "enabled_in": ["rv", "tst"],
                "profile_id": profile_id,
            },
        }
        for index, line in enumerate(_shortlist_lines(patient_lines, minimum_words=3, maximum_words=18, limit=12))
    ]

    knowledge_snippets = [
        {
            "id": f"{profile_id}_kb_{index + 1:03d}",
            "text": paragraph,
            "source_path": source_path,
            "topics": topics[:5],
            "allowed_stages": ["phrase_selection", "generative_fallback"],
            "review_status": "needs_review",
            "meta": {
                "src": "sum",
                "status": "rev",
                "enabled_in": ["rv", "tst"],
                "profile_id": profile_id,
                "origin_ref": source_path,
            },
        }
        for index, (paragraph, source_path) in enumerate(summary_paragraphs[:10])
    ]

    voice_seed_manifest = [
        {
            "audio_path": str(audio_path),
            "file_size_bytes": audio_path.stat().st_size if audio_path.exists() else None,
            "recommended_use": "speaker_clone_seed",
            "status": "needs_transcription_and_consent_review",
            "meta": {
                "src": "aud",
                "status": "rev",
                "enabled_in": ["rv"],
                "profile_id": profile_id,
                "origin_ref": str(audio_path),
            },
        }
        for audio_path in audio_files
    ]

    return {
        "profile_id": profile_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "sources": {
            "summary_files": [str(item) for item in summary_files],
            "transcript_files": [str(item) for item in transcript_files],
            "audio_files": [str(item) for item in audio_files],
        },
        "topic_hints": topics,
        "profile_enrichment": {
            "knowledge_snippets": knowledge_snippets,
            "phrase_candidates": phrase_candidates,
            "trigger_candidates": trigger_candidates,
            "voice_seed_manifest": voice_seed_manifest,
        },
    }