from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.json_utils import load_json_document


def load_manifest(lang: str) -> dict:
    return load_json_document(ROOT / "manifests" / f"manifest.{lang}.jsonc")


def load_category_ids(lang: str, filename: str) -> list[str]:
    path = ROOT / "locales" / lang / filename
    items = load_json_document(path)
    if not isinstance(items, list):
        return []
    return [item["id"] for item in items if isinstance(item, dict) and "id" in item]


def check_alignment(langs: list[str]) -> list[str]:
    manifests = {lang: load_manifest(lang) for lang in langs}
    baseline_lang = langs[0]
    baseline_categories = manifests[baseline_lang]["category_order"]
    errors: list[str] = []

    for lang in langs[1:]:
        categories = manifests[lang]["category_order"]
        if len(categories) != len(baseline_categories):
            errors.append(f"Category count mismatch: {baseline_lang} vs {lang}")
            continue

        for base_category, category in zip(baseline_categories, categories, strict=True):
            if (base_category["prefix"], base_category["name"]) != (category["prefix"], category["name"]):
                errors.append(
                    f"Category order mismatch: {baseline_lang}:{base_category['prefix']}_{base_category['name']} vs {lang}:{category['prefix']}_{category['name']}"
                )

            base_ids = load_category_ids(baseline_lang, base_category["filename"])
            compare_ids = load_category_ids(lang, category["filename"])

            if not compare_ids:
                continue
            if base_ids and base_ids != compare_ids:
                errors.append(
                    f"ID alignment mismatch in {base_category['name']}: {baseline_lang} and {lang} have different item IDs"
                )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--langs", nargs="+", default=["hu", "en", "de"])
    args = parser.parse_args()
    errors = check_alignment(args.langs)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"Locale alignment passed for {', '.join(args.langs)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
