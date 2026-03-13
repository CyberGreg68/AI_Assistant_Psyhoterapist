from __future__ import annotations

import argparse
from email.parser import BytesParser
from email.policy import default as email_policy
from html import escape
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from mimetypes import guess_type
from pathlib import Path
import tempfile
from urllib.parse import parse_qs
from urllib.parse import quote
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from assistant_runtime.live.admin_api import health_payload
from assistant_runtime.live.admin_api import operations_payload
from assistant_runtime.live.admin_api import process_audio_payload
from assistant_runtime.live.admin_api import process_audio_upload_payload
from assistant_runtime.live.admin_api import process_text_payload
from assistant_runtime.config.loader import load_access_governance_settings
from assistant_runtime.env_loader import load_local_env
from assistant_runtime.live.patient_tokens import PatientTokenRecord
from assistant_runtime.live.patient_tokens import PatientTokenStore
from assistant_runtime.live.runtime_service import RuntimeService
from assistant_runtime.live.session_auth import DEFAULT_ACCESS_CODE
from assistant_runtime.live.session_auth import PortalSessionAuth
from assistant_runtime.live.session_auth import SESSION_COOKIE_NAME
from assistant_runtime.live.session_auth import SessionIdentity


WEB_DIR = ROOT / "web"
GENERATED_AUDIO_DIR = ROOT / "data" / "runtime_state" / "generated_audio"
UPLOADED_AUDIO_DIR = ROOT / "data" / "runtime_state" / "uploaded_audio"
TOKEN_STORE_PATH = ROOT / "data" / "runtime_state" / "patient_tokens.json"
TOKEN_STORE_SECRET_PATH = ROOT / "data" / "runtime_state" / "patient_tokens.secret"

ROLE_PATIENT = "patient"
ROLE_CLINICIAN = "clinician"
ROLE_DEVELOPER = "developer"
VIEW_PATIENT = "patient"
VIEW_CLINICIAN = "clinician"
VIEW_DEVELOPER = "developer"

DEFAULT_CLINICIAN_ACCESS_CODE = "clinical-demo"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a thin admin API for operations snapshot and runtime testing.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--lang", default="de")
    return parser.parse_args()


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, object]:
    content_length = int(handler.headers.get("Content-Length", "0"))
    raw_body = handler.rfile.read(content_length) if content_length else b"{}"
    return json.loads(raw_body.decode("utf-8") or "{}")


def _write_json(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: dict[str, object],
    extra_headers: dict[str, str] | None = None,
) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    for name, value in (extra_headers or {}).items():
        handler.send_header(name, value)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_html(handler: BaseHTTPRequestHandler, status: int, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _write_redirect(handler: BaseHTTPRequestHandler, location: str, set_cookie: str | None = None) -> None:
    handler.send_response(HTTPStatus.SEE_OTHER)
    handler.send_header("Location", location)
    if set_cookie:
        handler.send_header("Set-Cookie", set_cookie)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def _write_file(handler: BaseHTTPRequestHandler, file_path: Path) -> None:
    content_type, _ = guess_type(file_path.name)
    body = file_path.read_bytes()
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", f"{content_type or 'application/octet-stream'}; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_multipart_audio_upload(handler: BaseHTTPRequestHandler) -> tuple[Path, dict[str, object]]:
    content_type = handler.headers.get("Content-Type", "")
    if "multipart/form-data" not in content_type:
        raise ValueError("Expected multipart/form-data upload.")
    content_length = int(handler.headers.get("Content-Length", "0"))
    raw_body = handler.rfile.read(content_length) if content_length else b""
    message = BytesParser(policy=email_policy).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + raw_body
    )
    if not message.is_multipart():
        raise ValueError("Multipart upload could not be parsed.")

    file_name = "upload.webm"
    file_bytes = b""
    payload: dict[str, object] = {}
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        field_name = part.get_param("name", header="content-disposition")
        if not field_name:
            continue
        field_bytes = part.get_payload(decode=True) or b""
        if field_name == "audio":
            file_name = Path(part.get_filename() or file_name).name
            file_bytes = field_bytes
            continue

        charset = part.get_content_charset() or "utf-8"
        value = field_bytes.decode(charset, errors="replace")
        if field_name in {"patient_identity", "profile_overrides", "active_conditions"} and value:
            payload[field_name] = json.loads(value)
        elif field_name in {"prefer_online", "debug", "synthesize_speech"}:
            payload[field_name] = str(value).lower() in {"1", "true", "yes", "on"}
        elif field_name in {"latency_elapsed_ms"} and value:
            payload[field_name] = int(value)
        else:
            payload[field_name] = value

    if not file_bytes:
        raise ValueError("Missing audio file field named 'audio'.")
    suffix = Path(file_name).suffix or ".webm"
    UPLOADED_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(delete=False, dir=UPLOADED_AUDIO_DIR, suffix=suffix) as handle:
        upload_path = Path(handle.name)
        handle.write(file_bytes)
    return upload_path, payload


def _attach_audio_urls(payload: dict[str, object]) -> dict[str, object]:
    tts = payload.get("tts")
    if isinstance(tts, dict) and tts.get("audio_file_name"):
        tts["audio_url"] = f"/runtime/generated-audio/{tts['audio_file_name']}"
    return payload


def _supported_languages() -> list[str]:
    languages = []
    for manifest_path in sorted((ROOT / "manifests").glob("manifest.*.jsonc")):
        suffix = manifest_path.stem.split(".")[-1]
        languages.append(suffix)
    return languages


def _service_for_lang(services: dict[str, RuntimeService], lang: str) -> RuntimeService:
    if lang in services:
        return services[lang]
    raise LookupError(f"Unsupported language: {lang}")


def _parse_cookies(raw_cookie_header: str | None) -> dict[str, str]:
    if not raw_cookie_header:
        return {}
    cookies: dict[str, str] = {}
    for item in raw_cookie_header.split(";"):
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        cookies[name.strip()] = value.strip()
    return cookies


def _sanitize_next_path(raw_path: str | None) -> str:
    candidate = (raw_path or "/chat").strip() or "/chat"
    parsed = urlparse(candidate)
    safe_path = parsed.path or "/chat"
    if not safe_path.startswith("/"):
        safe_path = "/chat"
    if safe_path.startswith("/login") or safe_path.startswith("/auth/"):
        safe_path = "/chat"
    if parsed.query:
        safe_path = f"{safe_path}?{parsed.query}"
    return safe_path


