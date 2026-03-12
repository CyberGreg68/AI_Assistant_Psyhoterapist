from __future__ import annotations

import os
from pathlib import Path


def load_local_env(project_root: Path, file_names: tuple[str, ...] = (".env.local", ".env")) -> list[str]:
    loaded_files: list[str] = []
    for file_name in file_names:
        file_path = project_root / file_name
        if not file_path.exists() or not file_path.is_file():
            continue
        for raw_line in file_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            normalized_key = key.strip()
            normalized_value = value.strip().strip('"').strip("'")
            if normalized_key and normalized_key not in os.environ:
                os.environ[normalized_key] = normalized_value
        loaded_files.append(file_name)
    return loaded_files