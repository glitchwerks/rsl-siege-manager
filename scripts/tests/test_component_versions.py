"""Tests for component-version enforcement."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "check-component-versions.py"
SPEC = importlib.util.spec_from_file_location("component_versions", SCRIPT)
assert SPEC and SPEC.loader
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)


def test_external_surface_without_bump_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "affected_components",
        lambda _base: {"siege-api": ["backend/app/api/version.py"]},
    )
    monkeypatch.setattr(module, "_version_at", lambda _component, revision=None: "1.4.2")

    errors = module.check("base")

    assert len(errors) == 1
    assert "did not advance" in errors[0]


def test_external_surface_with_forward_bump_passes(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "affected_components",
        lambda _base: {"siege-frontend": ["frontend/src/pages/SystemPage.tsx"]},
    )
    monkeypatch.setattr(
        module,
        "_version_at",
        lambda _component, revision=None: "1.4.2" if revision else "1.5.0",
    )

    assert module.check("base") == []


def test_backward_or_prerelease_version_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "affected_components",
        lambda _base: {"siege-bot": ["bot/app/http_api.py"]},
    )
    monkeypatch.setattr(
        module,
        "_version_at",
        lambda _component, revision=None: "1.4.2" if revision else "1.5.0-rc.1",
    )

    assert "not a stable" in module.check("base")[0]


def test_direct_version_regression_fails_without_surface_change(monkeypatch) -> None:
    monkeypatch.setattr(module, "affected_components", lambda _base: {})

    def version_at(component, revision=None):
        if component.name == "siege-api":
            return "1.4.2" if revision else "1.4.1"
        return "1.4.2"

    monkeypatch.setattr(module, "_version_at", version_at)

    errors = module.check("base")

    assert len(errors) == 1
    assert "must not move backward" in errors[0]


def test_bypass_does_not_allow_version_regression(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "affected_components",
        lambda _base: {"siege-api": ["backend/app/api/version.py"]},
    )

    def version_at(component, revision=None):
        if component.name == "siege-api":
            return "1.4.2" if revision else "1.4.1"
        return "1.4.2"

    monkeypatch.setattr(module, "_version_at", version_at)

    errors = module.check(
        "base",
        allow_bypass=True,
        pr_body="## Version bump bypass\nRefactor preserves the public contract.",
    )

    assert len(errors) == 1
    assert "must not move backward" in errors[0]


def test_bypass_requires_auditable_reason(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "affected_components",
        lambda _base: {"siege-api": ["backend/app/api/version.py"]},
    )
    monkeypatch.setattr(module, "_version_at", lambda _component, revision=None: "1.4.2")

    assert module.check("base", allow_bypass=True, pr_body="## Version bump bypass\nN/A")
    assert module.check("base", allow_bypass=True, pr_body="## Version bump bypass\n- [ ]")
    assert (
        module.check(
            "base",
            allow_bypass=True,
            pr_body=(
                "## Version bump bypass\n"
                "Refactor only; response contract is unchanged.\n\n"
                "## Test plan\n- done"
            ),
        )
        == []
    )


def test_vite_contract_change_flags_frontend(monkeypatch) -> None:
    monkeypatch.setattr(module, "_git", lambda *_args: "frontend/vite.config.ts\n")
    monkeypatch.setattr(module, "_vite_names_in_base", lambda _base: {"VITE_API_URL"})
    monkeypatch.setattr(
        module,
        "_vite_names_in_worktree",
        lambda: {"VITE_API_URL", "VITE_NEW_CONTRACT"},
    )

    assert module.affected_components("base") == {
        "siege-frontend": ["VITE_* environment contract"]
    }


def test_rate_limit_change_flags_api(monkeypatch) -> None:
    def fake_git(*args: str) -> str:
        if "--name-only" in args:
            return "backend/app/rate_limit.py\n"
        return ""

    monkeypatch.setattr(module, "_git", fake_git)
    monkeypatch.setattr(module, "_vite_names_in_base", lambda _base: set())
    monkeypatch.setattr(module, "_vite_names_in_worktree", lambda: set())

    assert module.affected_components("base") == {
        "siege-api": ["backend/app/rate_limit.py"]
    }
