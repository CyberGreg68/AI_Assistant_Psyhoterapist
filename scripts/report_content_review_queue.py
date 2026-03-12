from __future__ import annotations

import json
import sys
from datetime import datetime, UTC
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.content_metadata import content_meta
from assistant_runtime.json_utils import load_json_document


def _collect_phrase_items(language_dir: Path) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for phrase_path in sorted((language_dir / "phrases").glob("*.jsonc")):
        for entry in load_json_document(phrase_path):
            meta = content_meta(entry)
            if meta["status"] == "appr":
                continue
            items.append(
                {
                    "type": "phrase",
                    "file": str(phrase_path.relative_to(ROOT)),
                    "id": entry.get("id"),
                    "tags": entry.get("tags", []),
                    "meta": meta,
                }
            )
    return items


def _collect_trigger_items(language_dir: Path, lang: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    trigger_dir = language_dir / "triggers"
    for trigger_path in sorted(trigger_dir.glob(f"*_triggers.{lang}.json")):
        for entry in load_json_document(trigger_path):
            meta = content_meta(entry)
            if meta["status"] == "appr":
                continue
            items.append(
                {
                    "type": "trigger",
                    "file": str(trigger_path.relative_to(ROOT)),
                    "id": entry.get("id"),
                    "tags": entry.get("tags", []),
                    "meta": meta,
                }
            )
    return items


def _collect_knowledge_items(language_dir: Path, lang: str) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    knowledge_path = language_dir / "mappings" / f"knowledge_snippets.{lang}.json"
    if not knowledge_path.exists():
        return items
    for entry in load_json_document(knowledge_path):
        meta = content_meta(entry, default_source=str(entry.get("source", "dev")))
        if meta["status"] == "appr":
            continue
        items.append(
            {
                "type": "knowledge",
                "file": str(knowledge_path.relative_to(ROOT)),
                "id": entry.get("id"),
                "topics": entry.get("topics", []),
                "meta": meta,
            }
        )
    return items


def main() -> int:
    queue: dict[str, list[dict[str, object]]] = {}
    for language_dir in sorted((ROOT / "locales").iterdir()):
        if not language_dir.is_dir():
            continue
        lang = language_dir.name
        pending = []
        pending.extend(_collect_phrase_items(language_dir))
        pending.extend(_collect_trigger_items(language_dir, lang))
        pending.extend(_collect_knowledge_items(language_dir, lang))
        queue[lang] = pending

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "pending_by_language": queue,
        "pending_count": sum(len(items) for items in queue.values()),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())