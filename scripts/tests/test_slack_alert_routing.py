"""Static safety-contract tests for Azure Monitor Slack alert routing."""

from pathlib import Path

ROOT = Path(__file__).parents[2]
MONITORING = ROOT / "infra" / "modules" / "monitoring.bicep"
KEY_VAULT = ROOT / "infra" / "modules" / "keyvault.bicep"


def _monitoring_text() -> str:
    return MONITORING.read_text(encoding="utf-8")


def test_action_group_keeps_email_and_adds_common_schema_logic_app() -> None:
    template = _monitoring_text()

    assert "emailReceivers: [" in template
    assert "logicAppReceivers: [" in template
    assert "name: 'Slack Infrastructure Alerts'" in template
    assert template.count("useCommonAlertSchema: true") >= 2


def test_workflow_accepts_only_required_common_alert_shape() -> None:
    template = _monitoring_text()

    for field in (
        "schemaId",
        "alertRule",
        "severity",
        "monitorCondition",
        "alertTargetIDs",
        "firedDateTime",
    ):
        assert field in template
    assert "azureMonitorCommonAlertSchema" in template
    assert "'Fired'" in template
    assert "'Resolved'" in template
    assert "statusCode: 400" in template


def test_slack_body_is_sanitized_and_distinguishes_state() -> None:
    template = _monitoring_text()

    assert "'✅ RESOLVED'" in template
    assert "'🚨 FIRED'" in template
    assert "parameters('alertEnvironment')" in template
    assert "fallbackInvestigationUrl" in template
    assert "alertContext" not in template
    assert "customProperties" not in template


def test_webhook_is_read_from_key_vault_and_secure_in_run_history() -> None:
    monitoring = _monitoring_text()
    key_vault = KEY_VAULT.read_text(encoding="utf-8")

    assert "name: 'slack-alert-webhook-url'" in key_vault
    assert "@secure()\nparam slackAlertWebhookUrl string" in key_vault
    assert "type: 'ManagedServiceIdentity'" in monitoring
    assert "kvSecretsUserRoleId" in monitoring
    assert monitoring.count("'inputs'\n                    'outputs'") == 2


def test_callback_and_webhook_are_not_module_outputs() -> None:
    template = _monitoring_text()
    outputs = template[template.index("// ── Outputs") :]

    assert "callback" not in outputs.lower()
    assert "webhook" not in outputs.lower()

