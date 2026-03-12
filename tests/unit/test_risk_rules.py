from assistant_runtime.pipeline.risk_rules import detect_risk_flags
from assistant_runtime.pipeline.risk_rules import requires_handoff


def test_detect_risk_flags_triggers_handoff() -> None:
    flags = detect_risk_flags("Nem akarok elni, veszelyben vagyok.")
    assert "crisis" in flags
    assert requires_handoff(flags) is True
