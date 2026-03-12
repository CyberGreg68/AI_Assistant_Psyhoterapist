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

from assistant_runtime.json_utils import load_json_document


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _review_required_files() -> dict[str, dict]:
    manifest = _load_json(ROOT / "manifests" / "manifest.hu.jsonc")
    return {
        (Path("locales") / "hu" / category["filename"]).as_posix(): category
        for category in manifest["category_order"]
        if category.get("requires_clinical_review")
    }


def requires_clinical_review(paths: list[str]) -> list[str]:
    sensitive_files = _review_required_files()
    flagged: list[str] = []
    for raw_path in paths:
        normalized = Path(raw_path).as_posix()
        if normalized in sensitive_files:
            flagged.append(raw_path)
    return flagged


def missing_review_metadata(paths: list[str]) -> dict[str, int]:
    missing: dict[str, int] = {}
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists() or path.suffix not in {".json", ".jsonc"}:
            continue
        try:
            items = load_json_document(path)
        except Exception:
            continue
        if not isinstance(items, list):
            continue
        count = sum(1 for item in items if isinstance(item, dict) and "review" not in item)
        if count:
            missing[raw_path] = count
    return missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--enforce", action="store_true")
    args = parser.parse_args()

    flagged = requires_clinical_review(args.paths)
    if not flagged:
        print("Clinical review hook: no sensitive file changes detected.")
        return 0

    print("Clinical review hook: sensitive files changed:")
    for item in flagged:
        print(f" - {item}")

    missing = missing_review_metadata(flagged)
    if missing:
        print("Clinical review hook: missing review metadata detected:")
        for path, count in missing.items():
            print(f" - {path}: {count} items without review metadata")

    approved = os.getenv("CLINICAL_REVIEW_APPROVED", "0") == "1"
    if args.enforce and not approved:
        print("Clinical approval missing.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

