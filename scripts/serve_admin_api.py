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
            --canvas: #f7f1e8;
            --panel: rgba(255, 250, 243, 0.9);
            --panel-soft: rgba(255, 250, 243, 0.72);
            --ink: #1f2928;
            --muted: #5e6c6a;
            --line: rgba(31, 41, 40, 0.12);
            --accent: #0f766a;
            --accent-soft: rgba(15, 118, 106, 0.12);
            --warm: #d88c29;
            --patient: #efe2cf;
            --assistant: #dff1ea;
            --system: #f8f1e4;
            --alert: #b4352d;
            --shadow: 0 22px 70px rgba(78, 62, 39, 0.15);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background:
                radial-gradient(circle at top left, rgba(216, 140, 41, 0.22), transparent 28%),
                radial-gradient(circle at bottom right, rgba(15, 118, 106, 0.17), transparent 30%),
                linear-gradient(145deg, #faf6ef 0%, #efe5d5 45%, var(--canvas) 100%);
            color: var(--ink);
            font-family: Georgia, "Times New Roman", serif;
    }}
        body::before,
        body::after {{
            content: "";
            position: fixed;
            width: 36vw;
            height: 36vw;
            border-radius: 999px;
            filter: blur(42px);
            opacity: 0.45;
            pointer-events: none;
        }}
        body::before {{
            top: -8vw;
            left: -10vw;
            background: radial-gradient(circle, rgba(216, 140, 41, 0.34), transparent 68%);
        }}
        body::after {{
            right: -8vw;
            bottom: -12vw;
            background: radial-gradient(circle, rgba(15, 118, 106, 0.28), transparent 70%);
        }}
        .shell {{
            position: relative;
            z-index: 1;
            width: min(1240px, calc(100vw - 28px));
            min-height: 100vh;
            margin: 0 auto;
            padding: 18px 0 24px;
            display: grid;
            grid-template-columns: minmax(0, 1.05fr) minmax(360px, 0.95fr);
            gap: 18px;
            align-items: center;
        }}
        .panel {{
      border: 1px solid var(--line);
            border-radius: 26px;
            background: var(--panel);
            -webkit-backdrop-filter: blur(14px);
            backdrop-filter: blur(14px);
      box-shadow: var(--shadow);
    }}
        .scene {{
            position: relative;
            overflow: hidden;
            min-height: 680px;
            padding: 28px;
            display: grid;
            grid-template-rows: auto auto auto 1fr;
            gap: 18px;
        }}
        .scene::before {{
            content: "PSY";
            position: absolute;
            right: -10px;
            top: 26px;
            opacity: 0.07;
            font: 700 min(12vw, 108px)/0.9 Georgia, serif;
            letter-spacing: 0.08em;
            color: var(--ink);
        }}
        .hero-row,
        .status-row,
        .quickbar,
        .portal-links,
        .brand-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            align-items: center;
            justify-content: space-between;
        }}
    .eyebrow {{
            margin: 0;
      font: 700 12px/1.2 "Courier New", monospace;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--accent);
    }}
        .brand-lockup {{
            display: flex;
            gap: 14px;
            align-items: center;
        }}
        .logo-badge {{
            width: 72px;
            height: 72px;
            border-radius: 22px;
            border: 1px solid rgba(31, 41, 40, 0.08);
            background: rgba(255, 255, 255, 0.58);
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.34);
            padding: 10px;
            object-fit: contain;
        }}
        .brand-copy {{
            display: grid;
            gap: 5px;
        }}
        .brand-title {{
            margin: 0;
            font-size: 21px;
            line-height: 1.1;
        }}
        .status-pill,
        .chip,
        .portal-link,
        .bubble-head,
        label,
        button {{
            font: 700 11px/1.2 "Courier New", monospace;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }}
        .status-pill,
        .chip,
        .portal-link {{
            border-radius: 999px;
            padding: 8px 12px;
            border: 1px solid rgba(31, 41, 40, 0.08);
            background: rgba(255, 255, 255, 0.62);
            color: var(--muted);
            text-decoration: none;
        }}
        .status-pill {{ color: var(--warm); }}
        .portal-link.primary-link {{
            background: var(--accent);
            border-color: transparent;
            color: #fffdf9;
        }}
        .hero-copy {{
            position: relative;
            z-index: 1;
            max-width: 58ch;
        }}
        h1 {{
            margin: 0 0 12px;
            font-size: clamp(36px, 5vw, 62px);
            line-height: 0.98;
            max-width: 10ch;
        }}
        p {{
            margin: 0;
            color: var(--muted);
            line-height: 1.58;
        }}
        .lede {{
            font-size: 17px;
            max-width: 54ch;
        }}
        .trust-banner {{
            border: 1px solid var(--line);
            border-radius: 20px;
            padding: 14px 16px;
            display: flex;
            align-items: start;
            justify-content: space-between;
            gap: 14px;
            background: rgba(255, 255, 255, 0.6);
        }}
        .trust-banner strong {{
            display: block;
            margin-bottom: 4px;
            color: var(--ink);
        }}
        .preview-panel {{
            position: relative;
            z-index: 1;
            display: grid;
            gap: 12px;
            align-content: start;
            padding: 18px;
            border-radius: 24px;
            background:
                linear-gradient(to bottom, rgba(255,255,255,0.12), rgba(255,255,255,0.02)),
                repeating-linear-gradient(180deg, transparent, transparent 25px, rgba(31,41,40,0.026) 26px);
            border: 1px solid rgba(31, 41, 40, 0.08);
        }}
        .bubble {{
            max-width: min(560px, 95%);
            padding: 16px 18px;
            border-radius: 22px;
            border: 1px solid var(--line);
            box-shadow: 0 12px 28px rgba(31, 41, 40, 0.06);
        }}
        .bubble.patient {{
            margin-left: auto;
            background: var(--patient);
            border-bottom-right-radius: 8px;
        }}
        .bubble.assistant {{
            background: var(--assistant);
            border-bottom-left-radius: 8px;
        }}
        .bubble.system {{
            background: var(--system);
            max-width: 100%;
        }}
        .bubble-head {{
            margin-bottom: 10px;
            color: var(--muted);
        }}
        .bubble-body {{
            white-space: pre-wrap;
            line-height: 1.6;
            word-break: break-word;
        }}
        .login-card {{
            padding: 28px;
            display: grid;
            gap: 18px;
        }}
        .support-box {{
            border-radius: 18px;
            border: 1px solid rgba(31, 41, 40, 0.08);
            background: linear-gradient(135deg, var(--accent-soft), rgba(255,255,255,0.45));
            padding: 14px 16px;
        }}
        .support-box strong {{
            display: block;
            margin-bottom: 4px;
            color: var(--ink);
        }}
        .field-grid {{
            display: grid;
            gap: 12px;
        }}
        label {{
            display: grid;
            gap: 8px;
            color: var(--muted);
        }}
    input, button {{ font: inherit; }}
    input {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 13px 14px;
            background: rgba(255,255,255,0.82);
      color: var(--ink);
            box-shadow: inset 0 1px 0 rgba(255,255,255,0.3);
    }}
        input:focus {{
            outline: 2px solid rgba(15,118,106,0.18);
            border-color: rgba(15,118,106,0.28);
        }}
    button {{
      width: 100%;
      border: 0;
      border-radius: 16px;
      padding: 14px 16px;
            background: linear-gradient(135deg, #0f766a, #145b63);
      color: #fff;
      cursor: pointer;
            box-shadow: 0 16px 30px rgba(15,118,106,0.18);
        }}
        button:hover {{
            filter: brightness(1.03);
            transform: translateY(-1px);
        }}
        button:disabled {{
            cursor: wait;
            opacity: 0.8;
    }}
        .hint {{
            color: var(--ink);
            background: rgba(23,111,103,0.08);
            border: 1px solid rgba(23,111,103,0.14);
            border-radius: 16px;
            padding: 12px 14px;
        }}
        .error {{
            min-height: 20px;
            color: var(--alert);
            margin-top: 6px;
        }}
        .footer-note {{
            padding-top: 14px;
            border-top: 1px solid rgba(31,41,40,0.08);
            font-size: 14px;
        }}
        @media (max-width: 980px) {{
            .shell {{
                width: min(100vw - 20px, 760px);
                grid-template-columns: 1fr;
                padding: 10px 0 20px;
            }}
            .scene {{ min-height: auto; }}
            .scene::before {{ font-size: 108px; }}
            h1 {{ max-width: none; }}
        }}
        @media (max-width: 640px) {{
            .shell {{ width: min(100vw - 16px, 100%); gap: 14px; }}
            .scene,
            .login-card {{ padding: 18px; }}
            .hero-row,
            .brand-row,
            .portal-links {{
                align-items: flex-start;
                flex-direction: column;
            }}
            .brand-lockup {{ align-items: flex-start; }}
            .logo-badge {{ width: 60px; height: 60px; border-radius: 18px; }}
            .quickbar {{ gap: 8px; }}
            .status-pill,
            .chip,
            .portal-link {{ width: fit-content; }}
            .bubble {{ max-width: 100%; }}
            h1 {{ font-size: 32px; }}
            .lede {{ font-size: 16px; }}
        }}
  </style>
</head>
<body>
    <main class=\"shell\">
        <section class=\"scene panel\">
            <div class=\"hero-row\">
                <div class=\"brand-lockup\">
                    <img class=\"logo-badge\" src=\"/assets/patient_portal_logo.svg\" alt=\"Psychotherapist Assistant logo\">
                    <div class=\"brand-copy\">
                        <p class=\"eyebrow\">Patient Portal</p>
                        <p class=\"brand-title\">Psychotherapist Assistant</p>
                    </div>
                </div>
                <div class=\"status-pill\">Protected Session</div>
            </div>
            <div class=\"hero-copy\">
                <h1>Védett belépés a terápiás felülethez</h1>
                <p class=\"lede\">A belépési pont most már ugyanabból a meleg, üveges, nyugodt vizuális rendszerből épül, mint a chatfelület: ugyanaz a tónus, ugyanaz a tipográfiai ritmus, ugyanaz a gondosan visszafogott terápiás hangulat.</p>
            </div>
            <div class=\"trust-banner\">
                <div>
                    <strong>Nyugodt, biztonságos belépési pont</strong>
                    <p>A session tokenes védelem a chatet, a voice-demót és a review-alapú működést is ugyanabban a védett kontextusban tartja.</p>
                </div>
                <div class=\"chip\">Audit-ready</div>
            </div>
            <div class=\"quickbar\">
                <div class=\"chip\">Session token</div>
                <div class=\"chip\">Voice demo</div>
                <div class=\"chip\">Review-gated</div>
                <div class=\"chip\">Local-first runtime</div>
            </div>
            <div class=\"preview-panel\">
                <div class=\"portal-links\">
                    <p class=\"eyebrow\">Interface Preview</p>
                    <div class=\"portal-links\">
                        <a class=\"portal-link primary-link\" href=\"/chat\">Desktop Chat</a>
                        <a class=\"portal-link\" href=\"/chat/mobile\">Mobile Chat</a>
                    </div>
                </div>
                <article class=\"bubble assistant\">
                    <div class=\"bubble-head\">Assistant</div>
                    <div class=\"bubble-body\">Örülök, hogy itt vagy. A mai beszélgetést nyugodt tempóban visszük, és végig jelezni fogom, mi történik helyben és mi igényel külön jóváhagyást.</div>
                </article>
                <article class=\"bubble patient\">
                    <div class=\"bubble-head\">Patient</div>
                    <div class=\"bubble-body\">Szeretném gyorsan átlátni, hogy biztonságban marad-e a session, ha szöveget és hangot is kipróbálok.</div>
                </article>
                <article class=\"bubble system\">
                    <div class=\"bubble-head\">System</div>
                    <div class=\"bubble-body\">Portal access active. A belépés után a desktop és mobil nézet ugyanazzal a sessionnel használható.</div>
                </article>
            </div>
        </section>
        <section class=\"login-card panel\">
            <div class=\"brand-row\">
                <div>
                    <p class=\"eyebrow\">Session Access</p>
                    <h2 class=\"brand-title\">Belépés a betegportálra</h2>
                </div>
                <div class=\"status-pill\">Desktop + Mobile</div>
            </div>
            <div class=\"support-box\">
                <strong>Azonos arculat, kevesebb törés a flow-ban</strong>
                <p>A belépőoldal most már nem különálló technikai képernyő, hanem ugyanannak a betegoldali élménynek az első állomása.</p>
            </div>
            {demo_hint}
            <div class=\"field-grid\">
                <label for=\"access-code\">Access Code</label>
                <input id=\"access-code\" type=\"password\" autocomplete=\"current-password\" placeholder=\"Portal access code\">
            </div>
            <button id=\"login-button\" type=\"button\">Belépés</button>
            <p id=\"login-error\" class=\"error\"></p>
            <p class=\"footer-note\">A jelenlegi nézet külön optimalizált keskeny kijelzőre is, és közvetlenül ugyanazt a vizuális készletet használja, mint a desktop és mobil chatfelület.</p>
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