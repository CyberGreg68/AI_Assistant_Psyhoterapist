from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.audit_logger import AuditLogger
from assistant_runtime.ops.document_ingest import build_external_knowledge_pack
from assistant_runtime.ops.document_ingest import collect_local_document_paths


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a review pack from local external knowledge documents.")
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--glob", action="append", default=[])
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--actor", default=os.getenv("USERNAME", "unknown"))
    parser.add_argument("--reason", default="local_external_ingest")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    document_paths = collect_local_document_paths(
        [Path(item) for item in args.source],
        recursive=not args.no_recursive,
        include_globs=args.glob or None,
    )
    if not document_paths:
        raise SystemExit("No supported local documents were found for ingest.")
    payload = build_external_knowledge_pack(args.pack_id, document_paths=document_paths)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    AuditLogger(ROOT / "data" / "runtime_state" / "audit").append_event(
        stream="content",
        event_type="external_knowledge_pack_generated",
        actor={
            "role": "operator",
            "id": args.actor,
        },
        subject={
            "pack_id": args.pack_id,
            "output_path": str(output_path),
        },
        payload={
            "reason": args.reason,
            "source_paths": [str(item) for item in args.source],
            "resolved_document_paths": [str(item) for item in document_paths],
            "knowledge_snippet_count": len(payload["knowledge_enrichment"]["knowledge_snippets"]),
        },
    )
    print(f"Wrote external knowledge pack to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())