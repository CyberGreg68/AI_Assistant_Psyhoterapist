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
from assistant_runtime.ops.literature_batch_builder import build_literature_batch


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a dated literature batch with manifest, documents, chunks, candidates, and rule hints."
    )
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--lang", default="hu")
    parser.add_argument("--glob", action="append", default=[])
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--download-dir")
    parser.add_argument("--config-dir", default=str(ROOT / "config"))
    parser.add_argument("--bearer-env", default="REMOTE_SOURCE_TOKEN")
    parser.add_argument("--basic-auth-env", default="REMOTE_SOURCE_BASIC_AUTH")
    parser.add_argument("--headers-env", default="REMOTE_SOURCE_HEADERS_JSON")
    parser.add_argument("--cookie-env", default="REMOTE_SOURCE_COOKIE")
    parser.add_argument("--header", action="append", default=[])
    parser.add_argument("--cookie", action="append", default=[])
    parser.add_argument("--actor", default=os.getenv("USERNAME", "unknown"))
    parser.add_argument("--reason", default="literature_batch_build")
    parser.add_argument("--max-snippets", type=int, default=40)
    parser.add_argument("--max-phrase-candidates", type=int, default=48)
    parser.add_argument("--max-trigger-candidates", type=int, default=48)
    return parser.parse_args()


def _parse_header_entries(items: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in items:
        if ":" not in item:
            raise ValueError(f"Invalid header format: {item}. Use 'Name: Value'.")
        name, value = item.split(":", 1)
        headers[name.strip()] = value.strip()
    return headers


def _load_headers_from_env(env_var: str | None) -> dict[str, str]:
    if not env_var:
        return {}
    raw_value = os.getenv(env_var)
    if not raw_value:
        return {}
    payload = json.loads(raw_value)
    if not isinstance(payload, dict):
        raise ValueError(f"Environment variable {env_var} must contain a JSON object.")
    return {str(key): str(value) for key, value in payload.items()}


def _build_cookie_header(cli_cookies: list[str], env_var: str | None) -> str | None:
    cookie_entries = [item.strip() for item in cli_cookies if item.strip()]
    env_cookie = os.getenv(env_var) if env_var else None
    if env_cookie:
        cookie_entries.insert(0, env_cookie.strip())
    return "; ".join(cookie_entries) or None


def main() -> int:
    args = _parse_args()
    extra_headers = _load_headers_from_env(args.headers_env)
    extra_headers.update(_parse_header_entries(args.header))
    payload = build_literature_batch(
        args.batch_id,
        output_dir=Path(args.output_dir),
        source_paths=[Path(item) for item in args.source],
        urls=args.url,
        lang=args.lang,
        download_dir=Path(args.download_dir) if args.download_dir else None,
        config_dir=Path(args.config_dir),
        recursive=not args.no_recursive,
        include_globs=args.glob or None,
        max_snippets=max(1, args.max_snippets),
        max_phrase_candidates=max(1, args.max_phrase_candidates),
        max_trigger_candidates=max(1, args.max_trigger_candidates),
        bearer_token=os.getenv(args.bearer_env) if args.bearer_env else None,
        basic_auth=os.getenv(args.basic_auth_env) if args.basic_auth_env else None,
        cookie_header=_build_cookie_header(args.cookie, args.cookie_env),
        extra_headers=extra_headers,
    )
    AuditLogger(ROOT / "data" / "runtime_state" / "audit").append_event(
        stream="content",
        event_type="literature_batch_generated",
        actor={"role": "operator", "id": args.actor},
        subject={"batch_id": args.batch_id, "output_dir": str(Path(args.output_dir))},
        payload={
            "reason": args.reason,
            "source_paths": args.source,
            "urls": args.url,
            "counts": payload["manifest"]["counts"],
        },
    )
    print(f"Wrote literature batch to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())