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
from assistant_runtime.live.runtime_service import RuntimeService
from assistant_runtime.live.session_auth import DEFAULT_ACCESS_CODE
from assistant_runtime.live.session_auth import PortalSessionAuth
from assistant_runtime.live.session_auth import SESSION_COOKIE_NAME


WEB_DIR = ROOT / "web"
GENERATED_AUDIO_DIR = ROOT / "data" / "runtime_state" / "generated_audio"
UPLOADED_AUDIO_DIR = ROOT / "data" / "runtime_state" / "uploaded_audio"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve a thin admin API for operations snapshot and runtime testing.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--lang", default="hu")
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


def _render_login_page(auth_manager: PortalSessionAuth, next_path: str) -> str:
    demo_hint = ""
    if auth_manager.uses_default_access_code:
        demo_hint = (
            f"<p class=\"hint\">Local demo access code: <strong>{escape(DEFAULT_ACCESS_CODE)}</strong></p>"
        )
    escaped_next = escape(next_path, quote=True)
    return f"""<!DOCTYPE html>
<html lang=\"hu\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Patient Portal Login</title>
  <style>
    :root {{
      --bg: #f4ede4;
      --panel: rgba(255, 250, 244, 0.94);
      --ink: #213332;
      --muted: #61716c;
      --accent: #176f67;
      --line: rgba(33, 51, 50, 0.12);
      --shadow: 0 24px 64px rgba(88, 69, 43, 0.16);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 20px;
      background:
        radial-gradient(circle at top left, rgba(23, 111, 103, 0.16), transparent 30%),
        radial-gradient(circle at bottom right, rgba(185, 122, 54, 0.18), transparent 28%),
        linear-gradient(145deg, #fbf7f1 0%, var(--bg) 100%);
      color: var(--ink);
      font-family: Georgia, serif;
    }}
    .card {{
      width: min(460px, 100%);
      padding: 26px;
      border-radius: 28px;
      border: 1px solid var(--line);
      background: var(--panel);
      box-shadow: var(--shadow);
    }}
    .eyebrow {{
      margin: 0 0 12px;
      font: 700 12px/1.2 "Courier New", monospace;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--accent);
    }}
    h1 {{ margin: 0 0 10px; font-size: 32px; line-height: 1.06; }}
    p {{ margin: 0 0 14px; color: var(--muted); line-height: 1.55; }}
    label {{ display: grid; gap: 8px; font: 700 11px/1.2 "Courier New", monospace; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); }}
    input, button {{ font: inherit; }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 13px 14px;
      background: rgba(255,255,255,0.86);
      color: var(--ink);
      margin-bottom: 14px;
    }}
    button {{
      width: 100%;
      border: 0;
      border-radius: 16px;
      padding: 14px 16px;
      background: var(--accent);
      color: #fff;
      cursor: pointer;
      font: 700 12px/1.1 "Courier New", monospace;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .hint {{ color: var(--ink); background: rgba(23,111,103,0.1); border: 1px solid rgba(23,111,103,0.14); border-radius: 16px; padding: 12px 14px; }}
    .error {{ min-height: 20px; color: #b23a2d; margin-top: 12px; }}
  </style>
</head>
<body>
  <section class=\"card\">
    <p class=\"eyebrow\">Patient Portal</p>
    <h1>Vedett belepes</h1>
    <p>A portal csak session tokennel erheto el. A belepes utan a chat- es voice-demo ugyanebben a sessionben marad vedett.</p>
    {demo_hint}
    <label for=\"access-code\">Access Code</label>
    <input id=\"access-code\" type=\"password\" autocomplete=\"current-password\" placeholder=\"Portal access code\">
    <button id=\"login-button\" type=\"button\">Belepes</button>
    <p id=\"login-error\" class=\"error\"></p>
  </section>
  <script>
    const accessCodeInput = document.getElementById('access-code');
    const loginButton = document.getElementById('login-button');
    const errorNode = document.getElementById('login-error');
    async function submitLogin() {{
      errorNode.textContent = '';
      loginButton.disabled = true;
      try {{
        const response = await fetch('/auth/session', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ access_code: accessCodeInput.value, next_path: '{escaped_next}' }})
        }});
        const payload = await response.json();
        if (!response.ok) {{
          errorNode.textContent = payload.message || 'Login failed';
          return;
        }}
        window.location.href = payload.next_path || '/chat';
      }} catch (error) {{
        errorNode.textContent = error.message || 'Network error';
      }} finally {{
        loginButton.disabled = false;
      }}
    }}
    loginButton.addEventListener('click', submitLogin);
    accessCodeInput.addEventListener('keydown', (event) => {{
      if (event.key === 'Enter') {{
        event.preventDefault();
        submitLogin();
      }}
    }});
    accessCodeInput.focus();
  </script>
</body>
</html>
"""


