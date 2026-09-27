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
        "group: infra-deploy-${{ github.event_name == 'push' && 'dev' || "
        "inputs.environment }}"
    ) in workflow


def test_automatic_deploy_waits_for_exact_commit_images() -> None:
    workflow = _workflow_text()

    assert 'IMAGE_TAG="$GITHUB_SHA"' in workflow
    assert "./scripts/wait-for-acr-images.sh" in workflow
    assert "siege-api siege-frontend siege-bot" in workflow
    assert "ACR_WAIT_ATTEMPTS: 80" in workflow


def test_main_image_builds_cannot_be_cancelled_while_infra_waits() -> None:
    workflow = APP_DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "'deploy-main-pipeline'" in workflow
    assert "cancel-in-progress: false" in workflow


def test_automatic_deploy_preserves_committed_sidecar_setting() -> None:
    workflow = _workflow_text()

    assert "if [[ \"${{ github.event_name }}\" == \"workflow_dispatch\" ]]; then" in workflow
    assert 'sidecar_parameters+=(--parameters "useExternalSidecar=${{ inputs.useExternalSidecar }}")' in workflow
    assert '"${sidecar_parameters[@]}"' in workflow
    assert "useExternalSidecar=${{ inputs.useExternalSidecar || false }}" not in workflow
