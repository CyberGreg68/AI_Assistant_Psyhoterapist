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

DEFAULTS = {
    "cri": {"reg": "plain", "lit": "low", "age": ["adult", "senior"]},
    "bd": {"reg": "plain", "lit": "medium", "age": ["adult", "senior"]},
    "str": {"reg": "plain", "lit": "medium", "age": ["teen", "adult", "senior"]},
    "emp": {"reg": "conversational", "lit": "medium", "age": ["teen", "adult", "senior"]},
    "oq": {"reg": "plain", "lit": "medium", "age": ["teen", "adult", "senior"]},
    "cq": {"reg": "plain", "lit": "low", "age": ["adult", "senior"]},
    "var": {"reg": "plain", "lit": "low", "age": ["child", "teen", "adult", "senior"]},
    "cbt": {"reg": "conversational", "lit": "medium", "age": ["teen", "adult"]},
    "edu": {"reg": "plain", "lit": "low", "age": ["adult", "senior"]},
    "enc": {"reg": "conversational", "lit": "low", "age": ["teen", "adult", "senior"]},
    "clo": {"reg": "plain", "lit": "low", "age": ["teen", "adult", "senior"]},
    "cult": {"reg": "conversational", "lit": "medium", "age": ["teen", "adult", "senior"]},
}


def infer_prefix(item: dict) -> str:
    item_id = str(item.get("id", ""))
    return item_id.split("_", 1)[0]


def extract_leading_block_comment(raw_text: str) -> str:
    stripped = raw_text.lstrip()
    if not stripped.startswith("/*"):
        return ""
    comment_end = stripped.find("*/")
    if comment_end == -1:
        return ""
    return stripped[: comment_end + 2].strip() + "\n\n"


def main() -> int:
    touched = 0
    for path in sorted(PHRASE_DIR.glob("*.jsonc")):
        raw_text = path.read_text(encoding="utf-8")
        leading_comment = extract_leading_block_comment(raw_text)
        items = load_json_document(path)
        if not isinstance(items, list):
            continue
        changed = False
        for item in items:
            prefix = infer_prefix(item)
            defaults = DEFAULTS.get(prefix)
            if not defaults:
                continue
            for key in ("reg", "lit", "age"):
                if key not in item:
                    item[key] = defaults[key]
                    changed = True
            for phrase in item.get("pp", []):
                for key in ("reg", "lit", "age"):
                    if key not in phrase:
                        phrase[key] = item[key]
                        changed = True
        if changed:
            path.write_text(
                leading_comment + json.dumps(items, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            touched += 1

    print(json.dumps({"files_updated": touched}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())