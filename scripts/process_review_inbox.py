from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.ops.review_inbox import process_review_inbox
from assistant_runtime.ops.review_inbox import watch_review_inbox


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process a clinician source inbox into offline review candidate packs.")
    parser.add_argument("--inbox", required=True)
    parser.add_argument("--output-dir", default=str(ROOT / "data" / "runtime_state" / "review_packs"))
    parser.add_argument("--state-path", default=str(ROOT / "data" / "runtime_state" / "review_inbox_state.json"))
    parser.add_argument("--archive-dir")
    parser.add_argument("--config-dir", default=str(ROOT / "config"))
    parser.add_argument("--pack-prefix", default="review_batch")
    parser.add_argument("--watch-seconds", type=int, default=0)
    parser.add_argument("--actor", default=os.getenv("USERNAME", "unknown"))
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    inbox_dir = Path(args.inbox)
    output_dir = Path(args.output_dir)
    state_path = Path(args.state_path)
    archive_dir = Path(args.archive_dir) if args.archive_dir else None
    config_dir = Path(args.config_dir) if args.config_dir else None
    if args.watch_seconds > 0:
        watch_review_inbox(
            ROOT,
            inbox_dir=inbox_dir,
            output_dir=output_dir,
            state_path=state_path,
            archive_dir=archive_dir,
            config_dir=config_dir,
            pack_prefix=args.pack_prefix,
            actor=args.actor,
            watch_seconds=args.watch_seconds,
        )
        return 0

    results = process_review_inbox(
        ROOT,
        inbox_dir=inbox_dir,
        output_dir=output_dir,
        state_path=state_path,
        archive_dir=archive_dir,
        config_dir=config_dir,
        pack_prefix=args.pack_prefix,
        actor=args.actor,
    )
    print(f"Processed {len(results)} review inbox batch(es).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())