"""Guard the target boundary across manually dispatched VPS release workflows."""

import re
import unittest
from pathlib import Path


WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"
PHASES = {
    "accept", "backup", "migrate", "pause", "plan", "preflight",
    "promote", "promotion-ready", "proof", "readiness", "ready",
    "recovery-inspect", "recovery-restore", "stage",
}
ENVIRONMENT = "environment: ${{ inputs.target == 'rehearsal' && 'vps-rehearsal' || 'vps-prod' }}"
TAILSCALE_TAG = "tags: ${{ inputs.target == 'rehearsal' && 'tag:github-rehearsal' || 'tag:github-deploy' }}"
GUARD = "inputs.target == 'production' || inputs.target == 'rehearsal'"
HOST_SECRETS = {
    "TAILSCALE_OAUTH_CLIENT_ID", "TAILSCALE_OAUTH_SECRET",
    "VPS_DEPLOY_SSH_KEY", "VPS_SSH_HOST_FINGERPRINT",
    "VPS_SSH_KNOWN_HOSTS", "VPS_TAILSCALE_HOST",
}
READ_SECRETS = {"VPS_RELEASE_PACKAGE_READ_TOKEN", "VPS_RELEASE_REPO_READ_TOKEN"}
VARS = {"VPS_RELEASE_READ_USER", "VPS_SSH_USER"}


class ReleaseTargetContract(unittest.TestCase):
    def test_day_role_stage_uses_protected_target_and_secret_decision(self):
        raw = (WORKFLOWS / "vps-day-role-stage.yml").read_text()
        self.assertIn("group: vps-production-promotion", raw)
        self.assertIn(ENVIRONMENT, raw)
        self.assertIn(TAILSCALE_TAG, raw)
        self.assertIn("default: rehearsal", raw)
        self.assertIn("inputs.target == 'rehearsal' || inputs.target == 'production'", raw)
        self.assertIn("'REHEARSAL_DAY_ROLE_DECISION_JSON' || 'DAY_ROLE_DECISION_JSON'", raw)
        for name in HOST_SECRETS:
            self.assertIn(f"'REHEARSAL_{name}'", raw)
        self.assertIn("STAGE_ID: ${{ inputs.stage_id }}", raw)
        self.assertIn('stage-id "$STAGE_ID"', raw)
        self.assertNotIn("--stage-id '${{ inputs.stage_id }}'", raw)
        self.assertNotIn("secrets.DAY_ROLE_DECISION_JSON", raw)

        proof = (WORKFLOWS / "vps-day-role-proof.yml").read_text()
        self.assertIn("group: vps-production-promotion", proof)
        self.assertIn(ENVIRONMENT, proof)
        self.assertIn(TAILSCALE_TAG, proof)
        self.assertIn("default: rehearsal", proof)
        self.assertIn("inputs.target == 'rehearsal' || inputs.target == 'production'", proof)
        for name in HOST_SECRETS:
            self.assertIn(f"'REHEARSAL_{name}'", proof)
        self.assertIn('stage-id "$STAGE_ID"', proof)
        self.assertNotIn("--stage-id '${{ inputs.stage_id }}'", proof)

    def test_every_phase_requires_explicit_protected_target(self):
        paths = {path.stem.removeprefix("vps-release-"): path
                 for path in WORKFLOWS.glob("vps-release-*.yml")}
        self.assertEqual(paths.keys(), PHASES)

        for phase, path in paths.items():
            with self.subTest(phase=phase):
                raw = path.read_text()
                self.assertIn("      target:\n", raw)
                self.assertIn("        default: production\n", raw)
                self.assertIn("        type: choice\n", raw)
                self.assertIn("          - production\n          - rehearsal\n", raw)
                self.assertIn("group: vps-production-promotion", raw)
                self.assertIn(ENVIRONMENT, raw)
                self.assertIn(TAILSCALE_TAG, raw)
                self.assertNotIn("environment: vps-prod", raw)
                job_count = len(re.findall(r"^  [a-z][a-z_-]*:\s*$", raw.split("jobs:\n", 1)[1], re.M))
                self.assertEqual(raw.count(GUARD), job_count - (phase == "accept"))
                for name in HOST_SECRETS | READ_SECRETS:
                    self.assertNotIn(f"secrets.{name}", raw)
                    if f"'{name}'" in raw:
                        self.assertIn(
                            "${{ secrets[inputs.target == 'rehearsal' && "
                            f"'REHEARSAL_{name}' || '{name}'] }}", raw,
                        )
                for name in VARS:
                    self.assertNotIn(f"vars.{name}", raw)
                    if f"'{name}'" in raw:
                        self.assertIn(
                            "${{ vars[inputs.target == 'rehearsal' && "
                            f"'REHEARSAL_{name}' || '{name}'] }}", raw,
                        )
                for name in HOST_SECRETS:
                    self.assertIn(f"'REHEARSAL_{name}'", raw)
                self.assertIn("'REHEARSAL_VPS_SSH_USER'", raw)
                if phase in {"readiness", "stage"}:
                    for name in READ_SECRETS:
                        self.assertIn(f"'REHEARSAL_{name}'", raw)
                    self.assertIn("'REHEARSAL_VPS_RELEASE_READ_USER'", raw)

    def test_rehearsal_acceptance_runs_only_readonly_isolated_smoke(self):
        raw = (WORKFLOWS / "vps-release-accept.yml").read_text()
        self.assertEqual(raw.count("  isolated_readonly_smoke:\n"), 1)
        self.assertNotIn("\nconcurrency:\n", raw.split("jobs:\n", 1)[0])
        accept = raw.split("  accept:\n", 1)[1].split("  isolated_readonly_smoke:\n", 1)[0]
        self.assertIn("    concurrency:\n      group: vps-production-promotion\n", accept)
        smoke = raw.split("  isolated_readonly_smoke:\n", 1)[1]
        self.assertIn("    needs: accept\n", smoke)
        self.assertIn("inputs.target == 'rehearsal'", smoke)
        self.assertNotIn("inputs.target == 'production'", smoke)
        self.assertIn("    environment: integration-smoke\n", smoke)
        self.assertIn("      group: portable-integration-smoke\n", smoke)
        self.assertIn("tags: tag:integration-smoke", smoke)
        self.assertIn("python3 scripts/portable-integration.py readonly", smoke)
        self.assertIn("--confirm-isolated-target", smoke)
        self.assertNotIn("--confirm-discord-writes", smoke)
        self.assertNotIn("tag:github-deploy", smoke)


if __name__ == "__main__":
    unittest.main()
