from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE_MANIFEST = ROOT / "manifests" / "manifest.hu.jsonc"
SOURCE_LOCALE_DIR = ROOT / "locales" / "hu" / "phrases"
TARGET_DIR = ROOT / "data" / "phrases" / "hu"


def main() -> None:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_MANIFEST, TARGET_DIR / "manifest.hu.json")
    for source_path in SOURCE_LOCALE_DIR.glob("*.hu.jsonc"):
        target_name = source_path.name[:-1] if source_path.name.endswith("jsonc") else source_path.name
        shutil.copy2(source_path, TARGET_DIR / target_name)
    print("Synced scaffold primary data into legacy mirror directory.")


if __name__ == "__main__":
    main()
