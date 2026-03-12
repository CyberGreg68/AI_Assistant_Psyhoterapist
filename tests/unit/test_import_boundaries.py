from pathlib import Path


def test_live_namespace_does_not_import_ops_modules() -> None:
    live_dir = Path.cwd() / "src" / "assistant_runtime" / "live"
    offending_imports: list[str] = []

    for file_path in sorted(live_dir.glob("*.py")):
        content = file_path.read_text(encoding="utf-8")
        if "assistant_runtime.ops" in content:
            offending_imports.append(file_path.name)

    assert offending_imports == []