from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGACY_DIR = ROOT / "data" / "phrases"


def find_legacy_files() -> list[Path]:
    if not LEGACY_DIR.exists():
        return []
    return [path for path in LEGACY_DIR.rglob("*") if path.is_file()]


def main() -> int:
    legacy_files = find_legacy_files()
    if not legacy_files:
        print("Legacy data guard passed.")
        return 0

    print("Legacy data guard failed. Primary locale data must not live under data/phrases:")
    for path in legacy_files:
        print(f" - {path.relative_to(ROOT)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
