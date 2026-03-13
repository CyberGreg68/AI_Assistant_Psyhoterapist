from assistant_runtime.session_auth import DEFAULT_ACCESS_CODE
from assistant_runtime.session_auth import PortalSessionAuth
from assistant_runtime.session_auth import SESSION_COOKIE_NAME


def test_session_auth_validates_access_code_and_parses_token() -> None:
    auth = PortalSessionAuth(access_code="demo-code", secret="fixed-secret", session_ttl_seconds=3600)

    assert auth.validate_access_code("demo-code") is True
    assert auth.validate_access_code("wrong") is False

    token = auth.issue_session_token(subject="patient")
    identity = auth.parse_session_token(token)

    assert identity is not None
    assert identity.subject == "patient"
    assert identity.role == "patient"
    assert identity.allowed_views == ("patient",)
    assert identity.active_view == "patient"
    assert identity.expires_at > identity.issued_at


def test_session_auth_can_issue_role_aware_token() -> None:
    auth = PortalSessionAuth(access_code="demo-code", secret="fixed-secret", session_ttl_seconds=3600)

    token = auth.issue_session_token(
        subject="clinician-session",
        role="clinician",
        allowed_views=("clinician", "patient"),
        active_view="clinician",
        clinician_id="dr-kovacs",
    )
    identity = auth.parse_session_token(token)

    assert identity is not None
    assert identity.role == "clinician"
    assert identity.allowed_views == ("clinician", "patient")
    assert identity.active_view == "clinician"
    assert identity.clinician_id == "dr-kovacs"


def test_session_auth_rejects_tampered_token() -> None:
    auth = PortalSessionAuth(access_code="demo-code", secret="fixed-secret", session_ttl_seconds=3600)
    token = auth.issue_session_token()
    tampered = token[:-1] + ("0" if token[-1] != "0" else "1")

    assert auth.parse_session_token(tampered) is None


def test_session_auth_cookie_headers_cover_set_and_clear() -> None:
    auth = PortalSessionAuth(access_code=DEFAULT_ACCESS_CODE, secret="fixed-secret", session_ttl_seconds=900)
    token = auth.issue_session_token()

    set_cookie = auth.build_set_cookie_header(token)
    clear_cookie = auth.build_set_cookie_header("", clear=True)

    assert SESSION_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Max-Age=900" in set_cookie
    assert "Max-Age=0" in clear_cookie