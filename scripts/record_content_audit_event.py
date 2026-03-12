from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.audit_logger import AuditLogger
from assistant_runtime.content_metadata import content_meta
from assistant_runtime.json_utils import load_json_document


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record an audited content action for an inserted, modified, or approved runtime item.")
    parser.add_argument("--action", required=True, choices=["insert", "modify", "approve", "hold", "reject", "review"])
    parser.add_argument("--file", required=True)
    parser.add_argument("--item-id", required=True)
    parser.add_argument("--actor", default=os.getenv("USERNAME", "unknown"))
    parser.add_argument("--role", default="operator")
    parser.add_argument("--reason", required=True)
    parser.add_argument("--notes", default="")
    return parser.parse_args()


def _file_hash(file_path: Path) -> str:
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def _item_snapshot(file_path: Path, item_id: str) -> dict[str, object] | None:
    payload = load_json_document(file_path)
    if not isinstance(payload, list):
        return None
    for item in payload:
        if isinstance(item, dict) and str(item.get("id")) == item_id:
            return {
                "id": item_id,
                "tags": item.get("tags", []),
                "content_meta": content_meta(item, default_source=str(item.get("source", "dev"))),
            }
    return None


def main() -> int:
    args = _parse_args()
    file_path = Path(args.file)
    if not file_path.is_absolute():
        file_path = ROOT / file_path
    if not file_path.exists():
        raise FileNotFoundError(file_path)

    snapshot = _item_snapshot(file_path, args.item_id)
    AuditLogger(ROOT / "data" / "runtime_state" / "audit").append_event(
        stream="content",
        event_type=f"content_{args.action}",
        actor={
            "role": args.role,
            "id": args.actor,
        },
        subject={
            "file": str(file_path.relative_to(ROOT)),
            "item_id": args.item_id,
        },
        payload={
            "reason": args.reason,
            "notes": args.notes,
            "file_hash": _file_hash(file_path),
            "item_snapshot": snapshot,
        },
    )
    print(f"Recorded {args.action} audit event for {args.item_id} in {file_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())