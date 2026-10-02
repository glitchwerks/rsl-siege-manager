"""Safety contract for the subscription-wide cost anomaly email exception."""

from pathlib import Path

ROOT = Path(__file__).parents[2]


def test_cost_anomaly_only_deploys_with_prod_infra() -> None:
    main = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")

    assert (
        "module costAnomalyAlert 'modules/cost-anomaly-alert.bicep' = "
        "if (environment == 'prod')"
    ) in main
    assert "scope: subscription()" in main[main.index("module costAnomalyAlert") :]


def test_anomaly_uses_native_email_not_action_group() -> None:
    module = (ROOT / "infra" / "modules" / "cost-anomaly-alert.bicep").read_text(
        encoding="utf-8"
    )

    assert "targetScope = 'subscription'" in module
    assert "kind: 'InsightAlert'" in module
    assert "frequency: 'Daily'" in module
    assert "to: [alertEmail]" in module
    assert "notificationEmail: alertEmail" in module
    assert (
        "viewId: '${subscription().id}/providers/Microsoft.CostManagement/"
        "views/ms:DailyAnomalyByResourceGroup'"
    ) in module
    assert "actionGroup" not in module


def test_anomaly_display_name_fits_azure_limit() -> None:
    module = (ROOT / "infra" / "modules" / "cost-anomaly-alert.bicep").read_text(
        encoding="utf-8"
    )

    assert "displayName: 'Siege cost anomaly'" in module
    assert len("Siege cost anomaly") <= 25


def test_schedule_has_explicit_renewal_date() -> None:
    main = (ROOT / "infra" / "main.bicep").read_text(encoding="utf-8")

    assert "param costAnomalyStartDate string = '2026-10-01T00:00:00Z'" in main
    assert "param costAnomalyEndDate string = '2027-09-30T00:00:00Z'" in main
