from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "data" / "phrases" / "hu"
TARGET_MANIFEST = ROOT / "manifests" / "manifest.hu.jsonc"
TARGET_LOCALE_DIR = ROOT / "locales" / "hu" / "phrases"

FILENAME_MAP = {
    "01_crisis.hu.json": "01_cri_phrases.hu.jsonc",
    "02_boundary.hu.json": "02_bd_phrases.hu.jsonc",
    "03_structure.hu.json": "03_str_phrases.hu.jsonc",
    "04_empathy.hu.json": "04_emp_phrases.hu.jsonc",
    "05_open_questions.hu.json": "05_oq_phrases.hu.jsonc",
    "06_closed_questions.hu.json": "06_cq_phrases.hu.jsonc",
    "07_variants.hu.json": "07_var_phrases.hu.jsonc",
    "08_cbt_mi_dbt.hu.json": "08_cbt_phrases.hu.jsonc",
    "09_psychoeducation.hu.json": "09_edu_phrases.hu.jsonc",
    "10_encouragement.hu.json": "10_enc_phrases.hu.jsonc",
    "11_closing.hu.json": "11_clo_phrases.hu.jsonc",
    "12_cultural.hu.json": "12_cult_phrases.hu.jsonc",
}


def main() -> None:
    TARGET_LOCALE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_DIR / "manifest.hu.json", TARGET_MANIFEST)
    for source_path in SOURCE_DIR.glob("*.hu.json"):
        if source_path.name == "manifest.hu.json":
            continue
        target_name = FILENAME_MAP.get(source_path.name, f"{source_path.stem}c")
        shutil.copy2(source_path, TARGET_LOCALE_DIR / target_name)
    print("Migrated Hungarian data into scaffold structure.")


if __name__ == "__main__":
    main()
