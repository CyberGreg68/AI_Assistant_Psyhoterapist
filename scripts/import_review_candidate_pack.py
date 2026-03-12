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
from assistant_runtime.ops.review_pack_importer import import_review_candidate_pack


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import an approved review candidate pack into developer phrase, trigger, and knowledge kits.")
    parser.add_argument("--pack", required=True)
    parser.add_argument("--lang", default="hu")
    parser.add_argument("--content-status", default="rev", choices=["rev", "appr", "test", "hold", "sugg"])
    parser.add_argument("--enable-channel", action="append", default=[])
    parser.add_argument("--reviewed-by-role", default="clinical")
    parser.add_argument("--reviewed-at")
    parser.add_argument("--skip-phrases", action="store_true")
    parser.add_argument("--skip-triggers", action="store_true")
    parser.add_argument("--skip-knowledge", action="store_true")
    parser.add_argument("--actor", default=os.getenv("USERNAME", "unknown"))
    parser.add_argument("--reason", default="review_pack_import")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    report = import_review_candidate_pack(
        ROOT,
        pack_path=Path(args.pack),
        lang=args.lang,
        content_status=args.content_status,
        enabled_in=args.enable_channel or None,
        reviewed_by_role=args.reviewed_by_role,
        reviewed_at=args.reviewed_at,
        import_phrases=not args.skip_phrases,
        import_triggers=not args.skip_triggers,
        import_knowledge=not args.skip_knowledge,
    )
    AuditLogger(ROOT / "data" / "runtime_state" / "audit").append_event(
        stream="content",
        event_type="review_candidate_pack_imported",
        actor={"role": "operator", "id": args.actor},
        subject={"pack_path": str(Path(args.pack).resolve())},
        payload={
            "reason": args.reason,
            "lang": args.lang,
            "phrase_count": report.phrase_count,
            "trigger_count": report.trigger_count,
            "knowledge_count": report.knowledge_count,
            "changed_files": report.changed_files or [],
        },
    )
    print(
        f"Imported phrases={report.phrase_count}, triggers={report.trigger_count}, "
        f"knowledge={report.knowledge_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())