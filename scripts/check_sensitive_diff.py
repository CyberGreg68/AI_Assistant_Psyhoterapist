from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.json_utils import load_json_document


def _manifest_path(lang: str) -> Path:
    return ROOT / "manifests" / f"manifest.{lang}.jsonc"


def _review_required_files(lang: str) -> set[str]:
    manifest = load_json_document(_manifest_path(lang))
    return {
        str((ROOT / "locales" / lang / category["filename"]).relative_to(ROOT)).replace("\\", "/")
        for category in manifest["category_order"]
        if category.get("requires_clinical_review")
    }


def changed_files_from_git() -> list[str]:
    commands = [
        ["git", "diff", "--name-only", "HEAD^", "HEAD"],
        ["git", "diff", "--name-only", "HEAD~1", "HEAD"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]
    return []


def changed_sensitive_files(paths: list[str], langs: list[str]) -> list[str]:
    sensitive_paths: set[str] = set()
    for lang in langs:
        sensitive_paths.update(_review_required_files(lang))
    return [path for path in paths if path in sensitive_paths]


def files_missing_review(paths: list[str]) -> list[str]:
    missing: list[str] = []
    for relative_path in paths:
        file_path = ROOT / Path(relative_path)
        items = load_json_document(file_path)
        if not isinstance(items, list):
            continue
        if any(isinstance(item, dict) and "review" not in item for item in items):
            missing.append(relative_path)
    return missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*")
    parser.add_argument("--langs", nargs="+", default=["hu"])
    args = parser.parse_args()

    changed_paths = [path.replace("\\", "/") for path in args.paths] or changed_files_from_git()
    if not changed_paths:
        print("Sensitive diff check passed: no changed files detected.")
        return 0

    sensitive = changed_sensitive_files(changed_paths, args.langs)
    if not sensitive:
        print("Sensitive diff check passed: no changed sensitive locale files detected.")
        return 0

    missing = files_missing_review(sensitive)
    if missing:
        print("Sensitive diff check failed. Changed sensitive files missing review metadata:")
        for path in missing:
            print(f" - {path}")
        return 1

    print("Sensitive diff check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
