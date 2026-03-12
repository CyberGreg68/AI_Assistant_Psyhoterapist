from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.json_utils import load_json_document

PHRASE_DIR = ROOT / "locales" / "hu" / "phrases"
TRIGGER_DIR = ROOT / "locales" / "hu" / "triggers"


def summarize_phrase_file(path: Path) -> dict[str, int]:
    items = load_json_document(path)
    total = len(items)
    with_item_metadata = sum(1 for item in items if all(key in item for key in ("reg", "lit", "age")))
    total_variants = sum(len(item.get("pp", [])) for item in items)
    with_variant_metadata = sum(
        1
        for item in items
        for phrase in item.get("pp", [])
        if all(key in phrase for key in ("reg", "lit", "age"))
    )
    return {
        "items": total,
        "items_with_style_metadata": with_item_metadata,
        "variants": total_variants,
        "variants_with_style_metadata": with_variant_metadata,
    }


def summarize_trigger_file(path: Path) -> dict[str, int]:
    items = load_json_document(path)
    total = len(items)
    with_trigger_metadata = sum(1 for item in items if any(key in item for key in ("reg", "lit", "age", "persona", "pref")))
    return {"triggers": total, "triggers_with_profile_metadata": with_trigger_metadata}


def main() -> int:
    phrase_summary = {
        path.name: summarize_phrase_file(path)
        for path in sorted(PHRASE_DIR.glob("*.jsonc"))
    }
    trigger_summary = {
        path.name: summarize_trigger_file(path)
        for path in sorted(TRIGGER_DIR.glob("*_triggers.hu.json"))
    }
    report = {"phrases": phrase_summary, "triggers": trigger_summary}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())