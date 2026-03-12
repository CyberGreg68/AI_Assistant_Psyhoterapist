from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
import json
import re
from typing import Any

from assistant_runtime.json_utils import load_json_document


CATEGORY_SHORT_CODES = {
    "crisis": "cri",
    "boundary": "bd",
    "structure": "str",
    "empathy": "emp",
    "open_questions": "oq",
    "closed_questions": "cq",
    "variants": "var",
    "cbt_mi_dbt": "cbt",
    "psychoeducation": "edu",
    "encouragement": "enc",
    "closing": "clo",
    "cultural": "cult",
}

PHRASE_REC_BY_CATEGORY = {
    "crisis": ["f", "n"],
    "boundary": ["f", "n"],
    "empathy": ["w", "n"],
    "open_questions": ["w", "n"],
    "encouragement": ["w", "n"],
}


@dataclass(slots=True)
class ReviewImportReport:
    phrase_count: int = 0
    trigger_count: int = 0
    knowledge_count: int = 0
    changed_files: list[str] | None = None


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_manifest(project_root: Path, lang: str) -> dict[str, Any]:
    return load_json_document(project_root / "manifests" / f"manifest.{lang}.jsonc")


def _category_info(manifest: dict[str, Any]) -> dict[str, dict[str, str | int | bool]]:
    info: dict[str, dict[str, str | int | bool]] = {}
    for item in manifest["category_order"]:
        category = str(item["name"])
        info[category] = {
            "prefix": str(item["prefix"]),
            "filename": str(item["filename"]),
            "short": CATEGORY_SHORT_CODES[category],
            "default_priority": int(item["default_priority"]),
            "requires_clinical_review": bool(item.get("requires_clinical_review", False)),
            "typical_tags": list(item.get("typical_tags", [])),
        }
    return info


def _next_phrase_id(items: list[dict[str, Any]], short_code: str) -> str:
    pattern = re.compile(rf"^{re.escape(short_code)}_(\d+)$")
    max_value = 0
    for item in items:
        match = pattern.match(str(item.get("id", "")))
        if match:
            max_value = max(max_value, int(match.group(1)))
    return f"{short_code}_{max_value + 1:03d}"


def _next_trigger_id(trigger_groups: dict[Path, list[dict[str, Any]]]) -> str:
    pattern = re.compile(r"^pt_tr_(\d+)$")
    max_value = 0
    for items in trigger_groups.values():
        for item in items:
            match = pattern.match(str(item.get("id", "")))
            if match:
                max_value = max(max_value, int(match.group(1)))
    return f"pt_tr_{max_value + 1:03d}"


def _next_knowledge_id(items: list[dict[str, Any]], lang: str) -> str:
    pattern = re.compile(rf"^kb_{re.escape(lang)}_(\d+)$")
    max_value = 0
    for item in items:
        match = pattern.match(str(item.get("id", "")))
        if match:
            max_value = max(max_value, int(match.group(1)))
    return f"kb_{lang}_{max_value + 1:03d}"


def _enabled_in_for_status(status: str, explicit_channels: list[str] | None) -> list[str]:
    if explicit_channels:
        return explicit_channels
    if status == "appr":
        return ["rt", "rv", "tst"]
    return ["rv", "tst"]


def _length_code(text: str) -> str:
    word_count = len(text.split())
    if word_count <= 10:
        return "s"
    if word_count <= 22:
        return "m"
    return "l"


def _phrase_exists(items: list[dict[str, Any]], origin_ref: str, text: str) -> bool:
    for item in items:
        meta = item.get("meta", {})
        if isinstance(meta, dict) and meta.get("origin_ref") == origin_ref:
            return True
        for phrase in item.get("pp", []):
            if phrase.get("txt") == text:
                return True
    return False


def _trigger_exists(items: list[dict[str, Any]], origin_ref: str, text: str) -> bool:
    for item in items:
        meta = item.get("meta", {})
        if isinstance(meta, dict) and meta.get("origin_ref") == origin_ref:
            return True
        if text in item.get("ex", []):
            return True
    return False


def _knowledge_exists(items: list[dict[str, Any]], origin_ref: str, text: str) -> bool:
    for item in items:
        meta = item.get("meta", {})
        if isinstance(meta, dict) and meta.get("origin_ref") == origin_ref:
            return True
        if item.get("text") == text:
            return True
    return False


def _trigger_examples(trigger_text: str, normalized_forms: list[str]) -> list[str]:
    values: list[str] = []
    for candidate in [trigger_text, *normalized_forms]:
        cleaned = str(candidate).strip()
        if cleaned and cleaned not in values:
            values.append(cleaned)
    while len(values) < 3 and values:
        values.append(values[-1])
    return values[:8]


