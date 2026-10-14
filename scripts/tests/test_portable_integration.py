"""Isolated acceptance fixtures: no network, gateway, workload or real messages."""
from contextlib import redirect_stdout
import copy
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import urllib.error
import zlib

SPEC = importlib.util.spec_from_file_location("smoke", Path(__file__).parents[1] / "portable-integration.py")
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def configuration():
    return {"schema": 1, "dedicated_test_identity": True, "origin": "https://pilot.fixture-domain.net",
            "sidecar_origin": "http://127.0.0.1:8001", "versions": {"backend_version": "1.4.3",
            "frontend_version": "1.5.0", "bot_version": "1.6.0+123.abcdef1", "git_sha": "a" * 40},
            "member_id": "123456789012345678", "username": "fixture_recipient", "channel_name": "rsl-smoke-fixture"}


class Fixture:
    def __init__(self, cfg):
        self.cfg = cfg
        self.calls = []
        self.version = cfg["versions"]
        self.members = [{"id": cfg["member_id"], "username": cfg["username"], "display_name": "Private display"}]
        self.fail_send = False

    def open(self, request, **kwargs):
        self.calls.append(request)
        url = request.full_url
        auth = request.get_header("Authorization")
        code = 200
        if url == self.cfg["origin"] + "/api/health":
            body = {"status": "healthy"}
        elif url == self.cfg["origin"] + "/api/version":
            body = self.version
        elif url.endswith("/api/health"):
            body = {"status": "healthy", "bot_connected": True}
        elif url.endswith("/api/version"):
            body = {"version": self.cfg["versions"]["bot_version"]}
        elif auth is None:
            code, body = 403, {"detail": "Not authenticated"}
        elif auth.startswith("Bearer invalid-smoke-"):
            code, body = 401, {"detail": "Invalid API key"}
        elif url.endswith("/api/members"):
            body = self.members
        elif "/api/members/" in url:
            body = {"is_member": True, "discord_id": self.cfg["member_id"], "username": self.cfg["username"],
                    "display_name": "Private display", "roles": [], "role_names": []}
        elif request.data == b"{}":
            code, body = 422, {"detail": [{"type": "missing"}]}
        elif self.fail_send:
            raise OSError("token-and-private-member-content")
        else:
            body = {"status": "sent", "url": "https://cdn.fixture-domain.net/private-message-id"}
        response = MagicMock(status=code)
        response.read.return_value = json.dumps(body).encode()
        response.__enter__.return_value = response
        return response


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = configuration()
        self.config_path = self.root / "config.json"
        self.token = self.root / "token"
        self.token.write_text("secret-fixture-token-long-enough")
        self.token.chmod(0o600)
        self.save()
        self.args = ["--config", str(self.config_path), "--token-file", str(self.token),
                     "--report", str(self.root / "report.json")]

    def save(self):
        self.config_path.write_text(json.dumps(self.cfg))
        self.config_path.chmod(0o600)

    def run_main(self, mode="readonly", gates=None, fixture=None):
        fixture = fixture or Fixture(self.cfg)
        opener = MagicMock()
        opener.open.side_effect = fixture.open
        with patch.object(smoke.urllib.request, "build_opener", return_value=opener), redirect_stdout(io.StringIO()) as out:
            code = smoke.main([mode, *self.args, *(gates or [])])
        result = json.loads(out.getvalue())
        for forbidden in (self.cfg["username"], self.cfg["member_id"], self.cfg["channel_name"],
                          "secret-fixture-token", "Private display", "fixture-domain", "private-message-id"):
            self.assertNotIn(forbidden, out.getvalue())
        self.assertEqual(json.loads((self.root / "report.json").read_text()), result)
        return code, result, fixture

    def test_readonly_checks_identity_auth_and_members_without_posts(self):
        code, result, fixture = self.run_main(gates=["--confirm-isolated-target"])
        self.assertEqual(code, 0)
        self.assertEqual(len(result["checks"]), 9)
        self.assertTrue(all(r.method == "GET" for r in fixture.calls))
        self.assertEqual(result["writes_attempted"], 0)
        self.assertFalse(result["cleanup_required"])
        self.assertFalse(result["pilot_accepted"])
        self.assertFalse(result["delivery_observed"])

    def test_full_requires_both_gates_before_network(self):
        for gates in ([], ["--confirm-isolated-target"], ["--confirm-discord-writes"]):
            (self.root / "report.json").unlink(missing_ok=True)
            code, result, fixture = self.run_main("full", gates)
            self.assertEqual(code, 1)
            self.assertEqual(fixture.calls, [])
            self.assertEqual(result["writes_attempted"], 0)

    def test_full_contract_payloads_and_cleanup_evidence(self):
        code, result, fixture = self.run_main("full", ["--confirm-isolated-target", "--confirm-discord-writes"])
        self.assertEqual(code, 0)
        self.assertEqual(result["writes_attempted"], 3)
        self.assertTrue(result["cleanup_required"])
        posts = [r for r in fixture.calls if r.method == "POST"]
        self.assertEqual(len(posts), 4)
        self.assertEqual(posts[0].data, b"{}")
        self.assertEqual(set(json.loads(posts[1].data)), {"username", "message"})
        self.assertEqual(set(json.loads(posts[2].data)), {"channel_name", "message"})
        self.assertIn(result["correlation_id"], json.loads(posts[1].data)["message"])
        self.assertIn(smoke.PNG, posts[3].data)
        self.assertIn(result["correlation_id"].encode(), posts[3].data)
        self.assertIn(b'name="channel_name"', posts[3].data)
        self.assertIn(b'name="file"', posts[3].data)

    def test_timeout_may_have_written_never_retries(self):
        fixture = Fixture(self.cfg)
        fixture.fail_send = True
        code, result, fixture = self.run_main("full", ["--confirm-isolated-target", "--confirm-discord-writes"], fixture)
        self.assertEqual(code, 1)
        self.assertEqual(result["writes_attempted"], 1)
        self.assertTrue(result["cleanup_required"])
        self.assertEqual(result["error"], "request_failed")
        self.assertEqual(sum(r.full_url.endswith("/api/notify") for r in fixture.calls), 1)

    def test_identity_mismatch_stops_before_any_sidecar_or_write(self):
        fixture = Fixture(self.cfg)
        fixture.version = {**self.cfg["versions"], "bot_version": "wrong-build"}
        code, result, fixture = self.run_main("full", ["--confirm-isolated-target", "--confirm-discord-writes"], fixture)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "siege_identity")
        self.assertTrue(all(r.full_url.startswith(self.cfg["origin"]) for r in fixture.calls))

    def test_duplicate_recipient_stops_before_writes(self):
        fixture = Fixture(self.cfg)
        fixture.members.append({**fixture.members[0], "id": "223456789012345678"})
        code, result, fixture = self.run_main("full", ["--confirm-isolated-target", "--confirm-discord-writes"], fixture)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "unique_test_recipient")
        self.assertTrue(all(r.method == "GET" for r in fixture.calls))

    def test_disconnected_bot_and_malformed_members_fail_closed(self):
        fixture = Fixture(self.cfg)
        fixture.members = [{"id": "not-a-snowflake", "username": self.cfg["username"], "display_name": "Private"}]
        code, result, fixture = self.run_main("full", ["--confirm-isolated-target", "--confirm-discord-writes"], fixture)
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "member_list_shape")
        self.assertTrue(all(r.method == "GET" for r in fixture.calls))
        (self.root / "report.json").unlink()
        original = Fixture.open

        def disconnected(instance, request, **kwargs):
            response = original(instance, request, **kwargs)
            if request.full_url == self.cfg["sidecar_origin"] + "/api/health":
                response.read.return_value = b'{"status":"healthy","bot_connected":false}'
            return response

        with patch.object(Fixture, "open", disconnected):
            code, result, fixture = self.run_main("full", ["--confirm-isolated-target", "--confirm-discord-writes"])
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "sidecar_connected")
        self.assertTrue(all(r.method == "GET" for r in fixture.calls))

    def test_corrupt_json_and_changed_auth_contract_are_sanitized(self):
        client = smoke.Client(self.cfg, "private-token")
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = b"private malformed payload"
        with patch.object(client.opener, "open", return_value=response), self.assertRaisesRegex(smoke.Stop, "^request_failed$"):
            client.request("sidecar_origin", "/api/members")
        original = Fixture.open

        def auth_bypass(instance, request, **kwargs):
            response = original(instance, request, **kwargs)
            if request.full_url.endswith("/api/members") and request.get_header("Authorization") is None:
                response.status = 200
            return response

        with patch.object(Fixture, "open", auth_bypass):
            code, result, fixture = self.run_main("full", ["--confirm-isolated-target", "--confirm-discord-writes"])
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "unexpected_http_status")
        self.assertTrue(all(r.method == "GET" for r in fixture.calls))

    def test_protected_inputs_and_dedicated_identity_required(self):
        for name, value in (("dedicated_test_identity", False), ("schema", True),
                            ("sidecar_origin", "http://10.0.0.1:8001"), ("channel_name", "general")):
            cfg = copy.deepcopy(self.cfg)
            cfg[name] = value
            self.config_path.write_text(json.dumps(cfg))
            with self.assertRaises(smoke.Stop):
                smoke.settings(self.config_path, self.token)
        self.save()
        self.token.chmod(0o644)
        with self.assertRaises(smoke.Stop):
            smoke.settings(self.config_path, self.token)

    def test_no_report_overwrite_and_symlink_input(self):
        report = self.root / "report.json"
        report.write_text("preserve")
        with patch.object(smoke, "settings") as cfg, redirect_stdout(io.StringIO()):
            self.assertEqual(smoke.main(["readonly", *self.args, "--confirm-isolated-target"]), 1)
        cfg.assert_not_called()
        self.assertEqual(report.read_text(), "preserve")
        target = self.root / "real-token"
        self.token.rename(target)
        self.token.symlink_to(target)
        with self.assertRaises(smoke.Stop):
            smoke.read_private(self.token, 4096)

    def test_overlap_stops_before_network(self):
        with open(self.root / ".integration-smoke.lock", "w") as lock:
            os.chmod(lock.name, 0o600)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            code, result, fixture = self.run_main(gates=["--confirm-isolated-target"])
        self.assertEqual(code, 1)
        self.assertEqual(result["error"], "integration_already_running")
        self.assertEqual(fixture.calls, [])

    def test_transport_blocks_redirects_proxies_and_unbounded_response(self):
        client = smoke.Client(self.cfg, "private-token")
        self.assertTrue(any(isinstance(h, smoke.helpers.NoRedirect) for h in client.opener.handlers))
        response = MagicMock(status=200)
        response.__enter__.return_value = response
        response.read.return_value = b"x" * (smoke.MAX_RESPONSE + 1)
        with patch.object(client.opener, "open", return_value=response), self.assertRaisesRegex(smoke.Stop, "oversized_response"):
            client.request("origin", "/api/health", auth=False)
        client.deadline = 0
        with patch.object(client.opener, "open") as request, self.assertRaises(smoke.Stop):
            client.request("origin", "/api/health", auth=False)
        request.assert_not_called()

    def test_http_error_body_is_bounded_and_expected_codes_verified(self):
        client = smoke.Client(self.cfg, "private-token")
        error = urllib.error.HTTPError("private-url", 401, "private-body", {}, io.BytesIO(b'{"detail":"secret"}'))
        with patch.object(client.opener, "open", side_effect=error):
            self.assertEqual(client.request("sidecar_origin", "/api/members", expected=401), {"detail": "secret"})
        error = urllib.error.HTTPError("private-url", 500, "secret", {}, io.BytesIO(b"secret"))
        with patch.object(client.opener, "open", side_effect=error), self.assertRaisesRegex(smoke.Stop, "unexpected_http_status"):
            client.request("sidecar_origin", "/api/members")

    def test_synthetic_png_has_valid_crc_and_pixel_data(self):
        self.assertEqual(smoke.PNG[:8], b"\x89PNG\r\n\x1a\n")
        chunks = smoke.PNG[8:]
        seen = []
        while chunks:
            size = struct.unpack(">I", chunks[:4])[0]
            kind, data = chunks[4:8], chunks[8:8 + size]
            crc = struct.unpack(">I", chunks[8 + size:12 + size])[0]
            self.assertEqual(zlib.crc32(kind + data) & 0xffffffff, crc)
            if kind == b"IDAT":
                self.assertEqual(zlib.decompress(data), b"\x00\x00\x80\xff")
            seen.append(kind)
            chunks = chunks[12 + size:]
        self.assertEqual(seen, [b"IHDR", b"IDAT", b"IEND"])


if __name__ == "__main__":
    unittest.main()
