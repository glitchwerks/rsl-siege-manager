"""Static policy tests for the hybrid infrastructure deployment workflow."""

from pathlib import Path

WORKFLOW_PATH = Path(__file__).parents[2] / ".github" / "workflows" / "infra-deploy.yml"


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