def _normalize_slug(value: object, *, fallback_prefix: str) -> str | None:
    candidate = "" if value is None else str(value).strip()
    normalized = []
    for char in candidate:
        if char.isalnum() or char in {"-", "_"}:
            normalized.append(char.lower())
        elif char.isspace():
            normalized.append("-")
    slug = "".join(normalized).strip("-")
    if slug:
        return slug[:80]
    return None


def _allowed_views_for_role(role: str) -> tuple[str, ...]:
    if role == ROLE_DEVELOPER:
        return (VIEW_DEVELOPER, VIEW_CLINICIAN, VIEW_PATIENT)
    if role == ROLE_CLINICIAN:
        return (VIEW_CLINICIAN, VIEW_PATIENT)
    return (VIEW_PATIENT,)


def _default_view_for_role(role: str) -> str:
    return _allowed_views_for_role(role)[0]


def _sanitize_view_for_role(role: str, requested_view: str | None) -> str:
    allowed_views = _allowed_views_for_role(role)
    if requested_view in allowed_views:
        return str(requested_view)
    return allowed_views[0]


def _session_payload(session: SessionIdentity) -> dict[str, object]:
    return {
        "subject": session.subject,
        "role": session.role,
        "allowed_views": list(session.allowed_views),
        "active_view": session.active_view,
        "clinician_id": session.clinician_id,
        "patient_alias_key": session.patient_alias_key,
        "expires_at": session.expires_at,
    }


def _public_token_record(record: PatientTokenRecord) -> dict[str, object]:
    return {
        "token_id": record.token_id,
        "patient_alias_key": record.patient_alias_key,
        "clinician_id": record.clinician_id,
        "token_preview": record.token_preview,
        "label": record.label,
        "issued_at": record.issued_at,
        "expires_at": record.expires_at,
        "revoked_at": record.revoked_at,
        "last_used_at": record.last_used_at,
        "is_active": record.is_active,
        "allowed_views": list(record.allowed_views),
    }


def _load_or_create_secret(secret_path: Path) -> str:
    env_secret = os.getenv("PATIENT_TOKEN_STORE_SECRET")
    if env_secret:
        return env_secret
    if secret_path.exists():
        value = secret_path.read_text(encoding="utf-8").strip()
        if value:
            return value
    secret = os.urandom(32).hex()
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_text(secret, encoding="utf-8")
    return secret


def _resolve_portal_login(
    payload: dict[str, object],
    auth_manager: PortalSessionAuth,
    token_store: PatientTokenStore,
) -> tuple[str, str, str | None, str | None]:
    mode = str(payload.get("mode") or "access_code").strip().lower()
    clinician_id = _normalize_slug(payload.get("clinician_id"), fallback_prefix="clinician")

    if mode == "patient_token":
        raw_token = str(payload.get("patient_token") or "").strip()
        if not raw_token:
            raise PermissionError("Missing patient token.")
        record = token_store.resolve_token(raw_token)
        if record is None:
            raise PermissionError("Invalid or expired patient token.")
        return ROLE_PATIENT, record.token_id, record.clinician_id, record.patient_alias_key

    access_code = str(payload.get("access_code") or "").strip()
    developer_code = os.getenv("DEVELOPER_PORTAL_ACCESS_CODE") or auth_manager.access_code
    clinician_code = os.getenv("CLINICIAN_PORTAL_ACCESS_CODE") or DEFAULT_CLINICIAN_ACCESS_CODE
    if access_code and access_code == developer_code:
        return ROLE_DEVELOPER, "developer-session", clinician_id or "developer-root", None
    if access_code and access_code == clinician_code:
        return ROLE_CLINICIAN, "clinician-session", clinician_id or "clinician-demo", None
    if auth_manager.validate_access_code(access_code):
        return ROLE_DEVELOPER, "developer-session", clinician_id or "developer-root", None
    raise PermissionError("Invalid access code.")


def _resolve_runtime_identity(
    session: SessionIdentity,
    payload: dict[str, object],
    token_store: PatientTokenStore,
) -> tuple[str | None, dict[str, object]]:
    incoming_identity = payload.get("patient_identity")
    identity_payload = dict(incoming_identity) if isinstance(incoming_identity, dict) else {}
    consent_to_store_excerpt = bool(identity_payload.get("consent_to_store_excerpt"))

    if session.role == ROLE_PATIENT:
        return None, {
            "anonymous_subject_key": session.patient_alias_key,
            "clinician_id": session.clinician_id,
            "identity_confidence": "clinician_issued_token",
            "consent_to_store_excerpt": consent_to_store_excerpt,
        }

    selected_alias = _normalize_slug(identity_payload.get("anonymous_subject_key"), fallback_prefix="anonpt")
    if selected_alias:
        can_access_alias = session.role == ROLE_DEVELOPER
        if session.role == ROLE_CLINICIAN and session.clinician_id:
            can_access_alias = token_store.clinician_can_access_alias(session.clinician_id, selected_alias)
        if can_access_alias:
            return None, {
                "anonymous_subject_key": selected_alias,
                "clinician_id": session.clinician_id,
                "identity_confidence": "staff_selected_alias",
                "consent_to_store_excerpt": consent_to_store_excerpt,
            }

    return None, {
        "clinician_id": session.clinician_id,
        "identity_confidence": "staff_demo_session",
        "consent_to_store_excerpt": consent_to_store_excerpt,
    }


def _sanitize_response_for_session(payload: dict[str, object], session: SessionIdentity) -> dict[str, object]:
    response_payload = dict(payload)
    response_payload["session"] = _session_payload(session)

    if session.role == ROLE_DEVELOPER:
        return response_payload

    response_payload.pop("debug", None)
    response_payload.pop("generation_request", None)

    patient_identity = response_payload.get("patient_identity")
    if isinstance(patient_identity, dict):
        if session.role == ROLE_PATIENT:
            response_payload["patient_identity"] = {
                "identity_mode": patient_identity.get("identity_mode"),
                "identity_confidence": patient_identity.get("identity_confidence"),
            }
        else:
            response_payload["patient_identity"] = {
                "memory_key": patient_identity.get("memory_key"),
                "identity_mode": patient_identity.get("identity_mode"),
                "identity_confidence": patient_identity.get("identity_confidence"),
                "anonymous_subject_key": patient_identity.get("anonymous_subject_key"),
                "clinician_id": patient_identity.get("clinician_id"),
            }

    if session.role == ROLE_PATIENT:
        response_payload.pop("analysis", None)
        response_payload.pop("route_decisions", None)
        response_payload.pop("patient_context", None)
        response_payload.pop("knowledge_context", None)

    return response_payload


