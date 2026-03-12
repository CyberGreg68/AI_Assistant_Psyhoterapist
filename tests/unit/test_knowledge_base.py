from pathlib import Path

from assistant_runtime.knowledge_base import KnowledgeSnippet
from assistant_runtime.knowledge_base import load_knowledge_snippets
from assistant_runtime.knowledge_base import retrieve_knowledge_snippets


def test_retrieve_knowledge_snippets_prefers_matching_category_and_tags() -> None:
    snippets = load_knowledge_snippets(Path.cwd(), "hu")

    result = retrieve_knowledge_snippets(
        snippets,
        intent="emotional_support",
        tags={"emp", "oq"},
        categories={"open_questions"},
        stage="phrase_selection",
    )

    assert result
    assert result[0]["id"] == "kb_hu_002"


def test_retrieve_knowledge_snippets_prefers_matching_audience() -> None:
    snippets = [
        KnowledgeSnippet(
            snippet_id="teen_only",
            text="Teen-focused snippet.",
            topics=["szakitas"],
            intents=["emotional_support"],
            tags=["oq", "emp"],
            categories=["open_questions"],
            audience=["teen"],
            risk_level="general",
            allowed_stages=["phrase_selection"],
        ),
        KnowledgeSnippet(
            snippet_id="adult_only",
            text="Adult-focused snippet.",
            topics=["szakitas"],
            intents=["emotional_support"],
            tags=["oq", "emp"],
            categories=["open_questions"],
            audience=["adult"],
            risk_level="general",
            allowed_stages=["phrase_selection"],
        ),
    ]

    result = retrieve_knowledge_snippets(
        snippets,
        intent="emotional_support",
        tags={"oq", "emp"},
        categories={"open_questions"},
        audiences={"teen"},
        stage="phrase_selection",
    )

    assert result[0]["id"] == "teen_only"