def _regex_pattern(examples: list[str]) -> str:
    parts = [re.escape(example) for example in examples if example]
    return rf"\\b({'|'.join(parts)})\\b"


def _trigger_safety(candidate: dict[str, Any]) -> str:
    risk_flags = set(candidate.get("suggested_risk_flags", []))
    if "crisis" in risk_flags:
        return "hard_handoff"
    if candidate.get("category") in {"crisis", "boundary", "closed_questions"}:
        return "monitor"
    return "none"


def _trigger_fallback(category: str, safety: str) -> str:
    if safety == "hard_handoff":
        return "escalate"
    if category == "open_questions":
        return "ask_clarifying"
    return "use_variant"


def _cost_profile(safety: str) -> dict[str, float | int]:
    if safety == "hard_handoff":
        return {"stt_min": 0.6, "in_tok": 240, "out_tok": 80, "tts_ch": 240}
    if safety == "monitor":
        return {"stt_min": 0.48, "in_tok": 190, "out_tok": 64, "tts_ch": 220}
    return {"stt_min": 0.42, "in_tok": 170, "out_tok": 58, "tts_ch": 205}


def _confidence_profile(safety: str) -> dict[str, float]:
    if safety == "hard_handoff":
        return {"m": 0.72, "r": 0.55}
    if safety == "monitor":
        return {"m": 0.68, "r": 0.5}
    return {"m": 0.65, "r": 0.45}


