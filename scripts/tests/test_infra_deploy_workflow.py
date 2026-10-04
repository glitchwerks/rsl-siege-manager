"""Static policy tests for the hybrid infrastructure deployment workflow."""

from pathlib import Path

WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "infra-deploy.yml"
APP_DEPLOY_WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "deploy.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_infra_changes_on_main_trigger_the_workflow() -> None:
    workflow = _workflow_text()

    assert """on:
  push:
    branches:
      - main
    paths:
      - 'infra/**'
  workflow_dispatch:""" in workflow


def test_push_and_manual_dev_runs_can_deploy_dev() -> None:
    workflow = _workflow_text()

    assert (
        "if: github.event_name == 'push' || (github.event_name == 'workflow_dispatch' "
        "&& inputs.environment == 'dev')"
    ) in workflow


def test_only_manual_prod_selection_can_deploy_prod() -> None:
    workflow = _workflow_text()

    assert (
        "if: github.event_name == 'workflow_dispatch' && inputs.environment == 'prod'"
    ) in workflow


def test_push_runs_share_the_dev_concurrency_group() -> None:
    workflow = _workflow_text()

    assert (
        "format('infra-deploy-{0}', github.event_name == 'push' && 'dev' || inputs.environment)"
    ) in workflow


def test_automatic_deploy_waits_for_exact_commit_images() -> None:
    workflow = _workflow_text()

    assert 'IMAGE_TAG="$GITHUB_SHA"' in workflow
    assert "./scripts/wait-for-acr-images.sh" in workflow
    assert "siege-api siege-frontend siege-bot" in workflow
    assert "ACR_WAIT_ATTEMPTS: 80" in workflow


def test_every_main_commit_gets_a_non_replacing_image_build_slot() -> None:
    workflow = APP_DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "format('deploy-main-build-{0}', github.sha)" in workflow
    assert "cancel-in-progress: false" in workflow


def test_only_latest_main_revision_enters_normal_dev_deploy() -> None:
    workflow = APP_DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert 'latest_sha=$(gh api "repos/$GITHUB_REPOSITORY/commits/main" --jq .sha)' in workflow
    assert "if: needs.select-latest-dev.outputs.deploy == 'true'" in workflow
    assert "group: deploy-api-dev" in workflow
    assert "group: deploy-frontend-dev" in workflow
    assert "group: deploy-bot-dev" in workflow


def test_infra_deploy_reconciles_if_main_advanced() -> None:
    workflow = _workflow_text()

    assert "Reconcile dev apps to latest main images" in workflow
    assert 'if [[ "$latest_sha" == "$deployed_sha" ]]' in workflow
    assert "for attempt in {1..5}" in workflow


def test_automatic_deploy_preserves_committed_sidecar_setting() -> None:
    workflow = _workflow_text()

    assert "if [[ \"${{ github.event_name }}\" == \"workflow_dispatch\" ]]; then" in workflow
    assert 'sidecar_parameters+=(--parameters "useExternalSidecar=${{ inputs.useExternalSidecar }}")' in workflow
    assert '"${sidecar_parameters[@]}"' in workflow
    assert "useExternalSidecar=${{ inputs.useExternalSidecar || false }}" not in workflow


def test_deploys_pass_bot_service_token_from_environment_secret() -> None:
    workflow = _workflow_text()

    parameter = '--parameters botServiceToken="${{ secrets.BOT_SERVICE_TOKEN }}"'
    assert workflow.count(parameter) == 2


def test_idempotency_check_uses_bot_service_token() -> None:
    workflow_path = Path(__file__).parents[2] / ".github" / "workflows" / "idempotency-check.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert '--parameters botServiceToken="${{ secrets.BOT_SERVICE_TOKEN }}"' in workflow


def test_deploys_use_shared_organization_slack_secret() -> None:
    workflow = _workflow_text()

    assert workflow.count("SLACK_ALERT_BOT_WEBHOOK: ${{ secrets.SLACK_ALERT_BOT_WEBHOOK }}") == 4
    assert workflow.count('if [[ -z "$SLACK_ALERT_BOT_WEBHOOK" ]]; then') == 2
    parameter = '--parameters slackAlertWebhookUrl="$SLACK_ALERT_BOT_WEBHOOK"'
    assert workflow.count(parameter) == 2
    assert "secrets.SLACK_ALERT_WEBHOOK_URL" not in workflow


def test_idempotency_check_uses_slack_webhook_secret() -> None:
    workflow_path = Path(__file__).parents[2] / ".github" / "workflows" / "idempotency-check.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    assert workflow.count("SLACK_ALERT_BOT_WEBHOOK: ${{ secrets.SLACK_ALERT_BOT_WEBHOOK }}") == 2
    assert 'if [[ -z "$SLACK_ALERT_BOT_WEBHOOK" ]]; then' in workflow
    assert '--parameters slackAlertWebhookUrl="$SLACK_ALERT_BOT_WEBHOOK"' in workflow
    assert "secrets.SLACK_ALERT_WEBHOOK_URL" not in workflow


def test_infra_ci_uses_only_a_placeholder_slack_webhook() -> None:
    workflow_path = Path(__file__).parents[2] / ".github" / "workflows" / "infra-ci.yml"
    workflow = workflow_path.read_text(encoding="utf-8")

    placeholder = (
        '--parameters slackAlertWebhookUrl="https://hooks.slack.com/services/ci/placeholder/value"'
    )
    assert workflow.count(placeholder) == 2
    assert "secrets.SLACK_ALERT_WEBHOOK_URL" not in workflow
    assert "secrets.SLACK_ALERT_BOT_WEBHOOK" not in workflow


def test_production_deployments_and_retention_share_a_non_replacing_queue() -> None:
    retention = (WORKFLOW_PATH.parent / "registry-retention.yml").read_text()
    app = APP_DEPLOY_WORKFLOW_PATH.read_text()
    infra = _workflow_text()
    for workflow in (retention, app, infra):
        assert "production-registry-maintenance" in workflow
        assert "queue: max" in workflow
        assert "cancel-in-progress: false" in workflow
    assert "inputs.environment == 'prod'" in app
    assert "inputs.environment == 'prod'" in infra


def test_retention_activation_and_deletion_are_separately_gated() -> None:
    workflow = (WORKFLOW_PATH.parent / "registry-retention.yml").read_text()
    assert "default: preflight" in workflow
    assert "inputs.phase == 'activate'" in workflow
    assert "github.event_name == 'schedule' || inputs.phase == 'apply'" in workflow
    assert 'activate --report "$REPORT" --confirm' in workflow
    assert 'apply --report "$REPORT" --confirm' in workflow
    assert "environment: prod" in workflow
    assert "if: always()" in workflow
