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

from assistant_runtime.adapters.factory import build_llm_adapter
from assistant_runtime.adapters.llm_adapter import GenerationRequest
from assistant_runtime.config.loader import load_llm_endpoint
from assistant_runtime.env_loader import load_local_env
from assistant_runtime.serialization import normalize_for_json


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare multiple remote models sequentially against the same prompt.")
    parser.add_argument("--model", action="append", required=True, help="Remote model id to test. Repeat for multiple models.")
    parser.add_argument("--prompt", default="Adj egy rovid, nyugodt magyar valaszt arra, hogy: szorongok, mit tegyek?", help="Prompt to send to every model.")
    parser.add_argument("--max-tokens", type=int, default=200)
    parser.add_argument("--lang", default="hu")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    load_local_env(ROOT)
    config_dir = ROOT / "config"
    endpoint = load_llm_endpoint(config_dir)

    token = os.getenv(endpoint.auth_env_var)
    if not token:
        raise SystemExit(
            f"Missing environment variable: {endpoint.auth_env_var}. Create a GitHub token with models:read permission and export it first."
        )

    adapter = build_llm_adapter(config_dir)
    comparisons: list[dict[str, object]] = []
    for model in args.model:
        response = adapter.generate(
            GenerationRequest(
                conversation_id=f"llm-compare-{model}",
                lang=args.lang,
                prompt=args.prompt,
                system_prompt=endpoint.system_prompt,
                model=model,
                max_tokens=args.max_tokens,
            )
        )
        comparisons.append(
            {
                "requested_model": model,
                "response_model": response.model,
                "finish_reason": response.finish_reason,
                "text": response.text,
            }
        )

    print(
        json.dumps(
            normalize_for_json(
                {
                    "provider": endpoint.provider,
                    "endpoint": endpoint.url,
                    "prompt": args.prompt,
                    "results": comparisons,
                }
            ),
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())