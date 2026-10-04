"""Offline tests for mutation gating, drift checks, and uncertain-write recovery."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("trim", Path(__file__).with_name("azure-cost-trim.py"))
trim = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trim)


class CostTrimTests(unittest.TestCase):
    def setUp(self):
        self.target = trim.targets("subscription")[1]
        self.resource = {"location": "westus", "tags": {}, "properties": {
            "evaluationFrequency": "PT1M", "windowSize": "PT5M", "enabled": True,
            "criteria": {"allOf": [{"query": "requests | where timestamp > ago(5m)"}]},
            "actions": {"actionGroups": ["existing-group"]}, "autoMitigate": True}}
        self.target.update(before="PT1M", invariant=trim.invariant(self.target, self.resource))
        self.report = {"created_at": trim.datetime.now(trim.timezone.utc).isoformat(),
                       "status": "PREFLIGHT_PASS", "subscription": "subscription",
                       "baseline": {}, "registry": {"tag_hashes": {}},
                       "targets": [self.target], "attempted": []}

    def test_allowlist_is_production_registry_and_two_advisory_alerts(self):
        targets = trim.targets("subscription")
        self.assertEqual(len(targets), 3)
        self.assertTrue(all("/siege-web-prod/" in t["id"] for t in targets))
        self.assertEqual(sum(t["kind"] == "registry" for t in targets), 1)
        self.assertFalse(any("restart" in t["id"] or "connection" in t["id"] for t in targets))

    def test_frequency_change_keeps_other_fields_invariant(self):
        before = trim.invariant(self.target, self.resource)
        self.resource["properties"]["evaluationFrequency"] = "PT5M"
        self.assertEqual(before, trim.invariant(self.target, self.resource))
        self.resource["properties"]["actions"] = {}
        self.assertNotEqual(before, trim.invariant(self.target, self.resource))

    def test_registry_progress_does_not_hide_configuration_drift(self):
        target = trim.targets("subscription")[0]
        resource = {"id": target["id"], "location": "westus", "tags": {},
                    "properties": {"provisioningState": "Succeeded", "adminUserEnabled": True}}
        before = trim.invariant(target, resource)
        resource["properties"]["provisioningState"] = "Updating"
        self.assertEqual(before, trim.invariant(target, resource))
        resource["properties"]["adminUserEnabled"] = False
        self.assertNotEqual(before, trim.invariant(target, resource))

    def test_patch_only_sends_authorized_field(self):
        with patch.object(trim, "azure") as request:
            trim.patch(self.target, "PT5M")
            self.assertEqual(trim.json.loads(request.call_args.args[-1]),
                             {"properties": {"evaluationFrequency": "PT5M"}})

    def test_apply_rejects_service_drift_before_mutation(self):
        with patch.object(trim, "service_baseline", return_value={"changed": True}), \
                patch.object(trim, "patch") as mutate:
            with self.assertRaises(trim.CheckFailed):
                trim.apply(self.report, lambda: None)
            mutate.assert_not_called()

    def test_apply_rejects_stale_report(self):
        self.report["created_at"] = "2020-01-01T00:00:00+00:00"
        with patch.object(trim, "patch") as mutate:
            with self.assertRaises(trim.CheckFailed):
                trim.apply(self.report, lambda: None)
            mutate.assert_not_called()

    def test_unknown_write_is_recorded_before_request_and_recovered(self):
        def uncertain_write(target, desired):
            self.assertEqual(self.report["attempted"], [0])
            self.resource["properties"]["evaluationFrequency"] = desired
            if desired == "PT5M":
                raise trim.CheckFailed("Uncertain request outcome")

        with patch.object(trim, "service_baseline", return_value={}), \
                patch.object(trim, "registry_baseline", return_value={"tag_hashes": {}}), \
                patch.object(trim, "read", side_effect=lambda _: self.resource), \
                patch.object(trim, "patch", side_effect=uncertain_write):
            with self.assertRaises(trim.CheckFailed):
                trim.apply(self.report, lambda: None)
        self.assertEqual(self.report["status"], "ROLLBACK_PASS")
        self.assertEqual(self.resource["properties"]["evaluationFrequency"], "PT1M")

    def test_rollback_refuses_concurrent_configuration_change(self):
        self.report["attempted"] = [0]
        self.resource["properties"]["actions"] = {"actionGroups": ["new-group"]}
        with patch.object(trim, "read", return_value=self.resource), patch.object(trim, "patch") as mutate:
            with self.assertRaises(trim.CheckFailed):
                trim.rollback(self.report, lambda: None)
            mutate.assert_not_called()
        self.assertEqual(self.report["status"], "ROLLBACK_FAILED")

    def test_ip_restricted_bot_does_not_trigger_public_probe(self):
        bot = {"id": "bot-id", "name": "ca-mom-bot", "properties": {
            "provisioningState": "Succeeded", "runningStatus": "Running",
            "latestReadyRevisionName": "revision", "template": {"containers": []},
            "configuration": {"ingress": {"external": True, "fqdn": "private-bot.example",
                                             "ipSecurityRestrictions": [{"action": "Allow"}]}}}}

        def request(*args):
            endpoint = args[-1]
            return {"value": [bot] if "/mom-bot/" in endpoint and "/containerApps?" in endpoint else []}

        with patch.object(trim, "azure", side_effect=request), patch.object(trim.subprocess, "run") as probe:
            result = trim.service_baseline("subscription")
            probe.assert_not_called()
        self.assertEqual(result["health"][0]["status"], "CONTROL_PLANE_ONLY_IP_RESTRICTED")

    def test_apply_requires_confirmation_before_azure_request(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            args = ["trim", "apply", "--subscription", "subscription",
                    "--report", str(Path(directory) / "report.json")]
            with patch.object(trim.sys, "argv", args), patch.object(trim, "azure") as request, \
                    patch("builtins.print"):
                self.assertEqual(trim.main(), 1)
                request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
