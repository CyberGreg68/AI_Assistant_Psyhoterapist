import json
from pathlib import Path

from scripts.scaffold_language import scaffold_language


def test_scaffold_language_creates_manifest_and_locale_files(tmp_path: Path) -> None:
    manifests_dir = tmp_path / "manifests"
    locales_dir = tmp_path / "locales"
    manifests_dir.mkdir()
    (locales_dir / "hu").mkdir(parents=True)

    source_manifest = {
        "lang": "hu",
        "version": "1.0",
        "generated_at": "2026-03-11",
        "category_order": [
            {
                "prefix": "01",
                "name": "crisis",
                "default_priority": 1,
                "description": "desc",
                "filename": "phrases/01_cri_phrases.hu.jsonc",
                "typical_tags": ["cri"]
            }
        ],
        "code_keys": {},
        "selection_rules": {}
    }
    (manifests_dir / "manifest.hu.jsonc").write_text(json.dumps(source_manifest), encoding="utf-8")

    import scripts.scaffold_language as scaffold_module

    original_root = scaffold_module.ROOT
    original_manifests = scaffold_module.MANIFESTS_DIR
    original_locales = scaffold_module.LOCALES_DIR
    scaffold_module.ROOT = tmp_path
    scaffold_module.MANIFESTS_DIR = manifests_dir
    scaffold_module.LOCALES_DIR = locales_dir
    try:
        scaffold_language("en")
    finally:
        scaffold_module.ROOT = original_root
        scaffold_module.MANIFESTS_DIR = original_manifests
        scaffold_module.LOCALES_DIR = original_locales

    assert (manifests_dir / "manifest.en.jsonc").exists()
    assert (locales_dir / "en" / "phrases" / "01_cri_phrases.en.jsonc").exists()
    assert (locales_dir / "en" / "triggers" / "README.txt").exists()
    assert (locales_dir / "en" / "rules" / "README.txt").exists()
    assert (locales_dir / "en" / "mappings" / "README.txt").exists()
