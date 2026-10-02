"""Guard the per-alert decisions from the #263 production-history review."""

from pathlib import Path


MONITORING = (Path(__file__).parents[2] / "infra/modules/monitoring.bicep").read_text(
    encoding="utf-8"
)


def _resource(name: str) -> str:
    start = MONITORING.index(f"resource {name} ")
    end = MONITORING.find("\nresource ", start + 1)
    return MONITORING[start:end] if end >= 0 else MONITORING[start:]


def test_transient_latency_alert_auto_resolves_without_action_mute() -> None:
    latency = _resource("alertLatencyP95")

    assert "autoMitigate: true" in latency
    assert "muteActionsDuration" not in latency


def test_other_application_rules_keep_stateless_policy() -> None:
    for name in (
        "alert5xxRate",
        "alertBotRestart",
        "alertDbConnectionError",
        "alertImageGenSlow",
    ):
        rule = _resource(name)
        assert "autoMitigate: false" in rule
        assert "muteActionsDuration: 'PT15M'" in rule
