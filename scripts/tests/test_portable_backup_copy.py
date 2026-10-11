"""Protected copy phase tests; real age/SFTP rehearsal only on disposable CI."""
from contextlib import redirect_stdout, nullcontext
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

SPEC = importlib.util.spec_from_file_location("copy_runner", Path(__file__).parents[1] / "portable-backup-copy.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


def private(path, data):
    path.write_bytes(data if isinstance(data, bytes) else data.encode())
    path.chmod(0o600)
    return path


def bundle(root):
    directory = root / "source"
    directory.mkdir(mode=0o700)
    manifest = {"schema": 1, "postgres_major": 16, "bundle_id": str(uuid.uuid4()),
                "created_at": "2026-10-11T00:00:00+00:00", "databases": {}}
    for app in runner.recovery.APPS:
        path = private(directory / f"{app}.dump", b"PGDMPprivate-workload")
        manifest["databases"][app] = {"sha256": runner.recovery.digest(path), "bytes": path.stat().st_size,
                                      "controls": []}
    runner.recovery.write_json(directory / "manifest.json", manifest)
    return directory


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def invoke(self, phase, *args):
        report = self.root / f"{uuid.uuid4()}.json"
        with redirect_stdout(io.StringIO()):
            code = runner.main([phase, "--work", str(self.root / f"work-{uuid.uuid4()}"),
                                "--report", str(report), *map(str, args)])
        return code, json.loads(report.read_text())

    def test_publish_confirmation_before_remote_access(self):
        with patch.object(runner, "transfer") as transfer:
            code, report = self.invoke("publish")
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "publish_confirmation_required")
        transfer.assert_not_called()

    def test_existing_evidence_and_workspace_refused(self):
        report = private(self.root / "report", "preserve")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(["seal", "--work", str(self.root / "work"), "--report", str(report)]), 1)
        self.assertEqual(report.read_text(), "preserve")
        work = self.root / "work"
        work.mkdir(mode=0o700)
        with redirect_stdout(io.StringIO()):
            self.assertEqual(runner.main(["seal", "--work", str(work), "--report", str(self.root / "fresh")]), 1)

    def test_unsafe_permissions_and_symlink_ancestors(self):
        path = private(self.root / "key", "secret")
        path.chmod(0o644)
        with self.assertRaises(runner.Stop):
            runner.read(path)
        path.chmod(0o600)
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(runner.Stop):
            runner.read(alias / "key")

    def test_only_explicit_remote_backends_credentials_and_tls(self):
        config = self.root / "config"
        for content in ("[backup]\ntype=local\n", "[backup]\ntype=s3\n", "[backup]\ntype=s3\nenv_auth=true\n",
                        "[backup]\ntype=s3\naccess_key_id=test\nsecret_access_key=test\nendpoint=http://host\n",
                        "[backup]\ntype=sftp\nhost=host\nuser=user\npass=test\n"):
            private(config, content)
            with self.assertRaises(runner.Stop):
                runner.remote(config, "backups", str(uuid.uuid4()))
        private(config, "[backup]\ntype=s3\naccess_key_id=test\nsecret_access_key=test\n")
        self.assertTrue(runner.remote(config, "bucket/backups", str(uuid.uuid4())).startswith("backup:bucket/backups/"))
        for prefix in ("../backups", "bucket/../x", "x:y", "/absolute", "--flag"):
            with self.assertRaises(runner.Stop):
                runner.remote(config, prefix, str(uuid.uuid4()))

    def test_malformed_config_diagnostic_is_sanitized(self):
        config = private(self.root / "config", "[backup]\ntype=s3\ncredential_without_equals_secret\n")
        sealed = self.root / "sealed"
        sealed.mkdir(mode=0o700)
        cipher = private(sealed / "bundle.tar.age", b"age-encryption.org/v1\nsynthetic")
        runner.recovery.write_json(sealed / "receipt.json", {
            "schema": 1, "copy_id": str(uuid.uuid4()), "bundle_id": str(uuid.uuid4()),
            "created_at": "2026-10-11T00:00:00+00:00", "sha256": runner.hash_file(cipher), "bytes": cipher.stat().st_size})
        code, report = self.invoke("publish", "--confirm-publish", "--package", sealed, "--config", config, "--prefix", "bucket")
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "operation_failed")
        self.assertNotIn("credential", json.dumps(report))

    def test_native_age_keys_only(self):
        path = self.root / "key"
        for content in ("", "ssh-ed25519 AAA", "age-plugin-test", "AGE-SECRET-KEY-1TEST"):
            private(path, content)
            with self.assertRaises(runner.Stop):
                runner.key_file(path)

    def test_cipher_corruption_blocks_publish(self):
        package = self.root / "package"
        package.mkdir(mode=0o700)
        private(package / "bundle.tar.age", "corrupt")
        runner.recovery.write_json(package / "receipt.json", {
            "schema": 1, "copy_id": str(uuid.uuid4()), "bundle_id": str(uuid.uuid4()),
            "created_at": "2026-10-11T00:00:00+00:00", "sha256": "0" * 64, "bytes": 7})
        with patch.object(runner, "transfer") as transfer:
            code, report = self.invoke("publish", "--confirm-publish", "--package", package,
                                       "--config", self.root / "config", "--prefix", "backups")
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "ciphertext_integrity_failed")
        transfer.assert_not_called()

    def test_archive_paths_links_duplicates_and_missing_members_refused(self):
        for names in (("../secret",), ("siege.dump", "siege.dump"), ("manifest.json",), ("mom.dump",)):
            archive = self.root / f"{uuid.uuid4()}.tar"
            with tarfile.open(archive, "w") as tar:
                for name in names:
                    member = tarfile.TarInfo(name)
                    if name == "mom.dump":
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/etc/passwd"
                    tar.addfile(member)
            with self.assertRaises(runner.Stop):
                runner.unpack(archive, self.root / str(uuid.uuid4()), 1024 * 1024)

    def test_secret_output_hidden_and_environment_removed(self):
        with patch.dict(os.environ, {"RCLONE_CONFIG_BACKUP_TYPE": "local", "AWS_SECRET_ACCESS_KEY": "secret",
                                     "SSH_AUTH_SOCK": "agent", "PGPASSWORD": "secret"}), \
                patch.object(runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", b"private-secret")) as run:
            with self.assertRaisesRegex(runner.Stop, "^tool_failed$"):
                runner.command(["rclone", "version"])
        env = run.call_args.kwargs["env"]
        self.assertFalse(any(k.startswith(("RCLONE", "AWS", "SSH_AUTH_SOCK", "PG")) for k in env))

    def test_source_edit_during_packaging_stops_before_encryption(self):
        source = bundle(self.root)
        recipients = private(self.root / "recipients", "age1synthetic")
        original = runner.recovery.verified_manifest
        def changed(directory, checks):
            manifest = original(directory, checks)
            private(directory / "siege.dump", "PGDMPchanged")
            return manifest
        with patch.object(runner.recovery, "archive_readable"), \
                patch.object(runner.recovery, "verified_manifest", side_effect=changed), \
                patch.object(runner, "command") as command:
            code, report = self.invoke("seal", "--bundle", source, "--recipients", recipients)
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "source_changed_during_seal")
        command.assert_not_called()

    def test_download_is_exclusive_private_and_bounded(self):
        destination = self.root / "download"
        with patch.object(runner, "command") as command:
            runner.download(self.root / "config", "backup:prefix/id.tar.age", destination, 100)
        args = command.call_args.args[0]
        self.assertEqual(args[:2], ["rclone", "cat"])
        self.assertEqual(args[args.index("--count") + 1], "101")
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(FileExistsError):
            runner.download(self.root / "config", "backup:prefix/id.tar.age", destination, 100)

    def test_seal_rejects_extra_files_before_encrypting(self):
        source = bundle(self.root)
        private(source / "credentials", "secret")
        with patch.object(runner, "command") as command:
            code, report = self.invoke("seal", "--bundle", source, "--recipients", self.root / "key")
        self.assertEqual(code, 1)
        self.assertEqual(report["error"], "unexpected_bundle_files")
        command.assert_not_called()


