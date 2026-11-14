"""Public signal contract is bounded, exact and emits sanitized evidence."""

import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-public-signal.py"
SPEC = importlib.util.spec_from_file_location("public_signal", SCRIPT)
SIGNAL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SIGNAL)


def settings():
    return {"host": "siege.example.io", "minimum_tls_days": 14,
            "versions": {"backend_version": "1.6.0", "frontend_version": "1.6.0",
                         "bot_version": "1.6.0", "git_sha": "a" * 40}}


def responses(data):
    expiry = time.time() + 30 * 86400
    return [{"status": 200, "location": None, "tls_expiry": expiry},
            {"status": 200, "location": None, "tls_expiry": expiry,
             "json": {"frontend_version": data["versions"]["frontend_version"],
                      "git_sha": data["versions"]["git_sha"]}},
            {"status": 308, "location": "https://siege.example.io/", "tls_expiry": expiry},
            {"status": 200, "location": None, "tls_expiry": expiry,
             "json": {"status": "healthy"}},
            {"status": 200, "location": None, "tls_expiry": expiry,
             "json": data["versions"]}]


class PublicSignalTests(unittest.TestCase):
    def test_exact_public_paths_redirect_health_version_and_tls(self):
        data = settings()
        with patch.object(SIGNAL, "request", side_effect=responses(data)) as request:
            result = SIGNAL.inspect(data)
        self.assertTrue(all(result.values()), result)
        self.assertEqual([(call.args[0], call.args[1]) for call in request.call_args_list], [
            ("siege.example.io", "/"), ("siege.example.io", "/version.json"),
            ("www.siege.example.io", "/"),
            ("siege.example.io", "/api/health"), ("siege.example.io", "/api/version")])

    def test_unexpected_redirect_and_stale_certificate_fail_independently(self):
        data = settings()
        reply = responses(data)
        reply[2]["location"] = "https://other.example.io/"
        reply[3]["tls_expiry"] = time.time() + 2 * 86400
        with patch.object(SIGNAL, "request", side_effect=reply):
            result = SIGNAL.inspect(data)
        self.assertFalse(result["www_redirect"])
        self.assertFalse(result["apex_tls_expiry"])
        self.assertTrue(result["api_health"])
        self.assertTrue(result["release_identity"])

    def test_api_failure_is_distinct_from_site_failure(self):
        data = settings()
        reply = responses(data)
        reply[3]["json"] = {"status": "degraded"}
        with patch.object(SIGNAL, "request", side_effect=reply):
            result = SIGNAL.inspect(data)
        self.assertTrue(result["apex_home"])
        self.assertFalse(result["api_health"])

    def test_requires_explicit_dispatch_and_omits_origin_and_version_from_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            configuration = root / "config.json"
            report = root / "report.json"
            configuration.write_text(json.dumps({"origin": "https://siege.example.io",
                "versions": settings()["versions"], "minimum_tls_days": 14}))
            with patch.object(SIGNAL, "inspect") as inspect:
                self.assertEqual(SIGNAL.main(["--config", str(configuration),
                    "--report", str(report)]), 2)
                inspect.assert_not_called()
            with patch.object(SIGNAL, "inspect", return_value={name: True for name in SIGNAL.CHECKS}):
                self.assertEqual(SIGNAL.main(["--config", str(configuration),
                    "--report", str(report), "--confirm-external-readonly"]), 0)
            raw = report.read_text()
            self.assertNotIn("siege.example.io", raw)
            self.assertNotIn("1.6.0", raw)
            self.assertEqual(json.loads(raw)["status"], "PASS")
            self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertEqual(SIGNAL.main(["--config", str(configuration),
                "--report", str(report), "--confirm-external-readonly"]), 2)


if __name__ == "__main__":
    unittest.main()
