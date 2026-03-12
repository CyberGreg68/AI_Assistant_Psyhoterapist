from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from datetime import datetime
from pathlib import Path
import hashlib
import json
import shutil
import time

from assistant_runtime.audit_logger import AuditLogger
from assistant_runtime.ops.review_pack_builder import build_review_candidate_pack
from assistant_runtime.ops.review_pack_builder import collect_review_source_paths


@dataclass(slots=True)
class ProcessedBatch:
    batch_name: str
    pack_id: str
    output_path: Path
    source_paths: list[Path]


def _load_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"processed_batches": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _batch_signature(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        stat = path.stat()
        digest.update(str(path).encode("utf-8"))
        digest.update(str(stat.st_mtime_ns).encode("utf-8"))
        digest.update(str(stat.st_size).encode("utf-8"))
    return digest.hexdigest()


def discover_review_batches(inbox_dir: Path) -> list[tuple[str, list[Path]]]:
    if not inbox_dir.exists():
        return []
    batches: list[tuple[str, list[Path]]] = []
    root_files = [item for item in sorted(inbox_dir.iterdir()) if item.is_file()]
    supported_root_files = collect_review_source_paths(root_files, recursive=False)
    if supported_root_files:
        batches.append(("root_uploads", supported_root_files))
    for item in sorted(inbox_dir.iterdir()):
        if not item.is_dir():
            continue
        resolved = collect_review_source_paths([item])
        if resolved:
            batches.append((item.name, resolved))
    return batches


def process_review_inbox(
    project_root: Path,
    *,
    inbox_dir: Path,
    output_dir: Path,
    state_path: Path,
    archive_dir: Path | None = None,
    config_dir: Path | None = None,
    pack_prefix: str = "review_batch",
    actor: str = "unknown",
) -> list[ProcessedBatch]:
    state = _load_state(state_path)
    processed = state.setdefault("processed_batches", {})
    results: list[ProcessedBatch] = []
    output_dir.mkdir(parents=True, exist_ok=True)

    for batch_name, source_paths in discover_review_batches(inbox_dir):
        signature = _batch_signature(source_paths)
        existing = processed.get(batch_name)
        if isinstance(existing, dict) and existing.get("signature") == signature:
            continue
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        pack_id = f"{pack_prefix}_{batch_name}_{timestamp}"
        payload = build_review_candidate_pack(
            pack_id,
            source_paths=source_paths,
            config_dir=config_dir,
        )
        output_path = output_dir / f"{pack_id}.json"
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        processed[batch_name] = {
            "signature": signature,
            "pack_id": pack_id,
            "output_path": str(output_path),
            "processed_at": datetime.now(UTC).isoformat(),
        }
        results.append(
            ProcessedBatch(
                batch_name=batch_name,
                pack_id=pack_id,
                output_path=output_path,
                source_paths=source_paths,
            )
        )
        AuditLogger(project_root / "data" / "runtime_state" / "audit").append_event(
            stream="content",
            event_type="review_inbox_batch_processed",
            actor={"role": "operator", "id": actor},
            subject={"batch_name": batch_name, "pack_id": pack_id, "output_path": str(output_path)},
            payload={"source_paths": [str(item) for item in source_paths]},
        )
        if archive_dir is not None:
            archive_dir.mkdir(parents=True, exist_ok=True)
            for source_path in source_paths:
                if source_path.parent == inbox_dir:
                    target = archive_dir / source_path.name
                    if source_path.exists():
                        shutil.move(str(source_path), str(target))
                else:
                    top_level = source_path
                    while top_level.parent != inbox_dir and top_level.parent != top_level:
                        top_level = top_level.parent
                    if top_level.parent == inbox_dir and top_level.exists():
                        target = archive_dir / top_level.name
                        if not target.exists():
                            shutil.move(str(top_level), str(target))
        _save_state(state_path, state)
    _save_state(state_path, state)
    return results


def watch_review_inbox(
    project_root: Path,
    *,
    inbox_dir: Path,
    output_dir: Path,
    state_path: Path,
    archive_dir: Path | None = None,
    config_dir: Path | None = None,
    pack_prefix: str = "review_batch",
    actor: str = "unknown",
    watch_seconds: int = 30,
) -> None:
    while True:
        process_review_inbox(
            project_root,
            inbox_dir=inbox_dir,
            output_dir=output_dir,
            state_path=state_path,
            archive_dir=archive_dir,
            config_dir=config_dir,
            pack_prefix=pack_prefix,
            actor=actor,
        )
        time.sleep(max(1, watch_seconds))