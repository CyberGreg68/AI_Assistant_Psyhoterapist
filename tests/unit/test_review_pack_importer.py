import json
from pathlib import Path

from assistant_runtime.ops.review_pack_importer import import_review_candidate_pack


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_import_review_candidate_pack_updates_phrase_trigger_and_knowledge_files(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "manifests"
    locales_dir = tmp_path / "locales" / "hu"
    phrases_dir = locales_dir / "phrases"
    triggers_dir = locales_dir / "triggers"
    mappings_dir = locales_dir / "mappings"
    manifest_dir.mkdir(parents=True)
    phrases_dir.mkdir(parents=True)
    triggers_dir.mkdir(parents=True)
    mappings_dir.mkdir(parents=True)

    _write_json(
        manifest_dir / "manifest.hu.jsonc",
        {
            "category_order": [
                {"prefix": "01", "name": "crisis", "default_priority": 1, "filename": "phrases/01_cri_phrases.hu.jsonc", "requires_clinical_review": True},
                {"prefix": "04", "name": "empathy", "default_priority": 1, "filename": "phrases/04_emp_phrases.hu.jsonc", "requires_clinical_review": False},
                {"prefix": "07", "name": "variants", "default_priority": 2, "filename": "phrases/07_var_phrases.hu.jsonc", "requires_clinical_review": False},
            ]
        },
    )
    _write_json(
        phrases_dir / "01_cri_phrases.hu.jsonc",
        [{"id": "cri_001", "pri": 1, "rec": ["f"], "use": ["c"], "tags": ["cri"], "pp": [{"txt": "Existing crisis", "t": "f", "l": "s"}]}],
    )
    _write_json(
        phrases_dir / "04_emp_phrases.hu.jsonc",
        [{"id": "emp_001", "pri": 1, "rec": ["w"], "use": ["c"], "tags": ["emp"], "pp": [{"txt": "Existing empathy", "t": "w", "l": "s"}]}],
    )
    _write_json(
        phrases_dir / "07_var_phrases.hu.jsonc",
        [{"id": "var_001", "pri": 2, "rec": ["n"], "use": ["c"], "tags": ["var"], "pp": [{"txt": "Existing variant", "t": "n", "l": "s"}]}],
    )
    _write_json(
        triggers_dir / "01_cri_triggers.hu.json",
        [{"id": "pt_tr_001", "ex": ["meghalok", "nem bírom", "vége"], "m": {"t": "regex", "p": "\\b(meghalok|nem bírom|vége)\\b"}, "tags": ["cri"], "prio": 1, "safety": "hard_handoff", "cat": "cri", "cand": ["cri_001"], "fb": "escalate", "ct": {"m": 0.72, "r": 0.55}, "cost": {"stt_min": 0.6, "in_tok": 240, "out_tok": 80, "tts_ch": 240}, "audit": True}],
    )
    _write_json(
        triggers_dir / "04_emp_triggers.hu.json",
        [{"id": "pt_tr_002", "ex": ["szomorú vagyok", "egyedül vagyok", "félek"], "m": {"t": "regex", "p": "\\b(szomorú vagyok|egyedül vagyok|félek)\\b"}, "tags": ["emp"], "prio": 2, "safety": "monitor", "cat": "emp", "cand": ["emp_001"], "fb": "use_variant", "ct": {"m": 0.68, "r": 0.5}, "cost": {"stt_min": 0.48, "in_tok": 190, "out_tok": 64, "tts_ch": 220}, "audit": False}],
    )
    _write_json(triggers_dir / "07_var_triggers.hu.json", [])
    _write_json(mappings_dir / "knowledge_snippets.hu.json", [])

    pack_path = tmp_path / "review_pack.json"
    _write_json(
        pack_path,
        {
            "indexed_chunks": [
                {"chunk_id": "chunk_001", "intent": "emotional_support", "tags": ["emp"], "risk_flags": [], "category_hint": "empathy"},
                {"chunk_id": "chunk_002", "intent": "support", "tags": ["cri", "saf"], "risk_flags": ["crisis"], "category_hint": "crisis"},
            ],
            "knowledge_enrichment": {
                "knowledge_snippets": [
                    {"id": "review_demo_kb_001", "text": "Validating distress helps before deeper work.", "topics": ["validalas"], "categories": ["empathy"], "allowed_stages": ["phrase_selection"], "meta": {"origin_ref": "chunk_001"}}
                ]
            },
            "review_candidates": {
                "phrase_candidates": [
                    {"candidate_id": "phr_cand_001", "category": "empathy", "draft_text": "Ez most nagyon nehéz lehet neked.", "tags": ["emp"], "allowed_uses": ["c", "t"], "suggested_priority": 2, "rationale": "Supportive empathy phrase."}
                ],
                "trigger_candidates": [
                    {"candidate_id": "trg_cand_001", "category": "crisis", "trigger_text": "Nem akarok élni", "normalized_forms": ["nem akarok elni"], "matched_tags": ["cri", "saf"], "suggested_risk_flags": ["crisis"], "rationale": "Crisis utterance."}
                ],
                "rule_hints": [],
            },
        },
    )

    report = import_review_candidate_pack(tmp_path, pack_path=pack_path, lang="hu", content_status="rev")

    assert report.phrase_count == 1
    assert report.trigger_count == 1
    assert report.knowledge_count == 1

    empathy_items = json.loads((phrases_dir / "04_emp_phrases.hu.jsonc").read_text(encoding="utf-8"))
    assert any(item.get("meta", {}).get("origin_ref") == "phr_cand_001" for item in empathy_items)
    crisis_triggers = json.loads((triggers_dir / "01_cri_triggers.hu.json").read_text(encoding="utf-8"))
    assert any(item.get("meta", {}).get("origin_ref") == "trg_cand_001" for item in crisis_triggers)
    knowledge_items = json.loads((mappings_dir / "knowledge_snippets.hu.json").read_text(encoding="utf-8"))
    assert any(item.get("meta", {}).get("origin_ref") == "chunk_001" for item in knowledge_items)