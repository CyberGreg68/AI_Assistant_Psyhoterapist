from pathlib import Path

from assistant_runtime.core.runtime_bundle import load_bundle
from assistant_runtime.core.runtime_bundle import load_bundle_from_source
from assistant_runtime.core.runtime_bundle import save_published_bundle
from assistant_runtime.runtime_service import RuntimeService


def test_runtime_bundle_can_roundtrip_through_published_artifact(tmp_path: Path) -> None:
    source_bundle = load_bundle_from_source(Path.cwd(), "hu")
    output_path = tmp_path / "runtime_bundle.hu.json"

    save_published_bundle(source_bundle, output_path)
    loaded_bundle = load_bundle(Path.cwd(), "hu", published_bundle_path=output_path)

    assert loaded_bundle.lang == "hu"
    assert loaded_bundle.manifest["lang"] == "hu"
    assert "crisis" in loaded_bundle.categories
    assert loaded_bundle.triggers
    assert loaded_bundle.knowledge_snippets


def test_runtime_service_can_use_published_bundle_via_env_override(tmp_path: Path, monkeypatch) -> None:
    source_bundle = load_bundle_from_source(Path.cwd(), "hu")
    output_path = tmp_path / "runtime_bundle.hu.json"
    save_published_bundle(source_bundle, output_path)
    monkeypatch.setenv("RUNTIME_BUNDLE_PATH", str(output_path))

    service = RuntimeService(Path.cwd(), "hu")
    result = service.process_text("Szorongok es szeretnek segitseget kerni.")

    assert result.selection["text"]
    assert service.bundle.knowledge_snippets