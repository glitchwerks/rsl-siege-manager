"""Safety invariants for destructive registry maintenance; no live Azure calls."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

SCRIPT = Path(__file__).resolve().parents[1] / "registry-retention.py"
SPEC = importlib.util.spec_from_file_location("registry_retention", SCRIPT)
r = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(r)
AT = datetime(2026, 10, 4, tzinfo=timezone.utc)


def image(number, days=20, tags=None):
    return {"digest": "sha256:" + f"{number:064x}", "lastUpdateTime": (AT - timedelta(days=days)).isoformat(),
            "tags": tags if tags is not None else [f"{number:040x}"],
            "mediaType": "application/vnd.docker.distribution.manifest.v2+json",
            "changeableAttributes": {"deleteEnabled": True, "writeEnabled": True}}


def inventory():
    return {repo: [image(i, days=i + 1) for i in range(1, 9)] for repo in r.REPOSITORIES}


def baseline(rows):
    return {"schema": 1, "registry": r.REGISTRY, "subscription": r.SUBSCRIPTION,
            "days": 7, "keep": 5, "createdAt": AT.isoformat(), "status": "PREFLIGHT_PASS",
            "baseline": {"apps": [], "references": []}, "images": r.plan(rows, [], AT),
            "inventoryHashes": {repo: r.inventory_hash(values) for repo, values in rows.items()},
            "attempted": [], "deleted": []}


class RetentionTests(unittest.TestCase):
    def test_seven_day_boundary_and_newest_five(self):
        rows = {"siege-api": [image(i, days=i) for i in range(1, 10)]}
        result = r.plan(rows, [], AT)
        self.assertEqual([row["digest"] for row in result if row["candidate"]], [image(8)["digest"], image(9)["digest"]])
        self.assertFalse(next(row for row in result if row["digest"] == image(7)["digest"])["candidate"])

    def test_newest_five_protected_even_when_old(self):
        rows = {"siege-api": [image(i, days=10+i) for i in range(1, 9)]}
        self.assertEqual(sum(row["candidate"] for row in r.plan(rows, [], AT)), 3)

    def test_tag_and_digest_references_protected(self):
        rows = {"siege-api": [image(i, days=10+i) for i in range(1, 9)]}
        refs = [f"{r.REGISTRY}.azurecr.io/siege-api:{image(7)['tags'][0]}", f"{r.REGISTRY}.azurecr.io/siege-api@{image(8)['digest']}"]
        self.assertEqual(sum(row["candidate"] for row in r.plan(rows, refs, AT)), 1)

    def test_last_five_release_manifests_protected(self):
        rows = {"siege-api": [image(i, days=10+i) for i in range(1, 8)] + [image(i, tags=[f"v{i}"]) for i in range(10, 16)]}
        protected = [row for row in r.plan(rows, [], AT) if row["tags"][0].startswith("v") and not row["candidate"]]
        self.assertEqual(len(protected), 5)

    def test_untagged_can_be_selected(self):
        rows = {"siege-api": [image(i, days=10+i) for i in range(1, 8)] + [image(9, tags=[])]}
        self.assertTrue(next(row for row in r.plan(rows, [], AT) if row["digest"] == image(9)["digest"])["candidate"])

    def test_locked_manifest_protected(self):
        rows = {"siege-api": [image(i, days=10+i) for i in range(1, 9)]}
        rows["siege-api"][-1]["changeableAttributes"]["writeEnabled"] = False
        self.assertFalse(r.plan(rows, [], AT)[-1]["candidate"])

    def test_nested_manifest_refused(self):
        rows = inventory()
        rows["siege-api"][0]["mediaType"] = "application/vnd.oci.image.index.v1+json"
        with self.assertRaises(r.Stop):
            r.plan(rows, [], AT)

    def test_deletion_budget_refused(self):
        with self.assertRaises(r.Stop):
            r.plan({"siege-api": [image(i) for i in range(1, 110)]}, [], AT)

    def test_report_scope_and_freshness(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(r, "now", return_value=AT):
            path = Path(directory) / "report.json"
            data = baseline(inventory())
            r.write(path, data)
            r.load(path)
            data["createdAt"] = (AT - timedelta(hours=1)).isoformat()
            r.write(path, data)
            with self.assertRaises(r.Stop):
                r.load(path)
            data["createdAt"] = AT.isoformat()
            data["days"] = 1
            r.write(path, data)
            with self.assertRaises(r.Stop):
                r.load(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_confirmation_precedes_azure(self):
        for phase in ("activate", "apply", "rollback-task"):
            with patch("sys.argv", [str(SCRIPT), phase, "--report", "/tmp/unused"]), patch.object(r, "require_account") as account:
                with self.assertRaises(SystemExit):
                    r.main()
                account.assert_not_called()

    def test_report_tampering_refused(self):
        rows = inventory()
        report = baseline(rows)
        report["images"][0]["candidate"] = True
        with patch.object(r, "applications", return_value=report["baseline"]), patch.object(r, "image_rows", return_value=rows), patch.object(r, "health"):
            with self.assertRaisesRegex(r.Stop, "report was modified"):
                r.unchanged(report)

    def test_application_drift_refused(self):
        report = baseline(inventory())
        with patch.object(r, "applications", return_value={"apps": [], "references": ["new-image"]}):
            with self.assertRaisesRegex(r.Stop, "Application drift"):
                r.unchanged(report)

    def test_registry_drift_refused(self):
        rows = inventory()
        report = baseline(rows)
        changed = copy.deepcopy(rows)
        changed["siege-api"].append(image(90))
        with patch.object(r, "applications", return_value=report["baseline"]), patch.object(r, "image_rows", return_value=changed):
            with self.assertRaisesRegex(r.Stop, "Registry drift"):
                r.unchanged(report)

    def test_enabled_legacy_task_blocks_deletion(self):
        with patch.object(r, "rest", return_value={"properties": {"status": "Enabled"}}), patch.object(r, "azure") as az:
            with self.assertRaises(r.Stop):
                r.apply(baseline(inventory()), "/tmp/unused")
            az.assert_not_called()

    def test_unknown_delete_outcome_recorded_before_write(self):
        rows = inventory()
        report = baseline(rows)
        def az(*args):
            if args[:3] == ("acr", "repository", "show-manifests"):
                return rows[args[args.index("--repository")+1]]
            if args[:3] == ("acr", "repository", "show"):
                repo, digest = args[args.index("--image")+1].split("@")
                return next(row for row in rows[repo] if row["digest"] == digest)
            if args[:3] == ("acr", "repository", "delete"):
                self.assertEqual(len(report["attempted"]), 1)
                raise r.Stop("unknown write outcome")
            self.fail(args)
        with patch.object(r, "rest", return_value={"properties": {"status": "Disabled"}}), patch.object(r, "unchanged", return_value=rows), patch.object(r, "applications", return_value=report["baseline"]), patch.object(r, "azure", side_effect=az), patch.object(r, "write"):
            with self.assertRaisesRegex(r.Stop, "unknown write"):
                r.apply(report, "/tmp/unused")
        self.assertEqual(len(report["attempted"]), 1)
        self.assertEqual(report["deleted"], [])

    def test_retained_manifest_deletion_detected(self):
        rows = inventory()
        report = baseline(rows)
        rows["siege-api"].pop(0)
        with patch.object(r, "image_rows", return_value=rows):
            with self.assertRaisesRegex(r.Stop, "Retained images"):
                r.verify(report, "/tmp/unused")

    def test_pagination_follows_all_pages(self):
        with patch.object(r, "rest", side_effect=[{"value": [1], "nextLink": "https://management.azure.com/next"}, {"value": [2]}]):
            self.assertEqual(r.listed("https://management.azure.com/start"), [1, 2])
        with self.assertRaises(r.Stop):
            r.listed("https://untrusted.example/next")

    def test_health_transport_retry_and_no_retry_on_unhealthy(self):
        from types import SimpleNamespace
        with patch.object(r.subprocess, "run", side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0, stdout='{ "status": "healthy" }\n200')]) as call:
            self.assertEqual(r.health()["web"], "PASS")
            self.assertEqual(call.call_count, 2)
        with patch.object(r.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout='{ "status": "unhealthy" }\n200')) as call:
            with self.assertRaises(r.Stop):
                r.health()
            self.assertEqual(call.call_count, 1)

    def test_task_rollback_refuses_concurrent_configuration_change(self):
        report = {"activationAttempted": True, "legacyBefore": "Enabled", "legacyConfigHash": "wrong"}
        with patch.object(r, "rest", return_value={"properties": {"status": "Disabled", "step": "changed"}}), patch.object(r, "azure") as az:
            with self.assertRaises(r.Stop):
                r.rollback_task(report, "/tmp/unused")
            az.assert_not_called()

    def test_activation_only_disables_task(self):
        before = {"properties": {"status": "Enabled", "step": "unchanged"}}
        after = {"properties": {"status": "Disabled", "step": "unchanged"}}
        report = baseline(inventory())
        with patch.object(r, "unchanged"), patch.object(r, "rest", side_effect=[before, after]), patch.object(r, "azure", side_effect=[[], {}]) as az, patch.object(r, "write"):
            r.activate(report, "/tmp/unused")
            self.assertEqual(json.loads(az.call_args.args[-1]), {"properties": {"status": "Disabled"}})
            self.assertEqual(report["status"], "ACTIVATE_PASS")


if __name__ == "__main__":
    unittest.main()
