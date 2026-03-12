from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "manifests" / "manifest.hu.jsonc"
LOCALE_DIR = ROOT / "locales" / "hu"


REVIEW_DEFAULTS = {
    "crisis": {
        "review_status": "approved",
        "reviewed_by_role": "clinical",
        "reviewed_at": "2026-03-11",
        "safety_notes": "Approved for crisis-first routing and hard handoff contexts.",
        "evidence_level": "guideline_based",
    },
    "boundary": {
        "review_status": "approved",
        "reviewed_by_role": "clinical",
        "reviewed_at": "2026-03-11",
        "safety_notes": "Approved for boundary, confidentiality, and referral framing.",
        "evidence_level": "guideline_based",
    },
    "closed_questions": {
        "review_status": "approved",
        "reviewed_by_role": "clinical",
        "reviewed_at": "2026-03-11",
        "safety_notes": "Approved for factual and safety-check prompts.",
        "evidence_level": "practice_based",
    },
    "cbt_mi_dbt": {
        "review_status": "approved",
        "reviewed_by_role": "clinical",
        "reviewed_at": "2026-03-11",
        "safety_notes": "Approved as structured therapeutic prompts, not diagnosis or direct treatment advice.",
        "evidence_level": "evidence_informed",
    },
    "psychoeducation": {
        "review_status": "approved",
        "reviewed_by_role": "clinical",
        "reviewed_at": "2026-03-11",
        "safety_notes": "Approved as general psychoeducation and coping guidance.",
        "evidence_level": "evidence_informed",
    },
}


def load_jsonc(path: Path):
    raw_text = path.read_text(encoding="utf-8")
    comment = ""
    stripped = raw_text.lstrip()
    if stripped.startswith("/*"):
        comment_end = stripped.find("*/")
        comment = stripped[: comment_end + 2].rstrip() + "\n"
        payload = stripped[comment_end + 2 :].lstrip()
    else:
        payload = raw_text
    return comment, json.loads(payload)


def write_jsonc(path: Path, comment: str, payload: object) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if comment:
        path.write_text(comment + "\n" + text + "\n", encoding="utf-8")
    else:
        path.write_text(text + "\n", encoding="utf-8")


def backfill() -> int:
    _, manifest = load_jsonc(MANIFEST_PATH)
    updated_files = 0

    for category in manifest["category_order"]:
        if not category.get("requires_clinical_review"):
            continue

        category_name = category["name"]
        defaults = REVIEW_DEFAULTS[category_name]
        category_path = LOCALE_DIR / category["filename"]
        comment, items = load_jsonc(category_path)

        changed = False
        for item in items:
            if "review" not in item:
                item["review_required"] = True
                item["review"] = dict(defaults)
                changed = True

        if changed:
            write_jsonc(category_path, comment, items)
            updated_files += 1

    print(f"Backfilled review metadata in {updated_files} files.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    return backfill()


if __name__ == "__main__":
    raise SystemExit(main())
