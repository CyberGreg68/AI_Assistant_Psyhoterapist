from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import time


def _now_epoch() -> int:
    return int(time.time())


def _dedupe_views(values: list[str] | tuple[str, ...] | None) -> list[str]:
    result: list[str] = []
    for value in values or []:
        normalized = str(value).strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


@dataclass(slots=True)
class PatientTokenRecord:
    token_id: str
    patient_alias_key: str
    clinician_id: str
    token_hash: str
    token_preview: str
    label: str = ""
    issued_at: int = field(default_factory=_now_epoch)
    expires_at: int | None = None
    revoked_at: int | None = None
    last_used_at: int | None = None
    allowed_views: list[str] = field(default_factory=lambda: ["patient"])

    @property
    def is_active(self) -> bool:
        now = _now_epoch()
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > now)


class PatientTokenStore:
    def __init__(self, storage_path: Path, *, secret: str) -> None:
        self.storage_path = storage_path
        self.secret = secret.encode("utf-8")
        self._records: dict[str, PatientTokenRecord] = {}
        self._load()

    def _hash_token(self, token: str) -> str:
        return hmac.new(self.secret, token.encode("utf-8"), hashlib.sha256).hexdigest()

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        payload = json.loads(self.storage_path.read_text(encoding="utf-8") or "{}")
        records = payload.get("records", [])
        if not isinstance(records, list):
            return
        for item in records:
            if not isinstance(item, dict):
                continue
            try:
                record = PatientTokenRecord(
                    token_id=str(item["token_id"]),
                    patient_alias_key=str(item["patient_alias_key"]),
                    clinician_id=str(item["clinician_id"]),
                    token_hash=str(item["token_hash"]),
                    token_preview=str(item["token_preview"]),
                    label=str(item.get("label") or ""),
                    issued_at=int(item.get("issued_at") or _now_epoch()),
                    expires_at=int(item["expires_at"]) if item.get("expires_at") is not None else None,
                    revoked_at=int(item["revoked_at"]) if item.get("revoked_at") is not None else None,
                    last_used_at=int(item["last_used_at"]) if item.get("last_used_at") is not None else None,
                    allowed_views=_dedupe_views(item.get("allowed_views") or ["patient"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            self._records[record.token_id] = record

    def _persist(self) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "records": [
                asdict(record)
                for record in sorted(self._records.values(), key=lambda item: (item.clinician_id, item.issued_at, item.token_id))
            ]
        }
        self.storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def issue_token(
        self,
        *,
        clinician_id: str,
        label: str = "",
        patient_alias_key: str | None = None,
        expires_in_days: int | None = 30,
        raw_token: str | None = None,
    ) -> tuple[str, PatientTokenRecord]:
        raw_token = (raw_token or ("ptk_" + secrets.token_urlsafe(24).rstrip("="))).strip()
        token_hash = self._hash_token(raw_token)
        token_id = "tok_" + secrets.token_hex(8)
        alias_key = patient_alias_key or ("anonpt_" + secrets.token_hex(8))
        preview = f"{raw_token[:9]}...{raw_token[-5:]}"
        expires_at = None
        if expires_in_days is not None:
            expires_at = _now_epoch() + max(1, int(expires_in_days)) * 24 * 60 * 60
        record = PatientTokenRecord(
            token_id=token_id,
            patient_alias_key=alias_key,
            clinician_id=clinician_id,
            token_hash=token_hash,
            token_preview=preview,
            label=label.strip(),
            expires_at=expires_at,
        )
        self._records[token_id] = record
        self._persist()
        return raw_token, record

    def ensure_token(
        self,
        *,
        raw_token: str,
        clinician_id: str,
        label: str = "",
        patient_alias_key: str | None = None,
        expires_in_days: int | None = 30,
    ) -> PatientTokenRecord:
        token_hash = self._hash_token(raw_token.strip())
        for record in self._records.values():
            if hmac.compare_digest(record.token_hash, token_hash):
                return record
        _, record = self.issue_token(
            clinician_id=clinician_id,
            label=label,
            patient_alias_key=patient_alias_key,
            expires_in_days=expires_in_days,
            raw_token=raw_token,
        )
        return record

    def resolve_token(self, raw_token: str) -> PatientTokenRecord | None:
        token_hash = self._hash_token(raw_token.strip())
        for record in self._records.values():
            if not hmac.compare_digest(record.token_hash, token_hash):
                continue
            if not record.is_active:
                return None
            record.last_used_at = _now_epoch()
            self._persist()
            return record
        return None

    def revoke_token(self, token_id: str, *, clinician_id: str | None = None) -> PatientTokenRecord | None:
        record = self._records.get(token_id)
        if record is None:
            return None
        if clinician_id is not None and record.clinician_id != clinician_id:
            return None
        record.revoked_at = _now_epoch()
        self._persist()
        return record

    def list_tokens(self, *, clinician_id: str | None = None, include_revoked: bool = True) -> list[PatientTokenRecord]:
        records = sorted(self._records.values(), key=lambda item: (item.clinician_id, -item.issued_at, item.token_id))
        if clinician_id is not None:
            records = [record for record in records if record.clinician_id == clinician_id]
        if not include_revoked:
            records = [record for record in records if record.revoked_at is None]
        return records

    def list_aliases(self, *, clinician_id: str | None = None) -> list[dict[str, object]]:
        aliases: dict[str, dict[str, object]] = {}
        for record in self.list_tokens(clinician_id=clinician_id, include_revoked=True):
            payload = aliases.setdefault(
                record.patient_alias_key,
                {
                    "patient_alias_key": record.patient_alias_key,
                    "clinician_id": record.clinician_id,
                    "latest_label": record.label,
                    "token_count": 0,
                    "active_token_count": 0,
                    "last_issued_at": record.issued_at,
                },
            )
            payload["token_count"] = int(payload["token_count"]) + 1
            if record.is_active:
                payload["active_token_count"] = int(payload["active_token_count"]) + 1
            if record.label:
                payload["latest_label"] = record.label
            payload["last_issued_at"] = max(int(payload["last_issued_at"]), record.issued_at)
        return sorted(aliases.values(), key=lambda item: (-int(item["last_issued_at"]), str(item["patient_alias_key"])))

    def clinician_can_access_alias(self, clinician_id: str, patient_alias_key: str) -> bool:
        for record in self._records.values():
            if record.clinician_id == clinician_id and record.patient_alias_key == patient_alias_key:
                return True
        return False