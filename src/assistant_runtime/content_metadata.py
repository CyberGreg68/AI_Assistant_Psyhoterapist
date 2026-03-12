from __future__ import annotations

from typing import Any


SOURCE_CODES = {"dev", "sum", "trn", "aud", "llm", "lit", "mix"}
STATUS_CODES = {"appr", "rev", "sugg", "test", "hold"}
CHANNEL_CODES = {"rt", "rv", "tst"}


def _default_enabled_in(status: str) -> list[str]:
    if status == "appr":
        return ["rt", "rv", "tst"]
    if status in {"rev", "sugg"}:
        return ["rv", "tst"]
    if status == "test":
        return ["tst", "rv"]
    return ["rv"]


def _status_from_review(item: dict[str, Any], default_status: str) -> str:
    review = item.get("review")
    if not isinstance(review, dict):
        return default_status
    review_status = str(review.get("review_status", "")).strip()
    if review_status == "approved":
        return "appr"
    if review_status == "draft":
        return "rev"
    if review_status == "needs_revision":
        return "hold"
    return default_status


def content_meta(
    item: dict[str, Any] | None,
    *,
    default_source: str = "dev",
    default_status: str = "appr",
) -> dict[str, Any]:
    if default_source not in SOURCE_CODES:
        default_source = "dev"
    if default_status not in STATUS_CODES:
        default_status = "appr"
    if not isinstance(item, dict):
        return {
            "src": default_source,
            "status": default_status,
            "enabled_in": _default_enabled_in(default_status),
        }

    raw_meta = item.get("meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}

    source = str(meta.get("src") or item.get("source") or default_source).strip() or default_source
    if source not in SOURCE_CODES:
        source = default_source

    status = str(
        meta.get("status")
        or item.get("review_status")
        or _status_from_review(item, default_status)
    ).strip() or default_status
    if status not in STATUS_CODES:
        status = default_status

    raw_enabled_in = meta.get("enabled_in")
    if isinstance(raw_enabled_in, list):
        enabled_in = [str(value) for value in raw_enabled_in if str(value) in CHANNEL_CODES]
    else:
        enabled_in = []
    if not enabled_in:
        enabled_in = _default_enabled_in(status)

    payload: dict[str, Any] = {
        "src": source,
        "status": status,
        "enabled_in": enabled_in,
    }
    if meta.get("profile_id"):
        payload["profile_id"] = str(meta["profile_id"])
    if meta.get("origin_ref"):
        payload["origin_ref"] = str(meta["origin_ref"])
    return payload


def is_content_enabled(
    item: dict[str, Any] | None,
    *,
    allowed_statuses: set[str] | None = None,
    channel: str = "rt",
    default_source: str = "dev",
    default_status: str = "appr",
) -> bool:
    meta = content_meta(item, default_source=default_source, default_status=default_status)
    statuses = allowed_statuses or {"appr"}
    if meta["status"] not in statuses:
        return False
    return channel in meta["enabled_in"]