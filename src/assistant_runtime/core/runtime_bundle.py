from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from assistant_runtime.json_utils import load_json_document


PUBLISHED_BUNDLE_SCHEMA_VERSION = 1


@dataclass(slots=True)
class CategoryDefinition:
    prefix: str
    name: str
    default_priority: int
    filename: str
    typical_tags: list[str]
    description: str = ""
    clinical_sensitivity: str = "low"
    requires_clinical_review: bool = False
    handoff_capable: bool = False
    review_owner: str = "content"
    review_sla_hours: int = 72


@dataclass(slots=True)
class ManifestBundle:
    lang: str
    manifest: dict[str, Any]
    categories: dict[str, list[dict[str, Any]]]
    triggers: dict[str, list[dict[str, Any]]]
    knowledge_snippets: list[dict[str, Any]] | None = None


def load_json(file_path: Path) -> Any:
    return load_json_document(file_path)


def load_manifest(manifests_dir: Path, lang: str) -> dict[str, Any]:
    return load_json(manifests_dir / f"manifest.{lang}.jsonc")


def build_category_index(manifest: dict[str, Any], locale_dir: Path) -> dict[str, list[dict[str, Any]]]:
    categories: dict[str, list[dict[str, Any]]] = {}
    for category in manifest["category_order"]:
        filename = category["filename"]
        categories[category["name"]] = load_json(locale_dir / filename)
    return categories


def load_triggers(locale_dir: Path, lang: str) -> dict[str, list[dict[str, Any]]]:
    trigger_dir = locale_dir / "triggers"
    if not trigger_dir.exists():
        return {}

    trigger_map: dict[str, list[dict[str, Any]]] = {}
    for trigger_path in sorted(trigger_dir.glob(f"*_triggers.{lang}.json")):
        items = load_json(trigger_path)
        if not isinstance(items, list):
            continue
        short_code = trigger_path.name.split("_", 2)[1]
        trigger_map[short_code] = items
    return trigger_map


def load_bundle_from_source(project_root: Path, lang: str) -> ManifestBundle:
    manifests_dir = project_root / "manifests"
    locale_dir = project_root / "locales" / lang
    manifest = load_manifest(manifests_dir, lang)
    categories = build_category_index(manifest, locale_dir)
    triggers = load_triggers(locale_dir, lang)
    knowledge_path = locale_dir / "mappings" / f"knowledge_snippets.{lang}.json"
    knowledge_snippets = load_json(knowledge_path) if knowledge_path.exists() else []
    return ManifestBundle(
        lang=lang,
        manifest=manifest,
        categories=categories,
        triggers=triggers,
        knowledge_snippets=knowledge_snippets,
    )


def _normalize_published_bundle_path(project_root: Path, bundle_path: Path | str, lang: str) -> Path:
    candidate = Path(bundle_path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    if candidate.exists() and candidate.is_dir():
        candidate = candidate / f"runtime_bundle.{lang}.json"
    elif candidate.suffix == "":
        candidate = candidate / f"runtime_bundle.{lang}.json"
    return candidate


def load_bundle_from_published(bundle_path: Path) -> ManifestBundle:
    payload = json.loads(bundle_path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0)) != PUBLISHED_BUNDLE_SCHEMA_VERSION:
        raise ValueError(f"Unsupported published runtime bundle schema: {payload.get('schema_version')}")
    return ManifestBundle(
        lang=str(payload["lang"]),
        manifest=dict(payload["manifest"]),
        categories={str(key): list(value) for key, value in dict(payload["categories"]).items()},
        triggers={str(key): list(value) for key, value in dict(payload.get("triggers", {})).items()},
        knowledge_snippets=list(payload.get("knowledge_snippets", [])),
    )


def load_bundle(project_root: Path, lang: str, published_bundle_path: Path | str | None = None) -> ManifestBundle:
    if published_bundle_path:
        resolved_bundle_path = _normalize_published_bundle_path(project_root, published_bundle_path, lang)
        return load_bundle_from_published(resolved_bundle_path)
    return load_bundle_from_source(project_root, lang)


def save_published_bundle(
    bundle: ManifestBundle,
    output_path: Path,
    *,
    source_label: str = "source_tree",
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": PUBLISHED_BUNDLE_SCHEMA_VERSION,
        "lang": bundle.lang,
        "source": {
            "type": source_label,
        },
        "manifest": bundle.manifest,
        "categories": bundle.categories,
        "triggers": bundle.triggers,
        "knowledge_snippets": list(bundle.knowledge_snippets or []),
        "stats": {
            "category_count": len(bundle.categories),
            "trigger_group_count": len(bundle.triggers),
            "phrase_item_count": sum(len(items) for items in bundle.categories.values()),
            "trigger_item_count": sum(len(items) for items in bundle.triggers.values()),
            "knowledge_snippet_count": len(bundle.knowledge_snippets or []),
        },
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return output_path


def category_definitions(manifest: dict[str, Any]) -> list[CategoryDefinition]:
    return [
        CategoryDefinition(
            prefix=item["prefix"],
            name=item["name"],
            default_priority=item["default_priority"],
            filename=item["filename"],
            typical_tags=list(item.get("typical_tags", [])),
            description=item.get("description", ""),
            clinical_sensitivity=item.get("clinical_sensitivity", "low"),
            requires_clinical_review=bool(item.get("requires_clinical_review", False)),
            handoff_capable=bool(item.get("handoff_capable", False)),
            review_owner=item.get("review_owner", "content"),
            review_sla_hours=int(item.get("review_sla_hours", 72)),
        )
        for item in manifest["category_order"]
    ]