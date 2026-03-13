from pathlib import Path

from assistant_runtime.live.patient_tokens import PatientTokenStore


def test_patient_token_store_issues_and_resolves_token(tmp_path: Path) -> None:
    store = PatientTokenStore(tmp_path / "patient_tokens.json", secret="fixed-secret")

    raw_token, record = store.issue_token(clinician_id="dr-kovacs", label="private-ref", expires_in_days=7)
    resolved = store.resolve_token(raw_token)

    assert record.patient_alias_key.startswith("anonpt_")
    assert resolved is not None
    assert resolved.token_id == record.token_id
    assert resolved.clinician_id == "dr-kovacs"
    assert resolved.label == "private-ref"
    assert resolved.token_hash != raw_token


def test_patient_token_store_restricts_alias_access_by_clinician(tmp_path: Path) -> None:
    store = PatientTokenStore(tmp_path / "patient_tokens.json", secret="fixed-secret")

    _, record = store.issue_token(
        clinician_id="dr-kovacs",
        label="private-ref",
        patient_alias_key="anonpt_shared_1",
        expires_in_days=7,
    )

    assert store.clinician_can_access_alias("dr-kovacs", record.patient_alias_key) is True
    assert store.clinician_can_access_alias("dr-nagy", record.patient_alias_key) is False


def test_patient_token_store_can_revoke_token(tmp_path: Path) -> None:
    store = PatientTokenStore(tmp_path / "patient_tokens.json", secret="fixed-secret")

    raw_token, record = store.issue_token(clinician_id="dr-kovacs", expires_in_days=7)
    revoked = store.revoke_token(record.token_id, clinician_id="dr-kovacs")

    assert revoked is not None
    assert store.resolve_token(raw_token) is None


def test_patient_token_store_can_ensure_stable_demo_token(tmp_path: Path) -> None:
    store = PatientTokenStore(tmp_path / "patient_tokens.json", secret="fixed-secret")

    first = store.ensure_token(
        raw_token="ptk_demo_patient_access",
        clinician_id="demo-clinician",
        label="demo patient login",
        patient_alias_key="anonpt_demo_login",
        expires_in_days=365,
    )
    second = store.ensure_token(
        raw_token="ptk_demo_patient_access",
        clinician_id="demo-clinician",
        label="demo patient login",
        patient_alias_key="anonpt_demo_login",
        expires_in_days=365,
    )

    assert first.token_id == second.token_id
    assert store.resolve_token("ptk_demo_patient_access") is not None