from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def strip_leading_block_comment(raw_text: str) -> str:
    stripped = raw_text.lstrip()
    if not stripped.startswith("/*"):
        return raw_text

    comment_end = stripped.find("*/")
    if comment_end == -1:
        return raw_text

    prefix_length = len(raw_text) - len(stripped)
    return raw_text[:prefix_length] + stripped[comment_end + 2 :].lstrip()


def load_json_document(file_path: Path) -> Any:
    raw_text = file_path.read_text(encoding="utf-8")
    return json.loads(strip_leading_block_comment(raw_text))