def import_review_candidate_pack(
    project_root: Path,
    *,
    pack_path: Path,
    lang: str = "hu",
    content_status: str = "rev",
    enabled_in: list[str] | None = None,
    reviewed_by_role: str = "clinical",
    reviewed_at: str | None = None,
    import_phrases: bool = True,
    import_triggers: bool = True,
    import_knowledge: bool = True,
) -> ReviewImportReport:
    manifest = _load_manifest(project_root, lang)
    category_info = _category_info(manifest)
    pack = load_json_document(pack_path)
    reviewed_at = reviewed_at or datetime.now(UTC).date().isoformat()
    channels = _enabled_in_for_status(content_status, enabled_in)
    indexed_chunks = {
        str(item["chunk_id"]): item
        for item in pack.get("indexed_chunks", [])
        if isinstance(item, dict) and "chunk_id" in item
    }

    phrase_files: dict[Path, list[dict[str, Any]]] = {}
    trigger_files: dict[Path, list[dict[str, Any]]] = {}
    changed_files: set[str] = set()
    imported_phrase_ids_by_category: dict[str, list[str]] = {}
    report = ReviewImportReport(changed_files=[])

    if import_phrases:
        for candidate in pack.get("review_candidates", {}).get("phrase_candidates", []):
            category = str(candidate.get("category", "variants"))
            if category not in category_info:
                category = "variants"
            info = category_info[category]
            file_path = project_root / "locales" / lang / str(info["filename"])
            if file_path not in phrase_files:
                phrase_files[file_path] = load_json_document(file_path)
            items = phrase_files[file_path]
            origin_ref = str(candidate.get("candidate_id"))
            draft_text = str(candidate.get("draft_text", "")).strip()
            if not draft_text or _phrase_exists(items, origin_ref, draft_text):
                continue
            phrase_id = _next_phrase_id(items, str(info["short"]))
            imported_phrase_ids_by_category.setdefault(category, []).append(phrase_id)
            priority = int(candidate.get("suggested_priority", info["default_priority"]))
            rec = list(candidate.get("recommended_tones", [])) or PHRASE_REC_BY_CATEGORY.get(category, ["n", "w"])
            if not rec:
                rec = ["n", "w"]
            tags = sorted({str(tag) for tag in candidate.get("tags", []) if str(tag).strip()})
            if not tags:
                typical_tags = info.get("typical_tags", [])
                tags = [str(tag) for tag in typical_tags]
            phrase_item: dict[str, Any] = {
                "id": phrase_id,
                "pri": priority,
                "rec": rec,
                "use": [str(value) for value in candidate.get("allowed_uses", ["c", "t"])] or ["c", "t"],
                "tags": tags,
                "pp": [
                    {
                        "txt": draft_text,
                        "t": "f" if category in {"crisis", "boundary"} else rec[0],
                        "l": _length_code(draft_text),
                    }
                ],
                "meta": {
                    "src": "lit",
                    "status": content_status,
                    "enabled_in": channels,
                    "origin_ref": origin_ref,
                },
            }
            requires_review = bool(info["requires_clinical_review"])
            if requires_review:
                phrase_item["review_required"] = True
                phrase_item["review"] = {
                    "review_status": "approved" if content_status == "appr" else "draft",
                    "reviewed_by_role": reviewed_by_role,
                    "reviewed_at": reviewed_at,
                    "safety_notes": str(candidate.get("rationale", "Imported from approved review pack.")),
                    "evidence_level": "guideline_based" if category in {"crisis", "psychoeducation", "boundary"} else "practice_based",
                }
            items.append(phrase_item)
            report.phrase_count += 1
            changed_files.add(str(file_path))

    if import_triggers:
        for category, info in category_info.items():
            trigger_path = project_root / "locales" / lang / "triggers" / f"{info['prefix']}_{info['short']}_triggers.{lang}.json"
            trigger_files[trigger_path] = load_json_document(trigger_path)
        for candidate in pack.get("review_candidates", {}).get("trigger_candidates", []):
            category = str(candidate.get("category", "variants"))
            if category not in category_info:
                category = "variants"
            info = category_info[category]
            target_path = project_root / "locales" / lang / "triggers" / f"{info['prefix']}_{info['short']}_triggers.{lang}.json"
            items = trigger_files[target_path]
            origin_ref = str(candidate.get("candidate_id"))
            trigger_text = str(candidate.get("trigger_text", "")).strip()
            if not trigger_text or _trigger_exists(items, origin_ref, trigger_text):
                continue
            examples = _trigger_examples(trigger_text, [str(item) for item in candidate.get("normalized_forms", [])])
            phrase_targets = imported_phrase_ids_by_category.get(category)
            if not phrase_targets:
                phrase_file_path = project_root / "locales" / lang / str(info["filename"])
                phrase_items = phrase_files.get(phrase_file_path) or load_json_document(phrase_file_path)
                phrase_targets = [str(item["id"]) for item in phrase_items[:3] if isinstance(item, dict) and item.get("id")]
            if not phrase_targets:
                continue
            safety = _trigger_safety(candidate)
            trigger_item = {
                "id": _next_trigger_id(trigger_files),
                "ex": examples,
                "m": {"t": "regex", "p": _regex_pattern(examples)},
                "tags": [str(tag) for tag in candidate.get("matched_tags", [])] or [str(info["short"])],
                "prio": 1 if safety == "hard_handoff" else 2,
                "safety": safety,
                "cat": str(info["short"]),
                "cand": phrase_targets[:3],
                "fb": _trigger_fallback(category, safety),
                "ct": _confidence_profile(safety),
                "cost": _cost_profile(safety),
                "audit": safety == "hard_handoff",
                "note": str(candidate.get("rationale", "Imported from review pack.")),
                "age": ["adult", "senior"] if safety == "hard_handoff" else ["teen", "adult", "senior"],
                "lit": "low" if safety == "hard_handoff" else "medium",
                "reg": "plain",
                "meta": {
                    "src": "lit",
                    "status": content_status,
                    "enabled_in": channels,
                    "origin_ref": origin_ref,
                },
            }
            items.append(trigger_item)
            report.trigger_count += 1
            changed_files.add(str(target_path))

    if import_knowledge:
        knowledge_path = project_root / "locales" / lang / "mappings" / f"knowledge_snippets.{lang}.json"
        knowledge_items = load_json_document(knowledge_path)
        for candidate in pack.get("knowledge_enrichment", {}).get("knowledge_snippets", []):
            meta = candidate.get("meta", {}) if isinstance(candidate, dict) else {}
            origin_ref = str(meta.get("origin_ref", candidate.get("id", "")))
            text = str(candidate.get("text", "")).strip()
            if not text or _knowledge_exists(knowledge_items, origin_ref, text):
                continue
            chunk = indexed_chunks.get(origin_ref, {})
            risk_flags = set(chunk.get("risk_flags", []))
            knowledge_items.append(
                {
                    "id": _next_knowledge_id(knowledge_items, lang),
                    "text": text,
                    "topics": [str(item) for item in candidate.get("topics", [])],
                    "intents": [str(chunk.get("intent", "support"))],
                    "tags": [str(item) for item in chunk.get("tags", [])],
                    "categories": [str(item) for item in candidate.get("categories", [])],
                    "audience": ["adult", "senior"],
                    "risk_level": "critical" if "crisis" in risk_flags else "general",
                    "allowed_stages": [str(item) for item in candidate.get("allowed_stages", [])] or ["phrase_selection"],
                    "source": "review_pack_import",
                    "meta": {
                        "src": "lit",
                        "status": content_status,
                        "enabled_in": channels,
                        "origin_ref": origin_ref,
                    },
                }
            )
            report.knowledge_count += 1
            changed_files.add(str(knowledge_path))
        if report.knowledge_count:
            _write_json(knowledge_path, knowledge_items)

    for file_path, items in phrase_files.items():
        if str(file_path) in changed_files:
            _write_json(file_path, items)
    for file_path, items in trigger_files.items():
        if str(file_path) in changed_files:
            _write_json(file_path, items)

    report.changed_files = sorted(changed_files)
    return report