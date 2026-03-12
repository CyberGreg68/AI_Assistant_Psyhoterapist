from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.json_utils import load_json_document


def _load_json(path: Path):
    return load_json_document(path)


def check_lang(lang: str) -> list[str]:
    manifest_path = ROOT / "manifests" / f"manifest.{lang}.jsonc"
    locale_dir = ROOT / "locales" / lang
    manifest = _load_json(manifest_path)
    errors: list[str] = []

    seen_prefixes: set[str] = set()
    for category in manifest["category_order"]:
        prefix = category["prefix"]
        filename = category["filename"]
        file_name_only = Path(filename).name
        if prefix in seen_prefixes:
            errors.append(f"Duplicate prefix in manifest: {prefix}")
        seen_prefixes.add(prefix)

        if not (locale_dir / filename).exists():
            errors.append(f"Missing locale file: {locale_dir / filename}")

        if not file_name_only.startswith(f"{prefix}_"):
            errors.append(f"Filename/prefix mismatch: {filename}")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lang", default="hu")
    args = parser.parse_args()
    errors = check_lang(args.lang)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"Manifest consistency passed for {args.lang}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