@unittest.skipUnless(os.environ.get("PORTABLE_COPY_TEST") == "1", "disposable CI only")
class RealCopyTests(unittest.TestCase):
    def test_real_encryption_sftp_readback_key_recovery_and_tampering(self):
        # No applications, Discord or live databases; start only a disposable CI server.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = root / "store"
            store.mkdir(mode=0o700)
            identity = root / "identity"
            subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
            identity.chmod(0o600)
            public = subprocess.run(["age-keygen", "-y", str(identity)], check=True, capture_output=True).stdout
            recipients = private(root / "recipients", public)
            hostkey = root / "hostkey"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(hostkey)], check=True)
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            server = subprocess.Popen(["rclone", "serve", "sftp", str(store), "--addr", f"127.0.0.1:{port}",
                                       "--user", "ci", "--pass", "synthetic", "--key", str(hostkey)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    self.fail("disposable SFTP startup failed")
                key = hostkey.with_suffix(".pub").read_text().split()
                known = private(root / "known_hosts", f"[127.0.0.1]:{port} {key[0]} {key[1]}\n")
                password = subprocess.run(["rclone", "obscure", "synthetic"], check=True, capture_output=True).stdout.decode().strip()
                config = private(root / "rclone.conf", f"[backup]\ntype=sftp\nhost=127.0.0.1\nport={port}\nuser=ci\n"
                                 f"pass={password}\nknown_hosts_file={known}\ndisable_hashcheck=true\nshell_type=none\n")
                from test_portable_recovery import files
                port_pg = os.environ["PG_TEST_PORT"]
                services, passwords = files(root, port=port_pg)
                source = root / "source"
                runner.recovery.backup(runner.recovery.Client(services, passwords), source, {}, {})
                approved_config = config.read_bytes()
                with nullcontext():
                    for phase, extra in (
                        ("seal", ["--bundle", source, "--recipients", recipients]),
                        ("publish", ["--package", root / "seal", "--config", config, "--prefix", "backups", "--confirm-publish"]),
                        ("recover", ["--receipt", root / "publish" / "receipt.json", "--identity", identity, "--config", config, "--prefix", "backups"]),
                    ):
                        with redirect_stdout(io.StringIO()):
                            code = runner.main([phase, "--work", str(root / phase), "--report", str(root / f"{phase}.json"),
                                                *map(str, extra)])
                        self.assertEqual(code, 0, (root / f"{phase}.json").read_text())
                        self.assertEqual(config.read_bytes(), approved_config)
                    self.assertEqual((root / "recover/bundle/siege.dump").read_bytes(), (source / "siege.dump").read_bytes())
                    # Restore only the decrypted remote copy into fresh, least-privilege CI databases.
                    run_id = uuid.uuid4()
                    names = {app: f"rsl_restore_{run_id.hex}_{app}" for app in runner.recovery.APPS}
                    for app, role in runner.recovery.APPS.items():
                        sql = f"CREATE DATABASE {names[app]} OWNER {role}; REVOKE ALL ON DATABASE {names[app]} FROM PUBLIC;"
                        # CREATE DATABASE cannot run in a multi-statement transaction.
                        for statement in sql.split(";"):
                            if statement.strip():
                                subprocess.run(["psql", "--no-psqlrc", "--no-password", "-h", "127.0.0.1", "-p", port_pg,
                                    "-U", "postgres", "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-qAt", "-c", statement],
                                    check=True, capture_output=True, env={**os.environ, "PGPASSWORD": "synthetic-admin-password"})
                    services, passwords = files(root, dbnames=names, port=port_pg)
                    recovered = root / "recover/bundle"
                    manifest = runner.recovery.verified_manifest(recovered, {})
                    checks = {}
                    runner.recovery.restore(runner.recovery.Client(services, passwords), recovered, run_id, manifest, checks)
                    self.assertTrue(checks["siege_restored_controls_match"] and checks["mom_restored_controls_match"])
                    wrong = root / "wrong-key"
                    subprocess.run(["age-keygen", "-o", str(wrong)], check=True, capture_output=True)
                    wrong.chmod(0o600)
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(runner.main(["recover", "--work", str(root / "wrong"), "--report", str(root / "wrong.json"),
                            "--receipt", str(root / "seal/receipt.json"), "--identity", str(wrong), "--config", str(config),
                            "--prefix", "backups"]), 1)
                    self.assertEqual(json.loads((root / "wrong.json").read_text())["error"], "tool_failed")
                    receipt = json.loads((root / "seal/receipt.json").read_text())
                    remote = store / "backups" / f'{receipt["copy_id"]}.tar.age'
                    self.assertNotIn(b"private-workload", remote.read_bytes())
                    remote.write_bytes(b"X" * remote.stat().st_size)
                    with redirect_stdout(io.StringIO()):
                        self.assertEqual(runner.main(["recover", "--work", str(root / "bad"), "--report", str(root / "bad.json"),
                            "--receipt", str(root / "seal/receipt.json"), "--identity", str(identity), "--config", str(config),
                            "--prefix", "backups"]), 1)
                    self.assertEqual(json.loads((root / "bad.json").read_text())["error"], "ciphertext_integrity_failed")
                    # Rehearse the complete hourly job (no real external notification).
                    from test_portable_backup_job import job, configuration
                    job_config_path, job_config = configuration(root)
                    services, passwords = files(root, port=port_pg)
                    values = json.loads(job_config_path.read_text())
                    values.update(services=str(services), passwords=str(passwords), recipients=str(recipients),
                                  remote_config=str(config), prefix="backups")
                    private(job_config_path, json.dumps(values))
                    with patch.object(job, "ping") as notify, redirect_stdout(io.StringIO()):
                        self.assertEqual(job.main(["run", "--config", str(job_config_path), "--confirm-run"]), 0)
                    notify.assert_called_once_with(job_config["heartbeat_file"], True)
                    scheduled = list((job_config["root"] / "runs").iterdir())
                    self.assertEqual(len(scheduled), 1)
                    result = json.loads((scheduled[0] / "job.json").read_text())
                    self.assertEqual(result["result"], "PASS")
                    self.assertTrue(result["checks"]["remote_receipt_readback"])
                    self.assertEqual((store / "backups" / f'{result["copy_id"]}.receipt.json').read_bytes(),
                                     (scheduled[0] / "publish/receipt.json").read_bytes())
            finally:
                server.terminate()
                server.wait(timeout=10)
