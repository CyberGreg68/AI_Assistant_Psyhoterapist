from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.audit_logger import AuditLogger
from assistant_runtime.core.runtime_bundle import load_bundle_from_source
from assistant_runtime.core.runtime_bundle import save_published_bundle


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a runtime bundle artifact from the current manifest and locale source tree.")
    parser.add_argument("--lang", default="hu")
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "runtime_state" / "published"),
        help="Output file path or directory for the published bundle.",
    )
    parser.add_argument("--actor", default=os.getenv("USERNAME", "unknown"))
    parser.add_argument("--reason", default="publish_runtime_bundle")
    return parser.parse_args()


def _resolve_output_path(output_value: str, lang: str) -> Path:
    output_path = Path(output_value)
    if output_path.suffix:
        return output_path
    return output_path / f"runtime_bundle.{lang}.json"


def main() -> int:
    args = _parse_args()
    bundle = load_bundle_from_source(ROOT, args.lang)
    output_path = _resolve_output_path(args.output, args.lang)
    save_published_bundle(bundle, output_path, source_label="published_runtime_bundle")
    AuditLogger(ROOT / "data" / "runtime_state" / "audit").append_event(
        stream="content",
        event_type="runtime_bundle_published",
        actor={"role": "operator", "id": args.actor},
        subject={"lang": args.lang, "output_path": str(output_path)},
        payload={
            "reason": args.reason,
            "phrase_item_count": sum(len(items) for items in bundle.categories.values()),
            "trigger_item_count": sum(len(items) for items in bundle.triggers.values()),
            "knowledge_snippet_count": len(bundle.knowledge_snippets or []),
        },
    )
    print(f"Published runtime bundle to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())