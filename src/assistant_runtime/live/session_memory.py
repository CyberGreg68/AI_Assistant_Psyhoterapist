from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
import json
from pathlib import Path
import re
import time


@dataclass(slots=True)
class ConversationTurn:
    role: str
    text: str
    item_id: str | None = None
    category: str | None = None
    intent: str | None = None
    trigger_id: str | None = None
    tags: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    knowledge_ids: list[str] = field(default_factory=list)
    response_strategy: str | None = None
    timestamp: float = 0.0


@dataclass(slots=True)
class ConversationIdentity:
    memory_key: str
    identity_mode: str
    source: str
    persistence_enabled: bool
    resolved_patient_id: str | None = None
    verified_patient: bool = False
    browser_patient_key: str | None = None


class ConversationMemoryStore:
    def __init__(
        self,
        ttl_seconds: int,
        max_turns: int = 8,
        persistence_path: Path | None = None,
    ) -> None:
        self.ttl_seconds = max(ttl_seconds, 1)
        self.max_turns = max(max_turns, 2)
        self.persistence_path = persistence_path
        self._conversations: dict[str, list[ConversationTurn]] = {}
        self._identities: dict[str, dict[str, object]] = {}
        self._load()

    def _load(self) -> None:
        if self.persistence_path is None or not self.persistence_path.exists():
            return
        payload = json.loads(self.persistence_path.read_text(encoding="utf-8") or "{}")
        subjects = payload.get("subjects", {})
        now = time.time()
        for memory_key, subject_payload in subjects.items():
            turns: list[ConversationTurn] = []
            for item in subject_payload.get("turns", []):
                try:
                    turn = ConversationTurn(
                        role=str(item.get("role", "system")),
                        text=str(item.get("text", "")),
                        item_id=item.get("item_id"),
                        category=item.get("category"),
                        intent=item.get("intent"),
                        trigger_id=item.get("trigger_id"),
                        tags=[str(value) for value in item.get("tags", [])],
                        risk_flags=[str(value) for value in item.get("risk_flags", [])],
                        knowledge_ids=[str(value) for value in item.get("knowledge_ids", [])],
                        response_strategy=item.get("response_strategy"),
                        timestamp=float(item.get("timestamp", 0.0)),
                    )
                except (TypeError, ValueError):
                    continue
                if now - turn.timestamp <= self.ttl_seconds:
                    turns.append(turn)
            if turns:
                self._conversations[memory_key] = turns[-self.max_turns :]
            identity_payload = subject_payload.get("identity")
            if isinstance(identity_payload, dict):
                self._identities[memory_key] = dict(identity_payload)

    def _persist(self) -> None:
        if self.persistence_path is None:
            return
        self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "subjects": {
                memory_key: {
                    "identity": self._identities.get(memory_key, {}),
                    "turns": [asdict(turn) for turn in turns],
                }
                for memory_key, turns in self._conversations.items()
                if turns
            }
        }
        self.persistence_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _prune(self, conversation_id: str, now: float | None = None) -> None:
        now = now if now is not None else time.time()
        turns = self._conversations.get(conversation_id, [])
        fresh_turns = [turn for turn in turns if now - turn.timestamp <= self.ttl_seconds]
        if fresh_turns:
            self._conversations[conversation_id] = fresh_turns[-self.max_turns :]
        elif conversation_id in self._conversations:
            del self._conversations[conversation_id]
            self._identities.pop(conversation_id, None)

    def append(
        self,
        conversation_id: str,
        role: str,
        text: str,
        item_id: str | None = None,
        category: str | None = None,
        intent: str | None = None,
        trigger_id: str | None = None,
        tags: list[str] | None = None,
        risk_flags: list[str] | None = None,
        knowledge_ids: list[str] | None = None,
        response_strategy: str | None = None,
        persist_text: bool = True,
        identity: ConversationIdentity | None = None,
    ) -> None:
        now = time.time()
        self._prune(conversation_id, now)
        turns = self._conversations.setdefault(conversation_id, [])
        stored_text = _compress_text(text) if persist_text else ""
        turns.append(
            ConversationTurn(
                role=role,
                text=stored_text,
                item_id=item_id,
                category=category,
                intent=intent,
                trigger_id=trigger_id,
                tags=list(tags or []),
                risk_flags=list(risk_flags or []),
                knowledge_ids=list(knowledge_ids or []),
                response_strategy=response_strategy,
                timestamp=now,
            )
        )
        self._conversations[conversation_id] = turns[-self.max_turns :]
        if identity is not None:
            self._identities[conversation_id] = asdict(identity)
        self._persist()

    def get_recent_turns(self, conversation_id: str) -> list[ConversationTurn]:
        self._prune(conversation_id)
        return list(self._conversations.get(conversation_id, []))

    def get_recent_turns_payload(self, conversation_id: str) -> list[dict[str, object]]:
        return [asdict(turn) for turn in self.get_recent_turns(conversation_id)]

    def get_identity(self, conversation_id: str) -> dict[str, object]:
        self._prune(conversation_id)
        return dict(self._identities.get(conversation_id, {}))

    def get_summary(self, conversation_id: str) -> dict[str, object]:
        turns = self.get_recent_turns(conversation_id)
        if not turns:
            return {
                "turn_count": 0,
                "patient_messages": [],
                "assistant_item_ids": [],
                "recent_turns": [],
                "active_summary": {
                    "patient_themes": [],
                    "assistant_items": [],
                    "assistant_messages": [],
                    "active_intents": [],
                    "active_tags": [],
                    "active_risk_flags": [],
                    "recent_categories": [],
                    "recent_trigger_ids": [],
                    "recent_knowledge_ids": [],
                    "recent_response_strategies": [],
                    "last_user_message": "",
                    "last_assistant_message": "",
                },
                "summary_text": "No recent conversation summary.",
            }

        patient_messages = [
            _compress_text(turn.text)
            for turn in turns
            if turn.role == "user" and turn.text.strip()
        ]
        assistant_item_ids = [
            str(turn.item_id)
            for turn in turns
            if turn.role == "assistant" and turn.item_id
        ]
        recent_patient_messages = patient_messages[-3:]
        recent_assistant_items = assistant_item_ids[-3:]
        assistant_messages = [
            _compress_text(turn.text)
            for turn in turns
            if turn.role == "assistant" and turn.text.strip()
        ]
        recent_assistant_messages = assistant_messages[-2:]
        recent_knowledge_ids = _dedupe_keep_order(
            knowledge_id
            for turn in turns
            for knowledge_id in turn.knowledge_ids
        )[-4:]
        recent_response_strategies = _dedupe_keep_order(
            str(turn.response_strategy)
            for turn in turns
            if turn.response_strategy
        )[-3:]
        recent_turns = [
            {
                "role": turn.role,
                "text": _compress_text(turn.text),
                "item_id": turn.item_id,
                "category": turn.category,
                "intent": turn.intent,
                "trigger_id": turn.trigger_id,
                "tags": list(turn.tags),
                "risk_flags": list(turn.risk_flags),
                "knowledge_ids": list(turn.knowledge_ids),
                "response_strategy": turn.response_strategy,
                "timestamp": turn.timestamp,
            }
            for turn in turns[-6:]
        ]
        active_summary = {
            "patient_themes": recent_patient_messages,
            "assistant_items": recent_assistant_items,
            "assistant_messages": recent_assistant_messages,
            "active_intents": sorted({str(turn.intent) for turn in turns if turn.intent}),
            "active_tags": sorted({tag for turn in turns for tag in turn.tags}),
            "active_risk_flags": sorted(
                {risk_flag for turn in turns for risk_flag in turn.risk_flags}
            ),
            "recent_categories": sorted({str(turn.category) for turn in turns if turn.category}),
            "recent_trigger_ids": [
                str(turn.trigger_id)
                for turn in turns
                if turn.trigger_id
            ][-3:],
            "recent_knowledge_ids": recent_knowledge_ids,
            "recent_response_strategies": recent_response_strategies,
            "last_user_message": recent_patient_messages[-1] if recent_patient_messages else "",
            "last_assistant_message": (
                recent_assistant_messages[-1] if recent_assistant_messages else ""
            ),
        }

        summary_parts: list[str] = []
        if recent_patient_messages:
            summary_parts.append(
                "Recent patient themes: " + " | ".join(recent_patient_messages)
            )
        if recent_assistant_items:
            summary_parts.append(
                "Recent assistant items: " + ", ".join(recent_assistant_items)
            )
        if active_summary["recent_categories"]:
            summary_parts.append(
                "Recent categories: " + ", ".join(active_summary["recent_categories"])
            )
        if recent_knowledge_ids:
            summary_parts.append(
                "Recent knowledge: " + ", ".join(recent_knowledge_ids)
            )

        return {
            "turn_count": len(turns),
            "patient_messages": recent_patient_messages,
            "assistant_item_ids": recent_assistant_items,
            "recent_turns": recent_turns,
            "active_summary": active_summary,
            "summary_text": "; ".join(summary_parts) if summary_parts else "Recent conversation exists.",
        }


def _compress_text(text: str, limit: int = 120) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _dedupe_keep_order(items) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = str(item).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered