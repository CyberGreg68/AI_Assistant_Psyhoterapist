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
    <title>Psychotherapist Assistant Portal</title>
  <style>
    :root {{
            --page: #ededed;
            --surface: #ffffff;
            --ink: #676767;
            --ink-strong: #525252;
            --muted: #8b8b8b;
            --line: #e1e1e1;
            --accent: #4fc3d7;
            --accent-strong: #36b2c7;
            --accent-soft: rgba(79, 195, 215, 0.12);
            --shadow: 0 18px 34px rgba(0, 0, 0, 0.05);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
            background: var(--page);
            color: var(--ink);
            font-family: "Trebuchet MS", "Segoe UI", Arial, sans-serif;
    }}
        .page {{
            width: min(1180px, calc(100vw - 32px));
            margin: 0 auto;
            padding: 16px 0 42px;
        }}
        .topbar {{
            background: var(--surface);
            border-bottom: 1px solid var(--line);
        }}
        .topbar-inner {{
            width: min(1180px, calc(100vw - 32px));
            margin: 0 auto;
            padding: 20px 0 16px;
            display: flex;
            align-items: end;
            justify-content: space-between;
            gap: 20px;
        }}
        .brand {{
            display: grid;
            gap: 10px;
        }}
        .brand-logo {{
            width: 184px;
            height: auto;
            display: block;
        }}
        .nav {{
            display: flex;
            align-items: center;
            gap: 20px;
            flex-wrap: wrap;
            padding-bottom: 8px;
        }}
        .nav a {{
            color: var(--ink-strong);
            text-decoration: none;
            font-size: 14px;
            font-weight: 400;
        }}
        .nav a:first-child,
        .nav a:hover {{
            color: var(--accent-strong);
        }}
        .hero-strip {{
            display: grid;
            grid-template-columns: 0.9fr 1.9fr;
            gap: 0;
            margin-top: 28px;
            min-height: 280px;
            background: var(--surface);
            overflow: hidden;
        }}
        .hero-aside,
        .hero-main {{
            position: relative;
            min-height: 280px;
        }}
        .hero-aside {{
            background:
                linear-gradient(180deg, rgba(255,255,255,0.22), rgba(255,255,255,0)),
                linear-gradient(135deg, rgba(102, 142, 170, 0.55), rgba(215, 210, 205, 0.2)),
                radial-gradient(circle at 35% 26%, rgba(243, 231, 223, 0.95), rgba(170, 139, 121, 0.22) 45%, transparent 55%),
                linear-gradient(180deg, #c7b5a6 0%, #7e8d95 100%);
        }}
        .hero-main {{
            background:
                linear-gradient(180deg, rgba(255,255,255,0.12), rgba(255,255,255,0.02)),
                linear-gradient(0deg, rgba(114, 96, 74, 0.42) 0 22%, transparent 22%),
                radial-gradient(circle at 66% 28%, rgba(244, 237, 227, 0.82), rgba(172, 149, 127, 0.18) 18%, transparent 19%),
                radial-gradient(circle at 72% 18%, rgba(208, 190, 166, 0.5), transparent 11%),
                linear-gradient(120deg, rgba(115, 120, 90, 0.48), rgba(129, 112, 88, 0.18) 40%, rgba(166, 180, 190, 0.22)),
                linear-gradient(180deg, #776d5e 0%, #a89d8f 48%, #d1d0cb 100%);
        }}
        .hero-main::before {{
            content: "";
            position: absolute;
            inset: 0;
            background: repeating-linear-gradient(90deg, rgba(65, 82, 51, 0.12), rgba(65, 82, 51, 0.12) 4px, transparent 4px, transparent 48px);
            opacity: 0.3;
            mix-blend-mode: multiply;
        }}
        .hero-logo-card {{
            position: absolute;
            left: 18%;
            top: 28%;
            width: 190px;
            padding: 26px 22px 18px;
            background: var(--accent-strong);
            box-shadow: var(--shadow);
        }}
        .hero-logo-card img {{
            display: block;
            width: 100%;
            height: auto;
            filter: brightness(0) invert(1);
        }}
        .quote {{
            width: min(820px, calc(100vw - 96px));
            margin: 34px auto 0;
            color: #777;
            font-size: 18px;
            line-height: 1.7;
            font-style: italic;
            font-family: Georgia, "Times New Roman", serif;
        }}
        .quote cite {{
            display: block;
            margin-top: 10px;
            color: var(--muted);
            font-size: 16px;
            font-style: italic;
        }}
        .content {{
            width: min(820px, calc(100vw - 96px));
            margin: 58px auto 0;
            display: grid;
            grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
            gap: 44px;
            align-items: start;
        }}
        .copy h1,
        .login-card h2 {{
            margin: 0 0 18px;
            color: var(--ink-strong);
            font-size: 33px;
            font-weight: 300;
            letter-spacing: 0.01em;
        }}
        .copy p {{
            margin: 0 0 18px;
            font-size: 15px;
            line-height: 1.6;
        }}
        .signature {{
            margin-top: 28px;
            color: var(--ink-strong);
        }}
        .login-card {{
            background: var(--surface);
      border: 1px solid var(--line);
            padding: 28px;
            box-shadow: var(--shadow);
        }}
        .eyebrow {{
            margin: 0 0 12px;
            color: var(--accent-strong);
            text-transform: uppercase;
            letter-spacing: 0.22em;
            font-size: 11px;
            font-weight: 600;
        }}
        .support-box {{
            margin-bottom: 18px;
            padding: 15px 16px;
            background: linear-gradient(180deg, rgba(79,195,215,0.1), rgba(79,195,215,0.04));
            border-left: 3px solid var(--accent);
            color: var(--ink);
        }}
        .support-box strong {{
            display: block;
            margin-bottom: 6px;
            color: var(--ink-strong);
            font-weight: 600;
        }}
        .field-grid {{
            display: grid;
            gap: 10px;
        }}
        label {{
            color: var(--muted);
            font-size: 12px;
            letter-spacing: 0.14em;
            text-transform: uppercase;
        }}
        input,
        button {{
            font: inherit;
        }}
        input {{
            width: 100%;
            padding: 13px 14px;
            border: 1px solid #d6d6d6;
            background: #fbfbfb;
            color: var(--ink-strong);
        }}
        input:focus {{
            outline: none;
            border-color: var(--accent);
            box-shadow: 0 0 0 4px var(--accent-soft);
        }}
        button {{
            width: 100%;
            margin-top: 16px;
            border: 0;
            padding: 14px 16px;
            background: var(--accent-strong);
            color: white;
            text-transform: uppercase;
            letter-spacing: 0.18em;
            font-size: 12px;
            cursor: pointer;
        }}
        button:hover {{
            background: #29a7bd;
        }}
        button:disabled {{
            opacity: 0.8;
            cursor: wait;
        }}
        .hint {{
            margin: 0 0 14px;
            padding: 12px 14px;
            background: #f7fcfd;
            border: 1px solid rgba(79, 195, 215, 0.22);
            color: var(--ink);
            font-size: 14px;
        }}
        .error {{
            min-height: 20px;
            margin: 12px 0 0;
            color: #b95e5e;
            font-size: 14px;
        }}
        .footer-note {{
            margin-top: 18px;
            padding-top: 16px;
            border-top: 1px solid var(--line);
            font-size: 13px;
            line-height: 1.55;
            color: var(--muted);
        }}
        .mini-links {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px 16px;
            margin-top: 16px;
            font-size: 13px;
        }}
        .mini-links a {{
            color: var(--accent-strong);
            text-decoration: none;
        }}
        .mini-links a:hover {{
            text-decoration: underline;
        }}
        @media (max-width: 900px) {{
            .topbar-inner,
            .page,
            .quote,
            .content {{
                width: min(100vw - 24px, 100%);
            }}
            .topbar-inner {{
                padding-left: 0;
                padding-right: 0;
                align-items: flex-start;
                flex-direction: column;
            }}
            .hero-strip,
            .content {{
                grid-template-columns: 1fr;
            }}
            .hero-logo-card {{
                left: 50%;
                transform: translateX(-50%);
            }}
        }}
        @media (max-width: 640px) {{
            .page {{
                width: min(100vw - 16px, 100%);
                padding-bottom: 28px;
            }}
            .topbar-inner {{
                width: min(100vw - 16px, 100%);
            }}
            .brand-logo {{
                width: 156px;
            }}
            .nav {{
                gap: 12px;
            }}
            .nav a {{
                font-size: 13px;
            }}
            .hero-aside,
            .hero-main {{
                min-height: 220px;
            }}
            .hero-logo-card {{
                width: 156px;
                padding: 22px 16px 16px;
            }}
            .quote,
            .content {{
                width: min(100vw - 40px, 100%);
            }}
            .copy h1,
            .login-card h2 {{
                font-size: 27px;
            }}
        }}
  </style>
</head>
<body>
    <header class=\"topbar\">
        <div class=\"topbar-inner\">
            <div class=\"brand\">
                <img class=\"brand-logo\" src=\"/assets/patient_portal_logo.svg\" alt=\"AARE inspired therapy portal logo\">
            </div>
            <nav class=\"nav\" aria-label=\"Patient portal sections\">
                <a href=\"/login?next=/chat\">Home</a>
                <a href=\"/chat\">Patient Chat</a>
                <a href=\"/chat/mobile\">Mobile</a>
                <a href=\"/operations\">Operations</a>
                <a href=\"/health\">Health</a>
            </nav>
        </div>
    </header>
    <main class=\"page\">
        <section class=\"hero-strip\" aria-hidden=\"true\">
            <div class=\"hero-aside\"></div>
            <div class=\"hero-main\">
                <div class=\"hero-logo-card\">
                    <img src=\"/assets/patient_portal_logo.svg\" alt=\"\">
                </div>
            </div>
        </section>
        <blockquote class=\"quote\">
            „A gondolatok tere is olyan, mint egy kert: amit figyelemmel gondozunk, abból lassan forma, ritmus és belső rend lesz.”
            <cite>Patient Portal intro</cite>
        </blockquote>
        <section class=\"content\">
            <article class=\"copy\">
                <h1>Üdvözöljük</h1>
                <p>Ez a belépőnézet most már a referenciaoldal könnyű, nyitott vizuális nyelvét használja: világos háttér, visszafogott szürke szöveg, türkiz akcentusok és szellős tördelés fogadja a felhasználót már az első képernyőn.</p>
                <p>A cél itt nem egy technikai gate képernyő hangsúlyozása, hanem egy nyugodt, professzionális és bizalomkeltő átmenet a betegportál felé. A login után a chat és a mobil felület ugyanebben a védett sessionben marad.</p>
                <p>Ha a következő körben még közelebb akarod húzni az eredetihez, tovább tudom vinni a navigációs kiosztást, a hero arányokat és akár külön desktop/mobile vizuális variánst is.</p>
                <p class=\"signature\">Psychotherapist Assistant</p>
            </article>
            <aside class=\"login-card\">
                <p class=\"eyebrow\">Védett hozzáférés</p>
                <h2>Belépés a betegportálra</h2>
                <div class=\"support-box\">
                    <strong>Az eredeti oldal vizuális logikájára hangolva</strong>
                    <p>A logó, a türkiz tónusok és a könnyedebb sans tipográfia most már közvetlenül ebben a nézetben is megjelennek.</p>
                </div>
                {demo_hint}
                <div class=\"field-grid\">
                    <label for=\"access-code\">Belépési kód</label>
                    <input id=\"access-code\" type=\"password\" autocomplete=\"current-password\" placeholder=\"Portál-hozzáférési kód\">
                </div>
                <button id=\"login-button\" type=\"button\">Belépés</button>
                <p id=\"login-error\" class=\"error\"></p>
                <div class=\"mini-links\">
                    <a href=\"/chat\">Asztali chat előnézet</a>
                    <a href=\"/chat/mobile\">Mobil előnézet</a>
                </div>
                <p class=\"footer-note\">A mostani állapot már a feltöltött referencia betűhangulatát, logóirányát és színvilágát használja, nem a korábbi saját márkajelzést.</p>
            </aside>
        </section>
    </main>
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
          errorNode.textContent = payload.message || 'A belépés nem sikerült';
          return;
        }}
        window.location.href = payload.next_path || '/chat';
      }} catch (error) {{
        errorNode.textContent = error.message || 'Hálózati hiba';
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
            if path == "/assets/patient_portal_logo.svg":
                _write_file(self, WEB_DIR / "patient_portal_logo.svg")
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