from pathlib import Path

from assistant_runtime.manifest_loader import load_bundle


def test_load_bundle_reads_hungarian_manifest() -> None:
    bundle = load_bundle(Path.cwd(), "hu")
    assert bundle.lang == "hu"
    assert "crisis" in bundle.categories
    assert "cult" in bundle.triggers
    assert bundle.knowledge_snippets
    assert bundle.manifest["lang"] == "hu"
