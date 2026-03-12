from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.json_utils import load_json_document


DEFAULT_META = {
    "src": "dev",
    "status": "rev",
    "enabled_in": ["rt", "rv", "tst"],
}


def _write_json(file_path: Path, payload: object) -> None:
    file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _backfill_items(file_path: Path) -> int:
    payload = load_json_document(file_path)
    if not isinstance(payload, list):
        return 0
    updated = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        meta = item.get("meta")
        if not isinstance(meta, dict):
            item["meta"] = dict(DEFAULT_META)
            updated += 1
            continue
        changed = False
        for key, value in DEFAULT_META.items():
            if key not in meta:
                meta[key] = list(value) if isinstance(value, list) else value
                changed = True
        if changed:
            updated += 1
    if updated:
        _write_json(file_path, payload)
    return updated


def main() -> int:
    updated_files = 0
    updated_items = 0
    for phrase_path in (ROOT / "locales").glob("*/phrases/*.jsonc"):
        count = _backfill_items(phrase_path)
        if count:
            updated_files += 1
            updated_items += count
    for trigger_path in (ROOT / "locales").glob("*/triggers/*_triggers.*.json"):
        count = _backfill_items(trigger_path)
        if count:
            updated_files += 1
            updated_items += count
    for knowledge_path in (ROOT / "locales").glob("*/mappings/knowledge_snippets.*.json"):
        count = _backfill_items(knowledge_path)
        if count:
            updated_files += 1
            updated_items += count
    print(f"Backfilled content metadata in {updated_files} files, {updated_items} items.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())