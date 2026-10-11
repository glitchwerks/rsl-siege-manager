"""Backup job, process ownership, monitor and retention safety checks; no live services."""
from contextlib import redirect_stdout
from datetime import timedelta
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch
import uuid

SPEC = importlib.util.spec_from_file_location("job", Path(__file__).parents[1] / "portable-backup-job.py")
job = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(job)


def private(path, data):
    path.write_text(data)
    path.chmod(0o600)
    return path


def configuration(root):
    state = root / "state"
    state.mkdir(mode=0o700)
    (state / "runs").mkdir(mode=0o700)
    values = {"root": str(state), "prefix": "bucket/rsl", "timeout_seconds": 1800, "keep_days": 7, "keep_successful": 24}
    for key in ("services", "passwords", "recipients", "remote_config", "heartbeat_file"):
        path = private(root / key, "protected fixture")
        values[key] = str(path)
    private(root / "remote_config", "[backup]\ntype=s3\naccess_key_id=synthetic\nsecret_access_key=synthetic\n")
    private(root / "heartbeat_file", f"https://monitor.invalid/{uuid.uuid4()}\n")
    path = private(root / "job.json", json.dumps(values))
    return path, job.settings(path)


def runs(config, now):
    result = []
    for index in range(30):
        directory = config["root"] / "runs" / str(uuid.uuid4())
        directory.mkdir(mode=0o700)
        for folder, names in job.DATA.items():
            parent = directory / folder
            parent.mkdir(mode=0o700)
            for name in names:
                private(parent / name, "protected archive fixture")
        captured = now - timedelta(days=20, hours=30-index) if index < 26 else now - timedelta(minutes=30-index)
        copy_id = str(uuid.uuid4())
        value = {"result": "PASS", "phase": "run", "copy_id": copy_id, "bundle_id": str(uuid.uuid4()),
                 "timestamp": captured.isoformat(), "finished_at": (captured + timedelta(seconds=1)).isoformat(),
                 "checks": {check: True for check in ("database_backup", "encrypted_bundle", "encrypted_remote_readback",
                            "remote_receipt_readback", "independent_monitor_acknowledged")}}
        job.copies.recovery.write_json(directory / "job.json", value)
        result.append((directory, value))
    return result


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path, self.config = configuration(self.root)

    def invoke(self, phase, *extra):
        args = [phase, "--config", str(self.config_path)]
        if phase != "run":
            args += ["--report", str(self.root / f"report-{uuid.uuid4()}.json")]
        output = io.StringIO()
        with redirect_stdout(output):
            code = job.main([*args, *map(str, extra)])
        return code, json.loads(output.getvalue())

    def test_run_and_cleanup_require_separate_confirmations(self):
        with patch.object(job, "run_job") as run, patch.object(job, "retain") as retain:
            code, report = self.invoke("run")
            self.assertEqual(report["error"], "backup_job_confirmation_required")
            self.assertEqual(code, 1)
            code, report = self.invoke("apply")
            self.assertEqual(report["error"], "retention_confirmation_required")
            self.assertEqual(code, 1)
        run.assert_not_called()
        retain.assert_not_called()

    def test_overlap_refused_without_starting_another_backup(self):
        with job.locked(self.config["root"]), patch.object(job, "run_job") as run:
            code, report = self.invoke("run", "--confirm-run")
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "backup_job_already_running")
        run.assert_not_called()

    def test_success_signal_only_after_backup_copy_and_catalog(self):
        copy_id, bundle_id = str(uuid.uuid4()), str(uuid.uuid4())
        phases = []
        def child(script, args, report, deadline, phase):
            phases.append(phase)
            self.assertNotIn("restore", args)
            return {"result": "PASS", "phase": phase, "copy_id": copy_id, "bundle_id": bundle_id}
        def ping(path, success):
            self.assertTrue(success)
            self.assertEqual(phases, ["backup", "seal", "publish", "catalog"])
        with patch.object(job, "child", side_effect=child), patch.object(job, "ping", side_effect=ping):
            code, report = self.invoke("run", "--confirm-run")
        self.assertEqual(code, 0)
        saved = json.loads((self.config["root"] / "runs" / report["run_id"] / "job.json").read_text())
        self.assertEqual(saved, report)
        self.assertTrue(all(report["checks"].values()))

    def test_failed_phase_preserves_evidence_and_sends_only_failure(self):
        with patch.object(job, "child", side_effect=job.Stop("backup_phase_failed")), patch.object(job, "ping") as ping:
            code, report = self.invoke("run", "--confirm-run")
        self.assertEqual(code, 1)
        ping.assert_called_once_with(self.config["heartbeat_file"], False)
        self.assertEqual(report["result"], "STOP")
        self.assertTrue((self.config["root"] / "runs" / report["run_id"] / "job.json").exists())

    def test_missing_monitor_ack_is_not_success(self):
        result = {"copy_id": str(uuid.uuid4()), "bundle_id": str(uuid.uuid4())}
        with patch.object(job, "child", return_value=result), patch.object(job, "ping", side_effect=job.Stop("heartbeat_failed")):
            code, report = self.invoke("run", "--confirm-run")
        self.assertEqual(code, 1)
        self.assertFalse(report["checks"]["independent_failure_acknowledged"])
        self.assertNotIn("independent_monitor_acknowledged", report["checks"])

    def test_timeout_kills_only_owned_phase_process_group(self):
        process = Mock(pid=123456)
        process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 1), 0, 0]
        process.poll.return_value = None
        with patch.object(job.subprocess, "Popen", return_value=process) as start, patch.object(job.os, "killpg") as kill:
            with self.assertRaisesRegex(job.Stop, "backup_deadline_exceeded"):
                job.child("portable-recovery.py", [], self.root / "report", job.time.monotonic() + 60, "backup")
        self.assertTrue(start.call_args.kwargs["start_new_session"])
        self.assertEqual([call.args[0] for call in kill.call_args_list], [123456, 123456])

    def test_failed_phase_parent_still_cleans_its_owned_descendants(self):
        process = Mock(pid=123456)
        process.wait.return_value = 1
        process.poll.return_value = 1
        with patch.object(job.subprocess, "Popen", return_value=process), patch.object(job.os, "killpg") as kill:
            with self.assertRaisesRegex(job.Stop, "backup_phase_failed"):
                job.child("portable-recovery.py", [], self.root / "report", job.time.monotonic() + 60, "backup")
        self.assertEqual([call.args[0] for call in kill.call_args_list], [123456, 123456])

    def test_expired_retention_plan_preserves_all_data(self):
        data, args, plan_path = self.prepare_retention()
        self.assertEqual(self.invoke("plan", *args)[0], 0)
        plan = json.loads(plan_path.read_text())
        plan["created_at"] = (job.timestamp() - timedelta(hours=2)).isoformat()
        private(plan_path, json.dumps(plan))
        code, report = self.invoke("apply", *args, "--confirm-retention")
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "retention_plan_expired")
        self.assertTrue(all((directory / "source/mom.dump").exists() for directory, _ in data))

    def test_monitor_tls_redirect_and_false_ack_refused(self):
        for url in ("http://monitor.invalid/id", "https://user:secret@monitor.invalid/id", "https://monitor.invalid/id?create=1"):
            private(self.config["heartbeat_file"], url)
            with self.assertRaises(job.Stop):
                job.heartbeat_url(self.config["heartbeat_file"])
        with self.assertRaisesRegex(job.Stop, "heartbeat_redirect_refused"):
            job.NoRedirect().redirect_request(None)
        private(self.config["heartbeat_file"], "https://monitor.invalid/id")
        response = Mock(status=200)
        response.read.return_value = b"OK (not found)"
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch.object(job.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(job.Stop, "heartbeat_not_acknowledged"):
                job.ping(self.config["heartbeat_file"], True)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.data, b"")

    def prepare_retention(self):
        data = runs(self.config, job.timestamp())
        anchor = data[0][1]
        recover = private(self.root / "recover.json", json.dumps({"phase": "recover", "result": "PASS", "copy_id": anchor["copy_id"],
            "bundle_id": anchor["bundle_id"], "checks": {"recovered_bundle_verified": True}}))
        restore = private(self.root / "restore.json", json.dumps({"phase": "restore", "result": "PASS", "bundle_id": anchor["bundle_id"],
            "checks": {"siege_restored_controls_match": True, "mom_restored_controls_match": True}}))
        plan = self.root / "plan.json"
        args = ["--recover-report", recover, "--restore-report", restore, "--plan", plan]
        return data, args, plan

    def test_retention_preserves_anchor_recent_copies_failed_runs_and_reports(self):
        data, args, plan_path = self.prepare_retention()
        failed = self.config["root"] / "runs" / str(uuid.uuid4())
        failed.mkdir(mode=0o700)
        private(failed / "job.json", '{"result":"STOP"}')
        private(failed / "private-partial", "preserve")
        code, report = self.invoke("plan", *args)
        self.assertEqual(code, 0)
        plan = json.loads(plan_path.read_text())
        self.assertEqual(len(plan["entries"]), 5)
        self.assertNotIn(data[0][0].name, plan["entries"])
        code, report = self.invoke("apply", *args, "--confirm-retention")
        self.assertEqual(code, 0, report)
        for directory, _ in data:
            self.assertTrue((directory / "job.json").exists())
            self.assertEqual((directory / "source").exists(), directory.name not in plan["entries"])
        self.assertTrue((failed / "private-partial").exists())
        self.assertTrue((data[0][0] / "source").exists())

    def test_changed_file_or_edited_plan_prevents_any_deletion(self):
        data, args, plan_path = self.prepare_retention()
        self.assertEqual(self.invoke("plan", *args)[0], 0)
        plan = json.loads(plan_path.read_text())
        first = next(iter(plan["entries"]))
        private(self.config["root"] / "runs" / first / "source/siege.dump", "changed")
        code, report = self.invoke("apply", *args, "--confirm-retention")
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "retention_plan_changed")
        self.assertTrue(all((directory / "source/mom.dump").exists() for directory, _ in data))
        plan["entries"]["../../outside"] = {}
        private(plan_path, json.dumps(plan))
        self.assertEqual(self.invoke("apply", *args, "--confirm-retention")[0], 1)

    def test_unknown_artifacts_and_missing_restore_proof_stop_retention(self):
        data, args, plan_path = self.prepare_retention()
        private(data[1][0] / "source/credentials", "do not delete")
        code, report = self.invoke("plan", *args)
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "unknown_retention_artifact")
        self.assertFalse(plan_path.exists())
        private(self.root / "restore.json", '{"phase":"restore","result":"STOP"}')
        self.assertEqual(self.invoke("plan", *args)[1]["error"], "verified_restore_proof_required")

    def test_stale_backups_stop_retention(self):
        data = runs(self.config, job.timestamp() - timedelta(hours=3))
        with self.assertRaisesRegex(job.Stop, "fresh_verified_backup_required"):
            job.retention_plan(self.config, data[0][1]["copy_id"], job.timestamp())

    def test_policy_and_secret_configuration_errors_are_sanitized(self):
        private(self.config_path, '{"secret": "private token"}')
        code, report = self.invoke("run", "--confirm-run")
        self.assertEqual(code, 1)
        self.assertNotIn("private token", json.dumps(report))
        self.assertEqual(report["error"], "invalid_job_config")


if __name__ == "__main__":
    unittest.main()
