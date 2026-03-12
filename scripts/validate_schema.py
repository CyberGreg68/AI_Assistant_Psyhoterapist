from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import validate


ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.json_utils import load_json_document


MANIFESTS_DIR = ROOT / "manifests"
LOCALES_DIR = ROOT / "locales"


def _load_json(path: Path):
    if path.name.endswith("_schema.json"):
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    return load_json_document(path)


def main() -> int:
    manifest_schema = _load_json(MANIFESTS_DIR / "manifest_schema.json")
    phrase_schema = _load_json(MANIFESTS_DIR / "phrase_schema.json")

    for manifest_path in MANIFESTS_DIR.glob("manifest.*.jsonc"):
        validate(instance=_load_json(manifest_path), schema=manifest_schema)

    for language_dir in LOCALES_DIR.iterdir():
        if not language_dir.is_dir():
            continue
        phrase_dir = language_dir / "phrases"
        if not phrase_dir.exists():
            continue
        for phrase_path in phrase_dir.glob("*.jsonc"):
            validate(instance=_load_json(phrase_path), schema=phrase_schema)

        trigger_dir = language_dir / "triggers"
        trigger_schema_path = trigger_dir / "schema.triggers.json"
        if trigger_dir.exists() and trigger_schema_path.exists():
            trigger_schema = _load_json(trigger_schema_path)
            for trigger_path in trigger_dir.glob("*_triggers.*.json"):
                validate(instance=_load_json(trigger_path), schema=trigger_schema)

    print("Schema validation passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
