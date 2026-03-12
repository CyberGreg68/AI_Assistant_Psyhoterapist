from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib import error as urllib_error

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.adapters.factory import build_llm_adapter
from assistant_runtime.adapters.llm_adapter import GenerationRequest
from assistant_runtime.config.loader import load_llm_endpoint
from assistant_runtime.env_loader import load_local_env
from assistant_runtime.serialization import normalize_for_json


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test a direct LLM connection using the configured online endpoint.")
    parser.add_argument("--model", help="Actual remote model id, for example gpt-4o-mini or gpt-5-mini. Defaults to the configured endpoint default_model.")
    parser.add_argument("--prompt", default="Adj egy rovid, nyugodt magyar valaszt arra, hogy: szorongok, mit tegyek?", help="Prompt to send.")
    parser.add_argument("--max-tokens", type=int, default=200)
    parser.add_argument("--lang", default="hu")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    load_local_env(ROOT)
    config_dir = ROOT / "config"
    endpoint = load_llm_endpoint(config_dir)
    model = args.model or endpoint.default_model

    if not model:
        raise SystemExit("No model was provided and config/endpoints.json does not define llm.default_model.")

    token = os.getenv(endpoint.auth_env_var)
    if not token:
        raise SystemExit(
            f"Missing environment variable: {endpoint.auth_env_var}. Create a GitHub token with models:read permission and export it first."
        )

    adapter = build_llm_adapter(config_dir)
    try:
        response = adapter.generate(
            GenerationRequest(
                conversation_id="llm-connection-test",
                lang=args.lang,
                prompt=args.prompt,
                system_prompt=endpoint.system_prompt,
                model=model,
                max_tokens=args.max_tokens,
            )
        )
    except urllib_error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        print(
            json.dumps(
                {
                    "provider": endpoint.provider,
                    "endpoint": endpoint.url,
                    "requested_model": model,
                    "status": exc.code,
                    "error": error_body,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(
        json.dumps(
            normalize_for_json(
                {
                    "provider": endpoint.provider,
                    "endpoint": endpoint.url,
                    "requested_model": model,
                    "response_model": response.model,
                    "finish_reason": response.finish_reason,
                    "text": response.text,
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())