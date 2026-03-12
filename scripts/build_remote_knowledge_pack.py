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
from assistant_runtime.ops.remote_document_ingest import download_remote_documents


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download remote knowledge sources and convert them into a local review pack.")
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--url", action="append", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--download-dir", default=str(ROOT / "data" / "runtime_state" / "downloads"))
    parser.add_argument("--bearer-env", default="REMOTE_SOURCE_TOKEN")
    parser.add_argument("--basic-auth-env", default="REMOTE_SOURCE_BASIC_AUTH")
    parser.add_argument("--headers-env", default="REMOTE_SOURCE_HEADERS_JSON")
    parser.add_argument("--cookie-env", default="REMOTE_SOURCE_COOKIE")
    parser.add_argument("--header", action="append", default=[])
    parser.add_argument("--cookie", action="append", default=[])
    parser.add_argument("--actor", default=os.getenv("USERNAME", "unknown"))
    parser.add_argument("--reason", default="remote_external_ingest")
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
    bearer_token = os.getenv(args.bearer_env) if args.bearer_env else None
    basic_auth = os.getenv(args.basic_auth_env) if args.basic_auth_env else None
    extra_headers = _load_headers_from_env(args.headers_env)
    extra_headers.update(_parse_header_entries(args.header))
    cookie_header = _build_cookie_header(args.cookie, args.cookie_env)
    downloads = download_remote_documents(
        args.url,
        output_dir=Path(args.download_dir),
        bearer_token=bearer_token,
        basic_auth=basic_auth,
        cookie_header=cookie_header,
        extra_headers=extra_headers,
    )
    payload = build_external_knowledge_pack(
        args.pack_id,
        document_paths=[item.output_path for item in downloads],
    )
    payload["sources"]["remote_urls"] = [item.url for item in downloads]
    payload["sources"]["download_dir"] = str(Path(args.download_dir))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    AuditLogger(ROOT / "data" / "runtime_state" / "audit").append_event(
        stream="content",
        event_type="remote_knowledge_pack_generated",
        actor={"role": "operator", "id": args.actor},
        subject={"pack_id": args.pack_id, "output_path": str(output_path)},
        payload={
            "reason": args.reason,
            "remote_urls": args.url,
            "downloaded_files": [str(item.output_path) for item in downloads],
            "header_names": sorted(extra_headers),
            "cookie_configured": bool(cookie_header),
            "knowledge_snippet_count": len(payload["knowledge_enrichment"]["knowledge_snippets"]),
        },
    )
    print(f"Wrote remote knowledge pack to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())