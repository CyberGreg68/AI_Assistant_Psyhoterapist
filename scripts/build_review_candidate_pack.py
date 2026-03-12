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
from assistant_runtime.ops.review_pack_builder import build_review_candidate_pack


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an offline review pack from clinician-provided text and audio source folders."
    )
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--source", action="append", required=True)
    parser.add_argument("--glob", action="append", default=[])
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--config-dir", default=str(ROOT / "config"))
    parser.add_argument("--actor", default=os.getenv("USERNAME", "unknown"))
    parser.add_argument("--reason", default="offline_review_candidate_ingest")
    parser.add_argument("--max-snippets", type=int, default=40)
    parser.add_argument("--max-phrase-candidates", type=int, default=48)
    parser.add_argument("--max-trigger-candidates", type=int, default=48)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    payload = build_review_candidate_pack(
        args.pack_id,
        source_paths=[Path(item) for item in args.source],
        config_dir=Path(args.config_dir),
        recursive=not args.no_recursive,
        include_globs=args.glob or None,
        max_snippets=max(1, args.max_snippets),
        max_phrase_candidates=max(1, args.max_phrase_candidates),
        max_trigger_candidates=max(1, args.max_trigger_candidates),
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    AuditLogger(ROOT / "data" / "runtime_state" / "audit").append_event(
        stream="content",
        event_type="offline_review_candidate_pack_generated",
        actor={"role": "operator", "id": args.actor},
        subject={"pack_id": args.pack_id, "output_path": str(output_path)},
        payload={
            "reason": args.reason,
            "source_paths": [str(item) for item in args.source],
            "resolved_paths": payload["sources"]["resolved_paths"],
            "knowledge_snippet_count": len(payload["knowledge_enrichment"]["knowledge_snippets"]),
            "phrase_candidate_count": len(payload["review_candidates"]["phrase_candidates"]),
            "trigger_candidate_count": len(payload["review_candidates"]["trigger_candidates"]),
        },
    )
    print(f"Wrote review candidate pack to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())