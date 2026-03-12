from __future__ import annotations


def normalize_for_json(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): normalize_for_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_for_json(item) for item in value]
    if isinstance(value, tuple):
        return [normalize_for_json(item) for item in value]
    if isinstance(value, set):
        return sorted(normalize_for_json(item) for item in value)
    return value