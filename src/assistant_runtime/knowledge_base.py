from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from assistant_runtime.content_metadata import content_meta
from assistant_runtime.content_metadata import is_content_enabled
from assistant_runtime.json_utils import load_json_document


@dataclass(slots=True)
class KnowledgeSnippet:
    snippet_id: str
    text: str
    topics: list[str]
    intents: list[str]
    tags: list[str]
    categories: list[str]
    audience: list[str]
    risk_level: str
    allowed_stages: list[str]
    source: str = "local_curated"
    review_status: str = "appr"
    enabled_in: list[str] | None = None
    profile_id: str | None = None
    origin_ref: str | None = None


def _build_knowledge_snippet(item: dict[str, Any]) -> KnowledgeSnippet:
    metadata = content_meta(item, default_source=str(item.get("source", "dev")))
    return KnowledgeSnippet(
        snippet_id=str(item["id"]),
        text=str(item["text"]),
        topics=[str(value) for value in item.get("topics", [])],
        intents=[str(value) for value in item.get("intents", [])],
        tags=[str(value) for value in item.get("tags", [])],
        categories=[str(value) for value in item.get("categories", [])],
        audience=[str(value) for value in item.get("audience", [])],
        risk_level=str(item.get("risk_level", "general")),
        allowed_stages=[str(value) for value in item.get("allowed_stages", [])],
        source=metadata["src"],
        review_status=metadata["status"],
        enabled_in=list(metadata.get("enabled_in", [])),
        profile_id=metadata.get("profile_id"),
        origin_ref=metadata.get("origin_ref"),
    )


def load_knowledge_snippets_from_payload(payload: list[dict[str, Any]]) -> list[KnowledgeSnippet]:
    snippets: list[KnowledgeSnippet] = []
    for item in payload:
        snippets.append(_build_knowledge_snippet(item))
    return snippets


def load_knowledge_snippets(project_root: Path, lang: str) -> list[KnowledgeSnippet]:
    file_path = project_root / "locales" / lang / "mappings" / f"knowledge_snippets.{lang}.json"
    if not file_path.exists():
        return []

    payload = load_json_document(file_path)
    return load_knowledge_snippets_from_payload(payload)


def _score_snippet(
    snippet: KnowledgeSnippet,
    *,
    intent: str,
    tags: set[str],
    categories: set[str],
    audiences: set[str],
    stage: str,
) -> tuple[int, int, int, int, int, int]:
    stage_match = 1 if not snippet.allowed_stages or stage in snippet.allowed_stages else -1
    audience_match = 1 if not snippet.audience or not audiences or audiences.intersection(snippet.audience) else 0
    category_hits = len(categories.intersection(snippet.categories))
    intent_hit = 1 if intent in snippet.intents else 0
    tag_hits = len(tags.intersection(snippet.tags))
    generality = 1 if snippet.risk_level == "general" else 0
    return (-stage_match, -audience_match, -category_hits, -intent_hit, -tag_hits, -generality)


def retrieve_knowledge_snippets(
    snippets: list[KnowledgeSnippet],
    *,
    intent: str,
    tags: set[str],
    categories: set[str] | None = None,
    audiences: set[str] | None = None,
    stage: str,
    limit: int = 4,
    allowed_statuses: set[str] | None = None,
    channel: str = "rt",
) -> list[dict[str, Any]]:
    categories = categories or set()
    audiences = audiences or set()
    ranked = sorted(
        snippets,
        key=lambda item: _score_snippet(
            item,
            intent=intent,
            tags=tags,
            categories=categories,
            audiences=audiences,
            stage=stage,
        ),
    )
    selected: list[dict[str, Any]] = []
    for item in ranked:
        if item.allowed_stages and stage not in item.allowed_stages:
            continue
        if not is_content_enabled(
            {
                "source": item.source,
                "review_status": item.review_status,
                "meta": {
                    "src": item.source,
                    "status": item.review_status,
                    "enabled_in": item.enabled_in or ["rt", "rv", "tst"],
                    "profile_id": item.profile_id,
                    "origin_ref": item.origin_ref,
                },
            },
            allowed_statuses=allowed_statuses,
            channel=channel,
            default_source=item.source,
            default_status=item.review_status,
        ):
            continue
        selected.append(
            {
                "id": item.snippet_id,
                "text": item.text,
                "topics": list(item.topics),
                "intents": list(item.intents),
                "tags": list(item.tags),
                "categories": list(item.categories),
                "audience": list(item.audience),
                "risk_level": item.risk_level,
                "source": item.source,
                "review_status": item.review_status,
                "content_meta": {
                    "src": item.source,
                    "status": item.review_status,
                    "enabled_in": list(item.enabled_in or []),
                    "profile_id": item.profile_id,
                    "origin_ref": item.origin_ref,
                },
            }
        )
        if len(selected) >= limit:
            break
    return selected