def _render_login_page(auth_manager: PortalSessionAuth, next_path: str, demo_patient_token: str) -> str:
        escaped_next = escape(next_path, quote=True)
        clinician_hint = escape(os.getenv("CLINICIAN_PORTAL_ACCESS_CODE") or DEFAULT_CLINICIAN_ACCESS_CODE)
        developer_hint = escape(DEFAULT_ACCESS_CODE)
        demo_token_hint = escape(demo_patient_token)
        uses_default_developer_code = "true" if auth_manager.uses_default_access_code else "false"
        return f"""<!DOCTYPE html>
<html lang=\"de\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>Psychotherapist Assistant Portal</title>
    <style>
        :root {{
            --bg: #f2eee8;
            --panel: rgba(255,255,255,0.88);
            --panel-strong: #ffffff;
            --ink: #485451;
            --muted: #6e7774;
            --line: rgba(72, 84, 81, 0.14);
            --accent: #4fc3d7;
            --accent-soft: rgba(79, 195, 215, 0.14);
            --warn: #c16a61;
            --shadow: 0 22px 56px rgba(84, 74, 63, 0.12);
            --font-ui: \"Trebuchet MS\", \"Segoe UI\", Arial, sans-serif;
            --font-display: Georgia, \"Times New Roman\", serif;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            min-height: 100vh;
            color: var(--ink);
            background:
                radial-gradient(circle at top left, rgba(79, 195, 215, 0.18), transparent 24%),
                radial-gradient(circle at bottom right, rgba(201, 183, 157, 0.18), transparent 22%),
                linear-gradient(135deg, #f7f5f1 0%, #ede5da 46%, #f4efe7 100%);
            font-family: var(--font-ui);
        }}
        .shell {{
            width: min(1180px, calc(100vw - 28px));
            margin: 18px auto;
            display: grid;
            grid-template-columns: minmax(0, 1.15fr) minmax(320px, 0.85fr);
            gap: 18px;
        }}
        .panel {{
            border: 1px solid var(--line);
            border-radius: 28px;
            background: var(--panel);
            backdrop-filter: blur(14px);
            box-shadow: var(--shadow);
        }}
        .hero {{
            padding: 30px;
            display: grid;
            gap: 22px;
            min-height: 580px;
            align-content: start;
        }}
        .hero-header,
        .login-head {{
            display: flex;
            gap: 14px;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
        }}
        .brand {{
            display: flex;
            gap: 14px;
            align-items: center;
        }}
        .logo-pill {{
            position: relative;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            padding: 10px 18px;
            border-radius: 999px;
            border: 1px solid color-mix(in srgb, var(--line) 72%, transparent 28%);
            background: linear-gradient(180deg, rgba(255,255,255,0.26), rgba(255,255,255,0.06)), color-mix(in srgb, var(--panel) 86%, #f1e9df 14%);
            box-shadow: 0 16px 36px rgba(27, 38, 35, 0.08);
            isolation: isolate;
        }}
        .logo-pill::before {{
            content: "";
            position: absolute;
            inset: -18px -26px;
            border-radius: 999px;
            background: radial-gradient(circle, rgba(255,255,255,0.34) 0%, rgba(255,255,255,0.08) 52%, transparent 76%);
            filter: blur(12px);
            opacity: 0.9;
            z-index: -1;
            pointer-events: none;
        }}
        .brand img,
        .login-head img {{
            width: 154px;
            height: auto;
            display: block;
        }}
        .brand-copy {{
            display: grid;
            gap: 6px;
        }}
        .badge-row, .quick-links {{
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
            align-items: center;
        }}
        .pill {{
            border-radius: 999px;
            padding: 8px 12px;
            border: 1px solid var(--line);
            background: rgba(255,255,255,0.72);
            font: 700 11px/1.1 "Courier New", monospace;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            color: var(--muted);
        }}
        .accent {{ background: var(--accent); color: white; border-color: transparent; }}
        h1, h2, p {{ margin: 0; }}
        h1 {{ font: 400 46px/1.02 var(--font-display); max-width: 10ch; }}
        h2 {{ font: 400 28px/1.08 var(--font-display); }}
        .lead, .copy, .note {{ color: var(--muted); line-height: 1.65; }}
        .hero-art {{
            min-height: 260px;
            border-radius: 24px;
            background:
                linear-gradient(160deg, rgba(255,255,255,0.28), transparent 38%),
                radial-gradient(circle at 34% 32%, rgba(255, 244, 230, 0.9), rgba(195, 174, 142, 0.15) 22%, transparent 23%),
                linear-gradient(120deg, rgba(79, 195, 215, 0.66), rgba(173, 144, 112, 0.34) 56%, rgba(250, 247, 241, 0.42)),
                linear-gradient(180deg, #9ea7a4 0%, #c8b39a 100%);
            position: relative;
            overflow: hidden;
        }}
        .hero-art::after {{
            content: "";
            position: absolute;
            inset: auto 24px 24px auto;
            width: 180px;
            height: 180px;
            background: url('/assets/logo_light.png') center/contain no-repeat;
            opacity: 0.85;
        }}
        .login {{ padding: 26px; display: grid; gap: 16px; align-content: start; }}
        .login-tools {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
        .language-box {{ display: grid; gap: 6px; min-width: 120px; }}
        .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .tab {{
            border: 1px solid var(--line);
            background: transparent;
            color: var(--muted);
            border-radius: 999px;
            padding: 10px 14px;
            font: 700 11px/1.1 "Courier New", monospace;
            letter-spacing: 0.07em;
            text-transform: uppercase;
            cursor: pointer;
        }}
        .tab.active {{ background: var(--accent-soft); color: var(--ink); border-color: rgba(13, 122, 102, 0.22); }}
        .card {{
            display: none;
            gap: 12px;
            padding: 18px;
            border: 1px solid var(--line);
            border-radius: 22px;
            background: var(--panel-strong);
        }}
        .card.active {{ display: grid; }}
        label {{ font: 700 11px/1.2 "Courier New", monospace; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); }}
        input, button {{ font: inherit; }}
        input {{ width: 100%; border: 1px solid var(--line); border-radius: 16px; padding: 12px 13px; background: rgba(255,255,255,0.9); color: var(--ink); }}
        input:focus {{ outline: none; border-color: var(--accent); box-shadow: 0 0 0 4px var(--accent-soft); }}
        button.primary {{ border: 0; border-radius: 16px; padding: 13px 15px; background: var(--accent); color: white; cursor: pointer; font: 700 12px/1 "Courier New", monospace; letter-spacing: 0.08em; text-transform: uppercase; }}
        .hint {{ padding: 12px 14px; border-radius: 16px; background: rgba(13, 122, 102, 0.08); border: 1px solid rgba(13, 122, 102, 0.18); color: var(--ink); }}
        .warn {{ color: var(--warn); }}
        .error {{ min-height: 20px; color: var(--warn); font-size: 14px; }}
        a {{ color: var(--accent); text-decoration: none; }}
        @media (max-width: 900px) {{ .shell {{ grid-template-columns: 1fr; }} .hero {{ min-height: auto; }} h1 {{ max-width: none; font-size: 38px; }} .brand img, .login-head img {{ width: 132px; }} }}
    </style>
</head>
<body>
    <main class=\"shell\">
        <section class=\"panel hero\">
            <div class=\"hero-header\">
                <div class=\"brand\">
                    <div class=\"logo-pill\"><img src=\"/assets/logo_light.png\" alt=\"Portal logo\"></div>
                    <div class=\"brand-copy\">
                        <p id=\"hero-kicker\" class=\"pill accent\">Psychotherapie Portal</p>
                        <p id=\"hero-brand-copy\" class=\"copy\">Rollenbasiertes Demo mit pseudonymer Patientenidentität.</p>
                    </div>
                </div>
            </div>
            <div class=\"badge-row\">
                <span id=\"badge-primary\" class=\"pill accent\">Rollenportal</span>
                <span id=\"badge-patient\" class=\"pill\">Patient</span>
                <span id=\"badge-clinician\" class=\"pill\">Klinik</span>
                <span id=\"badge-developer\" class=\"pill\">Entwicklerdemo</span>
            </div>
            <div>
                <h1 id=\"hero-title\">Therapie-Demo mit rollenbasiertem Zugang</h1>
                <p id=\"hero-lead\" class=\"lead\">Patienten sehen nur ihre eigene Oberfläche. Kliniker können zwischen klinischer und Patientenansicht wechseln. Entwickler erhalten alle drei Ansichten samt Demo-Debug.</p>
            </div>
            <div class=\"hero-art\"></div>
            <div class=\"copy\">
                <p id=\"hero-copy-1\">Der Patientenzugang basiert auf einem vom Kliniker ausgegebenen pseudonymen Token. Die Runtime erhält nur einen anonymen Subject-Key.</p>
                <p id=\"hero-copy-2\">Der klinische Demo-Code lautet <strong>{clinician_hint}</strong>. Die Entwickleransicht nutzt einen separaten Code.</p>
            </div>
            <div class=\"quick-links\">
                <a id=\"hero-link-desktop\" href=\"/chat\">Desktop</a>
                <a id=\"hero-link-mobile\" href=\"/chat/mobile\">Mobil</a>
            </div>
        </section>
        <section class=\"panel login\">
            <div class=\"login-head\">
                <div class=\"brand\">
                    <div class=\"logo-pill\"><img src=\"/assets/logo_light.png\" alt=\"Portal logo\"></div>
                    <div class=\"brand-copy\">
                        <p id=\"login-protected-pill\" class=\"pill\">Geschützter Zugang</p>
                        <h2 id=\"login-heading\">Anmeldung</h2>
                    </div>
                </div>
                <div class=\"language-box\">
                    <label id=\"login-language-label\" for=\"login-language\">Sprache</label>
                    <select id=\"login-language\">
                        <option value=\"de\">Deutsch</option>
                        <option value=\"hu\">Magyar</option>
                        <option value=\"en\">English</option>
                    </select>
                </div>
            </div>
            <div class=\"tabs\">
                <button id=\"tab-patient-token\" class=\"tab active\" data-mode=\"patient_token\" type=\"button\">Patiententoken</button>
                <button id=\"tab-access-code\" class=\"tab\" data-mode=\"access_code\" type=\"button\">Klinik / Entwicklung</button>
            </div>
            <div id=\"card-patient_token\" class=\"card active\">
                <div id=\"patient-card-hint\" class=\"hint\">Patienten melden sich mit einem vom Kliniker ausgegebenen Token an.</div>
                <div id=\"demo-token-hint\" class=\"hint\">Demo-Patiententoken: <strong>{demo_token_hint}</strong></div>
                <label id=\"patient-token-label\" for=\"patient-token\">Patiententoken</label>
                <input id=\"patient-token\" type=\"password\" autocomplete=\"one-time-code\" placeholder=\"ptk_...\">
            </div>
            <div id=\"card-access_code\" class=\"card\">
                <div id=\"access-card-hint\" class=\"hint\">Entwicklercode gibt Vollzugriff auf die Demo. Der klinische Code erlaubt nur Klinik- und Patientenansicht.</div>
                <div id=\"developer-hint\" class=\"hint\"></div>
                <label id=\"access-code-label\" for=\"access-code\">Zugangscode</label>
                <input id=\"access-code\" type=\"password\" autocomplete=\"current-password\" placeholder=\"Portal-Zugangscode\">
                <label id=\"clinician-id-label\" for=\"clinician-id\">Kliniker-ID oder Alias</label>
                <input id=\"clinician-id\" type=\"text\" autocomplete=\"username\" placeholder=\"pl. dr-kovacs\">
            </div>
            <button id=\"login-button\" class=\"primary\" type=\"button\">Anmelden</button>
            <p id=\"login-error\" class=\"error\"></p>
        </section>
    </main>
    <script>
        const nextPath = '{escaped_next}';
        const defaultClinicianCode = '{clinician_hint}';
        const defaultDeveloperCode = '{developer_hint}';
        const demoPatientToken = '{demo_token_hint}';
        const usesDefaultDeveloperCode = {uses_default_developer_code};
        const languageStorageKey = 'patient-portal-language';
        const tabs = Array.from(document.querySelectorAll('.tab'));
        const loginButton = document.getElementById('login-button');
        const errorNode = document.getElementById('login-error');
        const accessCodeInput = document.getElementById('access-code');
        const clinicianIdInput = document.getElementById('clinician-id');
        const patientTokenInput = document.getElementById('patient-token');
        const languageSelect = document.getElementById('login-language');
        let loginMode = 'patient_token';

        const LOGIN_COPY = {{
            de: {{
                pageTitle: 'Psychotherapie-Portal',
                heroKicker: 'Psychotherapie-Portal',
                heroBrandCopy: 'Rollenbasiertes Demo mit pseudonymer Patientenidentität.',
                badgePrimary: 'Rollenportal',
                badgePatient: 'Patient',
                badgeClinician: 'Klinik',
                badgeDeveloper: 'Entwicklerdemo',
                heroTitle: 'Therapie-Demo mit rollenbasiertem Zugang',
                heroLead: 'Patienten sehen nur ihre eigene Oberfläche. Kliniker können zwischen klinischer und Patientenansicht wechseln. Entwickler erhalten alle drei Ansichten samt Demo-Debug.',
                heroCopy1: 'Der Patientenzugang basiert auf einem vom Kliniker ausgegebenen pseudonymen Token. Die Runtime erhält nur einen anonymen Subject-Key.',
                heroCopy2: `Der klinische Demo-Code lautet ${{defaultClinicianCode}}. Die Entwickleransicht nutzt einen separaten Code.`,
                heroLinkDesktop: 'Desktop',
                heroLinkMobile: 'Mobil',
                protectedAccess: 'Geschützter Zugang',
                loginHeading: 'Anmeldung',
                languageLabel: 'Sprache',
                patientTab: 'Patiententoken',
                accessTab: 'Klinik / Entwicklung',
                patientHint: 'Patienten melden sich mit einem vom Kliniker ausgegebenen Token an.',
                demoTokenHint: `Demo-Patiententoken: ${{demoPatientToken}}`,
                patientTokenLabel: 'Patiententoken',
                accessHint: 'Entwicklercode gibt Vollzugriff auf die Demo. Der klinische Code erlaubt nur Klinik- und Patientenansicht.',
                developerHint: `Lokaler Entwickler-Democode: ${{defaultDeveloperCode}}`,
                accessCodeLabel: 'Zugangscode',
                accessCodePlaceholder: 'Portal-Zugangscode',
                clinicianIdLabel: 'Kliniker-ID oder Alias',
                clinicianIdPlaceholder: 'z. B. dr-kovacs',
                loginButton: 'Anmelden',
                loginFailed: 'Anmeldung fehlgeschlagen.',
                networkError: 'Netzwerkfehler',
            }},
            hu: {{
                pageTitle: 'Pszichoterápiás portál',
                heroKicker: 'Pszichoterápiás portál',
                heroBrandCopy: 'Szerepkörös demó pszeudonim páciensazonossággal.',
                badgePrimary: 'Szerepkörös portál',
                badgePatient: 'Páciens',
                badgeClinician: 'Klinika',
                badgeDeveloper: 'Fejlesztői demó',
                heroTitle: 'Terápiás demó szerepkörös hozzáféréssel',
                heroLead: 'A páciens csak a saját felületét látja. A klinikus válthat klinikai és páciens nézet között. A fejlesztő mindhárom nézetet és a teljes demó-debugot is eléri.',
                heroCopy1: 'A páciens-hozzáférés klinikus által kibocsátott pszeudonim tokenre épül. A runtime csak anonim subject kulcsot kap.',
                heroCopy2: `A klinikai demó-kód: ${{defaultClinicianCode}}. A fejlesztői nézet külön kóddal érhető el.`,
                heroLinkDesktop: 'Asztali',
                heroLinkMobile: 'Mobil',
                protectedAccess: 'Védett hozzáférés',
                loginHeading: 'Belépés',
                languageLabel: 'Nyelv',
                patientTab: 'Páciens token',
                accessTab: 'Klinikus / fejlesztő',
                patientHint: 'A páciens klinikus által kiadott tokennel lép be.',
                demoTokenHint: `Teszt páciens token: ${{demoPatientToken}}`,
                patientTokenLabel: 'Páciens token',
                accessHint: 'A fejlesztői kód teljes demó-hozzáférést ad. A klinikai kód csak klinikai és páciens nézetet enged.',
                developerHint: `Helyi fejlesztői demó-kód: ${{defaultDeveloperCode}}`,
                accessCodeLabel: 'Belépési kód',
                accessCodePlaceholder: 'Portál-hozzáférési kód',
                clinicianIdLabel: 'Klinikus azonosító vagy alias',
                clinicianIdPlaceholder: 'pl. dr-kovacs',
                loginButton: 'Belépés',
                loginFailed: 'A belépés nem sikerült.',
                networkError: 'Hálózati hiba',
            }},
            en: {{
                pageTitle: 'Psychotherapy Portal',
                heroKicker: 'Psychotherapy Portal',
                heroBrandCopy: 'Role-aware demo with pseudonymous patient identity.',
                badgePrimary: 'Role-aware portal',
                badgePatient: 'Patient',
                badgeClinician: 'Clinician',
                badgeDeveloper: 'Developer demo',
                heroTitle: 'Therapy demo with role-based access',
                heroLead: 'Patients only see their own surface. Clinicians can switch between clinician and patient views. Developers can access all three views with full demo debug.',
                heroCopy1: 'Patient access is based on a clinician-issued pseudonymous token. The runtime only receives an anonymous subject key.',
                heroCopy2: `The clinician demo code is ${{defaultClinicianCode}}. The developer view uses a separate code.`,
                heroLinkDesktop: 'Desktop',
                heroLinkMobile: 'Mobile',
                protectedAccess: 'Protected access',
                loginHeading: 'Sign in',
                languageLabel: 'Language',
                patientTab: 'Patient token',
                accessTab: 'Clinician / developer',
                patientHint: 'Patients sign in with a clinician-issued token.',
                demoTokenHint: `Test patient token: ${{demoPatientToken}}`,
                patientTokenLabel: 'Patient token',
                accessHint: 'Developer code grants full demo access. The clinical code only allows clinician and patient views.',
                developerHint: `Local developer demo code: ${{defaultDeveloperCode}}`,
                accessCodeLabel: 'Access code',
                accessCodePlaceholder: 'Portal access code',
                clinicianIdLabel: 'Clinician ID or alias',
                clinicianIdPlaceholder: 'e.g. dr-kovacs',
                loginButton: 'Sign in',
                loginFailed: 'Sign-in failed.',
                networkError: 'Network error',
            }},
        }};

        function readLanguageCookie() {{
            const match = document.cookie.match(/(?:^|; )portal_lang=([^;]+)/);
            return match ? decodeURIComponent(match[1]) : '';
        }}

        function resolvePreferredLanguage() {{
            const saved = window.localStorage.getItem(languageStorageKey) || readLanguageCookie();
            return ['de', 'hu', 'en'].includes(saved) ? saved : 'de';
        }}

        function persistLanguage(lang) {{
            window.localStorage.setItem(languageStorageKey, lang);
            document.cookie = `portal_lang=${{encodeURIComponent(lang)}}; path=/; max-age=31536000; SameSite=Lax`;
        }}

        function loginCopyFor(lang) {{
            return LOGIN_COPY[lang] || LOGIN_COPY.de;
        }}

        function applyLanguage(lang) {{
            const copy = loginCopyFor(lang);
            document.documentElement.lang = lang;
            document.title = copy.pageTitle;
            document.getElementById('hero-kicker').textContent = copy.heroKicker;
            document.getElementById('hero-brand-copy').textContent = copy.heroBrandCopy;
            document.getElementById('badge-primary').textContent = copy.badgePrimary;
            document.getElementById('badge-patient').textContent = copy.badgePatient;
            document.getElementById('badge-clinician').textContent = copy.badgeClinician;
            document.getElementById('badge-developer').textContent = copy.badgeDeveloper;
            document.getElementById('hero-title').textContent = copy.heroTitle;
            document.getElementById('hero-lead').textContent = copy.heroLead;
            document.getElementById('hero-copy-1').textContent = copy.heroCopy1;
            document.getElementById('hero-copy-2').textContent = copy.heroCopy2;
            document.getElementById('hero-link-desktop').textContent = copy.heroLinkDesktop;
            document.getElementById('hero-link-mobile').textContent = copy.heroLinkMobile;
            document.getElementById('login-protected-pill').textContent = copy.protectedAccess;
            document.getElementById('login-heading').textContent = copy.loginHeading;
            document.getElementById('login-language-label').textContent = copy.languageLabel;
            document.getElementById('tab-patient-token').textContent = copy.patientTab;
            document.getElementById('tab-access-code').textContent = copy.accessTab;
            document.getElementById('patient-card-hint').textContent = copy.patientHint;
            document.getElementById('demo-token-hint').innerHTML = `${{copy.demoTokenHint}}`;
            document.getElementById('patient-token-label').textContent = copy.patientTokenLabel;
            document.getElementById('access-card-hint').textContent = copy.accessHint;
            const developerHintNode = document.getElementById('developer-hint');
            developerHintNode.textContent = copy.developerHint;
            developerHintNode.style.display = usesDefaultDeveloperCode ? 'block' : 'none';
            document.getElementById('access-code-label').textContent = copy.accessCodeLabel;
            accessCodeInput.placeholder = copy.accessCodePlaceholder;
            document.getElementById('clinician-id-label').textContent = copy.clinicianIdLabel;
            clinicianIdInput.placeholder = copy.clinicianIdPlaceholder;
            loginButton.textContent = copy.loginButton;
            languageSelect.value = lang;
            persistLanguage(lang);
        }}

        function updateMode(nextMode) {{
            loginMode = nextMode;
            tabs.forEach((tab) => tab.classList.toggle('active', tab.dataset.mode === nextMode));
            document.querySelectorAll('.card').forEach((card) => card.classList.remove('active'));
            document.getElementById(`card-${{nextMode}}`).classList.add('active');
            errorNode.textContent = '';
        }}

        tabs.forEach((tab) => tab.addEventListener('click', () => updateMode(tab.dataset.mode)));

        async function submitLogin() {{
            errorNode.textContent = '';
            loginButton.disabled = true;
            const payload = {{ mode: loginMode, next_path: nextPath }};
            if (loginMode === 'patient_token') {{
                payload.patient_token = patientTokenInput.value.trim();
            }} else {{
                payload.access_code = accessCodeInput.value.trim();
                payload.clinician_id = clinicianIdInput.value.trim();
            }}
            try {{
                const response = await fetch('/auth/session', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify(payload),
                }});
                const responsePayload = await response.json();
                if (!response.ok) {{
                    errorNode.textContent = responsePayload.message || loginCopyFor(languageSelect.value).loginFailed;
                    return;
                }}
                window.location.href = responsePayload.next_path || '/chat';
            }} catch (error) {{
                errorNode.textContent = error.message || loginCopyFor(languageSelect.value).networkError;
            }} finally {{
                loginButton.disabled = false;
            }}
        }}

        languageSelect.addEventListener('change', () => applyLanguage(languageSelect.value));
        loginButton.addEventListener('click', submitLogin);
        [accessCodeInput, clinicianIdInput, patientTokenInput].forEach((input) => input && input.addEventListener('keydown', (event) => {{
            if (event.key === 'Enter') {{
                event.preventDefault();
                submitLogin();
            }}
        }}));
        applyLanguage(resolvePreferredLanguage());
        patientTokenInput.focus();
    </script>
</body>
</html>
"""


