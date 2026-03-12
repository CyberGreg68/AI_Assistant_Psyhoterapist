import json
from pathlib import Path

from assistant_runtime.audit_logger import AuditLogger
from assistant_runtime.runtime_service import RuntimeService


def test_audit_logger_writes_hash_chained_events(tmp_path: Path) -> None:
    logger = AuditLogger(tmp_path / "audit", secret="secret")

    first = logger.append_event(
        stream="content",
        event_type="content_insert",
        actor={"role": "operator", "id": "tester"},
        subject={"file": "locales/hu/phrases/demo.jsonc", "item_id": "emp_001"},
        payload={"reason": "seed"},
    )
    second = logger.append_event(
        stream="content",
        event_type="content_approve",
        actor={"role": "clinical_lead", "id": "reviewer"},
        subject={"file": "locales/hu/phrases/demo.jsonc", "item_id": "emp_001"},
        payload={"reason": "checked"},
    )

    assert first["prev_hash"] is None
    assert second["prev_hash"] == first["chain_hash"]
    assert second["signature"]


def test_runtime_service_writes_conversation_audit_event(tmp_path: Path) -> None:
    audit_logger = AuditLogger(tmp_path / "audit")
    service = RuntimeService(Path.cwd(), "hu", audit_logger=audit_logger)

    result = service.process_text("Szakitas utan vagyok.", conversation_id="audit-conv-1")

    assert result.selection["text"]
    log_path = tmp_path / "audit" / "conversation.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert lines
    payload = json.loads(lines[-1])
    assert payload["event_type"] == "conversation_turn_processed"
    assert payload["subject"]["conversation_id"] == "audit-conv-1"
    assert payload["payload"]["selection"]["item_id"] == result.selection["item_id"]