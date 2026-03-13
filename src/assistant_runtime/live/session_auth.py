from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import os
import secrets
import time


SESSION_COOKIE_NAME = "patient_portal_session"
DEFAULT_ACCESS_CODE = "local-demo"


def _b64url_encode(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")


def _b64url_decode(payload: str) -> bytes:
    padding = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode((payload + padding).encode("ascii"))


def _now_epoch() -> int:
    return int(time.time())


@dataclass(slots=True)
class SessionIdentity:
    subject: str
    role: str
    allowed_views: tuple[str, ...]
    active_view: str
    issued_at: int
    expires_at: int
    clinician_id: str | None = None
    patient_alias_key: str | None = None


class PortalSessionAuth:
    def __init__(
        self,
        *,
        access_code: str | None = None,
        secret: str | None = None,
        session_ttl_seconds: int = 12 * 60 * 60,
    ) -> None:
        env_access_code = os.getenv("PATIENT_PORTAL_ACCESS_CODE")
        env_secret = os.getenv("PATIENT_PORTAL_SESSION_SECRET")
        self.access_code = (access_code or env_access_code or DEFAULT_ACCESS_CODE).strip()
        secret_value = secret or env_secret or secrets.token_hex(32)
        self.secret = secret_value.encode("utf-8")
        self.session_ttl_seconds = max(300, int(session_ttl_seconds))
        self.uses_default_access_code = not access_code and not env_access_code
        self.uses_generated_secret = not secret and not env_secret

    def validate_access_code(self, provided_code: str | None) -> bool:
        candidate = (provided_code or "").strip()
        if not candidate:
            return False
        return hmac.compare_digest(candidate.encode("utf-8"), self.access_code.encode("utf-8"))

    def issue_session_token(
        self,
        subject: str = "patient",
        *,
        role: str = "patient",
        allowed_views: tuple[str, ...] | list[str] | None = None,
        active_view: str | None = None,
        clinician_id: str | None = None,
        patient_alias_key: str | None = None,
    ) -> str:
        issued_at = _now_epoch()
        normalized_allowed_views = tuple(dict.fromkeys(allowed_views or ("patient",)))
        normalized_active_view = active_view or normalized_allowed_views[0]
        payload = {
            "sub": subject,
            "role": role,
            "views": list(normalized_allowed_views),
            "view": normalized_active_view,
            "iat": issued_at,
            "exp": issued_at + self.session_ttl_seconds,
            "nonce": secrets.token_hex(8),
        }
        if clinician_id:
            payload["clinician_id"] = clinician_id
        if patient_alias_key:
            payload["patient_alias_key"] = patient_alias_key
        encoded_payload = _b64url_encode(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = hmac.new(self.secret, encoded_payload.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{encoded_payload}.{signature}"

    def parse_session_token(self, token: str | None) -> SessionIdentity | None:
        if not token or "." not in token:
            return None
        encoded_payload, provided_signature = token.split(".", 1)
        expected_signature = hmac.new(
            self.secret,
            encoded_payload.encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(provided_signature, expected_signature):
            return None
        try:
            payload = json.loads(_b64url_decode(encoded_payload).decode("utf-8"))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        expires_at = int(payload.get("exp", 0))
        if expires_at <= _now_epoch():
            return None
        issued_at = int(payload.get("iat", 0))
        subject = str(payload.get("sub") or "patient")
        role = str(payload.get("role") or "patient")
        allowed_views_payload = payload.get("views") or ["patient"]
        if not isinstance(allowed_views_payload, list):
            allowed_views_payload = ["patient"]
        allowed_views = tuple(str(item) for item in allowed_views_payload if str(item).strip()) or ("patient",)
        active_view = str(payload.get("view") or allowed_views[0])
        if active_view not in allowed_views:
            active_view = allowed_views[0]
        clinician_id = payload.get("clinician_id")
        patient_alias_key = payload.get("patient_alias_key")
        return SessionIdentity(
            subject=subject,
            role=role,
            allowed_views=allowed_views,
            active_view=active_view,
            issued_at=issued_at,
            expires_at=expires_at,
            clinician_id=str(clinician_id) if clinician_id else None,
            patient_alias_key=str(patient_alias_key) if patient_alias_key else None,
        )

    def build_set_cookie_header(self, token: str, *, max_age: int | None = None, clear: bool = False) -> str:
        if clear:
            return (
                f"{SESSION_COOKIE_NAME}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0; "
                "Expires=Thu, 01 Jan 1970 00:00:00 GMT"
            )
        cookie_max_age = max_age if max_age is not None else self.session_ttl_seconds
        return (
            f"{SESSION_COOKIE_NAME}={token}; Path=/; HttpOnly; SameSite=Lax; "
            f"Max-Age={int(cookie_max_age)}"
        )