def build_handler(
    services: dict[str, RuntimeService],
    default_lang: str,
    auth_manager: PortalSessionAuth,
    auth_required: bool,
    token_store: PatientTokenStore,
) -> type[BaseHTTPRequestHandler]:
    audit_logger = next(
        (service.audit_logger for service in services.values() if service.audit_logger is not None),
        None,
    )
    demo_patient_token = os.getenv("DEMO_PATIENT_PORTAL_TOKEN") or "ptk_demo_patient_access"
    token_store.ensure_token(
        raw_token=demo_patient_token,
        clinician_id="demo-clinician",
        label="demo patient login",
        patient_alias_key="anonpt_demo_login",
        expires_in_days=3650,
    )

    class AdminHandler(BaseHTTPRequestHandler):
        def _session_identity(self) -> SessionIdentity | None:
            cookies = _parse_cookies(self.headers.get("Cookie"))
            return auth_manager.parse_session_token(cookies.get(SESSION_COOKIE_NAME))

        def _require_roles(self, roles: set[str], *, expect_json: bool) -> SessionIdentity | None:
            session = self._session_identity()
            if session is None:
                if not self._ensure_authenticated(expect_json=expect_json):
                    return None
                session = self._session_identity()
            if session is None:
                return None
            if session.role in roles:
                return session
            if expect_json:
                _write_json(
                    self,
                    HTTPStatus.FORBIDDEN,
                    {"error": "forbidden", "message": "Role does not have access to this resource."},
                )
            else:
                _write_redirect(self, "/chat")
            return None

        def _reissue_session(self, session: SessionIdentity, *, active_view: str | None = None) -> str:
            return auth_manager.issue_session_token(
                subject=session.subject,
                role=session.role,
                allowed_views=session.allowed_views,
                active_view=_sanitize_view_for_role(session.role, active_view or session.active_view),
                clinician_id=session.clinician_id,
                patient_alias_key=session.patient_alias_key,
            )

        def _ensure_authenticated(self, *, expect_json: bool) -> bool:
            if not auth_required:
                return True
            session = self._session_identity()
            if session is not None:
                return True
            next_path = _sanitize_next_path(self.path)
            if expect_json:
                _write_json(
                    self,
                    HTTPStatus.UNAUTHORIZED,
                    {
                        "error": "auth_required",
                        "message": "Patient portal session required.",
                        "login_url": f"/login?next={quote(next_path, safe='/?=&')}",
                    },
                )
                return False
            _write_redirect(self, f"/login?next={quote(next_path, safe='/?=&')}")
            return False

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path
            if path == "/login":
                next_path = _sanitize_next_path(parse_qs(parsed.query).get("next", ["/chat"])[0])
                if auth_required and self._session_identity() is not None:
                    _write_redirect(self, next_path)
                    return
                _write_html(self, HTTPStatus.OK, _render_login_page(auth_manager, next_path, demo_patient_token))
                return
            if path in {
                "/assets/patient_portal_logo.svg",
                "/assets/patient_portal_logo1.svg",
                "/assets/logo_light.png",
                "/assets/logo_dark.png",
            }:
                asset_map = {
                    "/assets/patient_portal_logo.svg": WEB_DIR / "patient_portal_logo.svg",
                    "/assets/patient_portal_logo1.svg": WEB_DIR / "patient_portal_logo1.svg",
                    "/assets/logo_light.png": WEB_DIR / "logo_light.png",
                    "/assets/logo_dark.png": WEB_DIR / "logo_dark.png",
                }
                _write_file(self, asset_map[path])
                return
            if path in {"/", "/chat"}:
                if not self._ensure_authenticated(expect_json=False):
                    return
                _write_file(self, WEB_DIR / "patient_chat.html")
                return
            if path in {"/chat/mobile", "/mobile"}:
                if not self._ensure_authenticated(expect_json=False):
                    return
                _write_file(self, WEB_DIR / "patient_chat_mobile.html")
                return
            if path.startswith("/runtime/generated-audio/"):
                if not self._ensure_authenticated(expect_json=False):
                    return
                file_name = path.rsplit("/", 1)[-1]
                target = GENERATED_AUDIO_DIR / file_name
                if not target.exists() or target.parent != GENERATED_AUDIO_DIR:
                    _write_json(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
                    return
                _write_file(self, target)
                return
            if path == "/ui-config":
                session = self._session_identity()
                _write_json(
                    self,
                    HTTPStatus.OK,
                    {
                        "supported_languages": _supported_languages(),
                        "default_language": default_lang,
                        "auth_required": auth_required,
                        "session": _session_payload(session) if session is not None else None,
                    },
                )
                return
            if path == "/auth/session/context":
                if not self._ensure_authenticated(expect_json=True):
                    return
                session = self._session_identity()
                if session is None:
                    _write_json(self, HTTPStatus.UNAUTHORIZED, {"error": "auth_required"})
                    return
                response_payload: dict[str, object] = {
                    "session": _session_payload(session),
                    "supported_languages": _supported_languages(),
                    "default_language": default_lang,
                }
                if session.role in {ROLE_CLINICIAN, ROLE_DEVELOPER}:
                    clinician_id = session.clinician_id if session.role == ROLE_CLINICIAN else None
                    response_payload["patient_aliases"] = token_store.list_aliases(clinician_id=clinician_id)
                _write_json(self, HTTPStatus.OK, response_payload)
                return
            if path == "/health":
                if self._require_roles({ROLE_CLINICIAN, ROLE_DEVELOPER}, expect_json=True) is None:
                    return
                _write_json(self, HTTPStatus.OK, health_payload(ROOT))
                return
            if path == "/operations":
                if self._require_roles({ROLE_CLINICIAN, ROLE_DEVELOPER}, expect_json=True) is None:
                    return
                _write_json(self, HTTPStatus.OK, operations_payload(ROOT))
                return
            if path == "/clinical/patient-tokens":
                session = self._require_roles({ROLE_CLINICIAN, ROLE_DEVELOPER}, expect_json=True)
                if session is None:
                    return
                clinician_id = session.clinician_id if session.role == ROLE_CLINICIAN else None
                _write_json(
                    self,
                    HTTPStatus.OK,
                    {
                        "tokens": [_public_token_record(record) for record in token_store.list_tokens(clinician_id=clinician_id)],
                        "patient_aliases": token_store.list_aliases(clinician_id=clinician_id),
                    },
                )
                return
            _write_json(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            try:
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/auth/session":
                    payload = _read_json_body(self)
                    next_path = _sanitize_next_path(str(payload.get("next_path", "/chat")))
                    try:
                        role, subject, clinician_id, patient_alias_key = _resolve_portal_login(
                            payload,
                            auth_manager,
                            token_store,
                        )
                    except PermissionError as exc:
                        if audit_logger is not None:
                            audit_logger.append_event(
                                stream="conversation",
                                event_type="portal_login_failed",
                                actor={"role": "patient_portal", "source": "web"},
                                subject={"next_path": next_path},
                                payload={"client_ip": self.client_address[0], "mode": str(payload.get("mode") or "access_code")},
                            )
                        _write_json(self, HTTPStatus.UNAUTHORIZED, {"error": "invalid_login", "message": str(exc)})
                        return
                    token = auth_manager.issue_session_token(
                        subject=subject,
                        role=role,
                        allowed_views=_allowed_views_for_role(role),
                        active_view=_default_view_for_role(role),
                        clinician_id=clinician_id,
                        patient_alias_key=patient_alias_key,
                    )
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Set-Cookie", auth_manager.build_set_cookie_header(token))
                    body = json.dumps(
                        {
                            "status": "ok",
                            "next_path": next_path,
                            "session": {
                                "role": role,
                                "active_view": _default_view_for_role(role),
                                "clinician_id": clinician_id,
                                "patient_alias_key": patient_alias_key,
                            },
                        },
                        ensure_ascii=False,
                        indent=2,
                    ).encode("utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    if audit_logger is not None:
                        audit_logger.append_event(
                            stream="conversation",
                            event_type=("patient_token_redeemed" if role == ROLE_PATIENT else "session_started"),
                            actor={"role": role, "source": "web", "clinician_id": clinician_id},
                            subject={"next_path": next_path, "patient_alias_key": patient_alias_key},
                            payload={"client_ip": self.client_address[0]},
                        )
                    return
                if path == "/auth/switch-view":
                    session = self._require_roles({ROLE_PATIENT, ROLE_CLINICIAN, ROLE_DEVELOPER}, expect_json=True)
                    if session is None:
                        return
                    payload = _read_json_body(self)
                    requested_view = str(payload.get("view") or "").strip().lower()
                    if requested_view not in session.allowed_views:
                        _write_json(self, HTTPStatus.FORBIDDEN, {"error": "forbidden", "message": "View not allowed for this role."})
                        return
                    updated_token = self._reissue_session(session, active_view=requested_view)
                    _write_json(
                        self,
                        HTTPStatus.OK,
                        {"status": "ok", "session": {**_session_payload(session), "active_view": requested_view}},
                        extra_headers={"Set-Cookie": auth_manager.build_set_cookie_header(updated_token)},
                    )
                    return
                if path == "/auth/logout":
                    _write_json(
                        self,
                        HTTPStatus.OK,
                        {"status": "ok", "next_path": "/login"},
                        extra_headers={
                            "Set-Cookie": auth_manager.build_set_cookie_header("", clear=True)
                        },
                    )
                    if audit_logger is not None:
                        audit_logger.append_event(
                            stream="conversation",
                            event_type="session_ended",
                            actor={"role": "patient_portal", "source": "web"},
                            subject={"client_ip": self.client_address[0]},
                            payload={},
                        )
                    return
                if path == "/clinical/patient-tokens":
                    session = self._require_roles({ROLE_CLINICIAN, ROLE_DEVELOPER}, expect_json=True)
                    if session is None:
                        return
                    payload = _read_json_body(self)
                    action = str(payload.get("action") or "issue").strip().lower()
                    clinician_id = session.clinician_id or "developer-root"
                    if action == "revoke":
                        token_id = str(payload.get("token_id") or "").strip()
                        record = token_store.revoke_token(
                            token_id,
                            clinician_id=(clinician_id if session.role == ROLE_CLINICIAN else None),
                        )
                        if record is None:
                            _write_json(self, HTTPStatus.NOT_FOUND, {"error": "not_found", "message": "Token not found."})
                            return
                        if audit_logger is not None:
                            audit_logger.append_event(
                                stream="conversation",
                                event_type="patient_token_revoked",
                                actor={"role": session.role, "source": "web", "clinician_id": clinician_id},
                                subject={"token_id": record.token_id, "patient_alias_key": record.patient_alias_key},
                                payload={},
                            )
                        _write_json(self, HTTPStatus.OK, {"status": "ok", "token": _public_token_record(record)})
                        return

                    expires_in_days = payload.get("expires_in_days")
                    record_token, record = token_store.issue_token(
                        clinician_id=clinician_id,
                        label=str(payload.get("label") or ""),
                        patient_alias_key=_normalize_slug(payload.get("patient_alias_key"), fallback_prefix="anonpt"),
                        expires_in_days=(int(expires_in_days) if expires_in_days is not None else 30),
                    )
                    if audit_logger is not None:
                        audit_logger.append_event(
                            stream="conversation",
                            event_type="patient_token_issued",
                            actor={"role": session.role, "source": "web", "clinician_id": clinician_id},
                            subject={"token_id": record.token_id, "patient_alias_key": record.patient_alias_key},
                            payload={"label": record.label, "expires_at": record.expires_at},
                        )
                    _write_json(
                        self,
                        HTTPStatus.OK,
                        {"status": "ok", "raw_token": record_token, "token": _public_token_record(record)},
                    )
                    return
                if path in {"/runtime/text", "/runtime/audio", "/runtime/audio-upload"} and not self._ensure_authenticated(expect_json=True):
                    return
                session = self._session_identity()
                if session is None:
                    _write_json(self, HTTPStatus.UNAUTHORIZED, {"error": "auth_required"})
                    return
                if path == "/runtime/audio-upload":
                    upload_path, payload = _read_multipart_audio_upload(self)
                    try:
                        lang = str(payload.get("lang", default_lang))
                        service = _service_for_lang(services, lang)
                        patient_id, patient_identity = _resolve_runtime_identity(session, payload, token_store)
                        payload["patient_id"] = patient_id
                        payload["patient_identity"] = patient_identity
                        payload["debug"] = bool(payload.get("debug")) and session.role == ROLE_DEVELOPER and session.active_view == VIEW_DEVELOPER
                        response_payload = _sanitize_response_for_session(
                            _attach_audio_urls(process_audio_upload_payload(service, payload, upload_path)),
                            session,
                        )
                        _write_json(self, HTTPStatus.OK, response_payload)
                    finally:
                        upload_path.unlink(missing_ok=True)
                    return
                payload = _read_json_body(self)
                lang = str(payload.get("lang", default_lang))
                service = _service_for_lang(services, lang)
                patient_id, patient_identity = _resolve_runtime_identity(session, payload, token_store)
                payload["patient_id"] = patient_id
                payload["patient_identity"] = patient_identity
                payload["debug"] = bool(payload.get("debug")) and session.role == ROLE_DEVELOPER and session.active_view == VIEW_DEVELOPER
                if path == "/runtime/text":
                    _write_json(
                        self,
                        HTTPStatus.OK,
                        _sanitize_response_for_session(_attach_audio_urls(process_text_payload(service, payload)), session),
                    )
                    return
                if path == "/runtime/audio":
                    _write_json(
                        self,
                        HTTPStatus.OK,
                        _sanitize_response_for_session(_attach_audio_urls(process_audio_payload(service, payload)), session),
                    )
                    return
                _write_json(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except Exception as exc:
                _write_json(
                    self,
                    HTTPStatus.BAD_REQUEST,
                    {"error": type(exc).__name__, "message": str(exc)},
                )

        def log_message(self, format: str, *args: object) -> None:
            return

    return AdminHandler


def main() -> int:
    args = _parse_args()
    load_local_env(ROOT)
    access_settings = load_access_governance_settings(ROOT / "config")
    auth_required = "session_token" in access_settings.patient.required_auth
    auth_manager = PortalSessionAuth(
        session_ttl_seconds=int(os.getenv("PATIENT_PORTAL_SESSION_TTL_SECONDS", str(12 * 60 * 60)))
    )
    token_store = PatientTokenStore(TOKEN_STORE_PATH, secret=_load_or_create_secret(TOKEN_STORE_SECRET_PATH))
    supported_languages = _supported_languages()
    if args.lang not in supported_languages:
        raise SystemExit(f"Unsupported default language: {args.lang}")
    services = {lang: RuntimeService(ROOT, lang) for lang in supported_languages}
    server = ThreadingHTTPServer(
        (args.host, args.port),
        build_handler(services, args.lang, auth_manager, auth_required, token_store),
    )
    print(f"Admin API listening on http://{args.host}:{args.port}")
    print(f"Patient portal auth required: {auth_required}")
    if auth_required and auth_manager.uses_default_access_code:
        print(f"Local demo access code: {DEFAULT_ACCESS_CODE}")
    print("GET /")
    print("GET /chat")
    print("GET /chat/mobile")
    print("GET /login")
    print("GET /ui-config")
    print("GET /auth/session/context")
    print("GET /health")
    print("GET /operations")
    print("GET /clinical/patient-tokens")
    print("GET /runtime/generated-audio/<file>")
    print("POST /auth/session")
    print("POST /auth/switch-view")
    print("POST /auth/logout")
    print("POST /clinical/patient-tokens")
    print("POST /runtime/text")
    print("POST /runtime/audio")
    print("POST /runtime/audio-upload")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())