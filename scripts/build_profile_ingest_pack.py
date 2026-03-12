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

from assistant_runtime.profile_ingest import build_profile_ingest_pack
from assistant_runtime.audit_logger import AuditLogger


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a clinician-profile ingest review pack from summaries, transcripts, and audio references.")
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--summary", action="append", default=[])
    parser.add_argument("--transcript", action="append", default=[])
    parser.add_argument("--audio", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--actor", default=os.getenv("USERNAME", "unknown"))
    parser.add_argument("--reason", default="scheduled_ingest")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    payload = build_profile_ingest_pack(
        args.profile_id,
        summary_files=[Path(item) for item in args.summary],
        transcript_files=[Path(item) for item in args.transcript],
        audio_files=[Path(item) for item in args.audio],
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    AuditLogger(ROOT / "data" / "runtime_state" / "audit").append_event(
        stream="content",
        event_type="ingest_pack_generated",
        actor={
            "role": "operator",
            "id": args.actor,
        },
        subject={
            "profile_id": args.profile_id,
            "output_path": str(output_path),
        },
        payload={
            "reason": args.reason,
            "summary_files": [str(item) for item in args.summary],
            "transcript_files": [str(item) for item in args.transcript],
            "audio_files": [str(item) for item in args.audio],
            "generated_counts": {
                "phrase_candidates": len(payload["profile_enrichment"]["phrase_candidates"]),
                "trigger_candidates": len(payload["profile_enrichment"]["trigger_candidates"]),
                "knowledge_snippets": len(payload["profile_enrichment"]["knowledge_snippets"]),
                "voice_seed_manifest": len(payload["profile_enrichment"]["voice_seed_manifest"]),
            },
        },
    )
    print(f"Wrote ingest pack to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())