def build_handler(
    services: dict[str, RuntimeService],
    default_lang: str,
    auth_manager: PortalSessionAuth,
    auth_required: bool,
) -> type[BaseHTTPRequestHandler]:
    audit_logger = next(
        (service.audit_logger for service in services.values() if service.audit_logger is not None),
        None,
    )

    class AdminHandler(BaseHTTPRequestHandler):
        def _session_identity(self):
            cookies = _parse_cookies(self.headers.get("Cookie"))
            return auth_manager.parse_session_token(cookies.get(SESSION_COOKIE_NAME))

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
                _write_html(self, HTTPStatus.OK, _render_login_page(auth_manager, next_path))
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
                _write_json(
                    self,
                    HTTPStatus.OK,
                    {
                        "supported_languages": _supported_languages(),
                        "default_language": default_lang,
                        "auth_required": auth_required,
                    },
                )
                return
            if path == "/health":
                _write_json(self, HTTPStatus.OK, health_payload(ROOT))
                return
            if path == "/operations":
                if not self._ensure_authenticated(expect_json=True):
                    return
                _write_json(self, HTTPStatus.OK, operations_payload(ROOT))
                return
            _write_json(self, HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:
            try:
                parsed = urlparse(self.path)
                path = parsed.path
                if path == "/auth/session":
                    payload = _read_json_body(self)
                    access_code = str(payload.get("access_code", ""))
                    next_path = _sanitize_next_path(str(payload.get("next_path", "/chat")))
                    if not auth_manager.validate_access_code(access_code):
                        if audit_logger is not None:
                            audit_logger.append_event(
                                stream="conversation",
                                event_type="portal_login_failed",
                                actor={"role": "patient_portal", "source": "web"},
                                subject={"next_path": next_path},
                                payload={"client_ip": self.client_address[0]},
                            )
                        _write_json(
                            self,
                            HTTPStatus.UNAUTHORIZED,
                            {"error": "invalid_access_code", "message": "Invalid access code."},
                        )
                        return
                    token = auth_manager.issue_session_token()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Set-Cookie", auth_manager.build_set_cookie_header(token))
                    body = json.dumps(
                        {"status": "ok", "next_path": next_path},
                        ensure_ascii=False,
                        indent=2,
                    ).encode("utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    if audit_logger is not None:
                        audit_logger.append_event(
                            stream="conversation",
                            event_type="session_started",
                            actor={"role": "patient_portal", "source": "web"},
                            subject={"next_path": next_path},
                            payload={"client_ip": self.client_address[0]},
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
                if path in {"/runtime/text", "/runtime/audio", "/runtime/audio-upload"} and not self._ensure_authenticated(expect_json=True):
                    return
                if path == "/runtime/audio-upload":
                    upload_path, payload = _read_multipart_audio_upload(self)
                    try:
                        lang = str(payload.get("lang", default_lang))
                        service = _service_for_lang(services, lang)
                        response_payload = _attach_audio_urls(
                            process_audio_upload_payload(service, payload, upload_path)
                        )
                        _write_json(self, HTTPStatus.OK, response_payload)
                    finally:
                        upload_path.unlink(missing_ok=True)
                    return
                payload = _read_json_body(self)
                lang = str(payload.get("lang", default_lang))
                service = _service_for_lang(services, lang)
                if path == "/runtime/text":
                    _write_json(self, HTTPStatus.OK, _attach_audio_urls(process_text_payload(service, payload)))
                    return
                if path == "/runtime/audio":
                    _write_json(self, HTTPStatus.OK, _attach_audio_urls(process_audio_payload(service, payload)))
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
    supported_languages = _supported_languages()
    if args.lang not in supported_languages:
        raise SystemExit(f"Unsupported default language: {args.lang}")
    services = {lang: RuntimeService(ROOT, lang) for lang in supported_languages}
    server = ThreadingHTTPServer(
        (args.host, args.port),
        build_handler(services, args.lang, auth_manager, auth_required),
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
    print("GET /health")
    print("GET /operations")
    print("GET /runtime/generated-audio/<file>")
    print("POST /auth/session")
    print("POST /auth/logout")
    print("POST /runtime/text")
    print("POST /runtime/audio")
    print("POST /runtime/audio-upload")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())