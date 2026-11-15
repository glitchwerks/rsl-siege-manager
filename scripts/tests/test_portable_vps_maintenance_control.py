"""Disposable host-control state and resume-evidence contract checks."""

from argparse import Namespace
from contextlib import nullcontext
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "portable-vps-maintenance-control.py"
SPEC = importlib.util.spec_from_file_location("maintenance_control", SCRIPT)
CONTROL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTROL)


class MaintenanceControlTests(unittest.TestCase):
    def test_failed_drain_leaves_state_paused(self):
        current = {"state": "active"}

        def replace(_project, value):
            current["state"] = value
            return True

        args = Namespace(
            phase="pause",
            confirm="pause",
            project_directory=Path("/synthetic"),
            bearer_report=[],
            monitor_report=None,
        )
        with patch.object(CONTROL.os, "geteuid", return_value=0), patch.object(
            CONTROL.PAUSE.GATE, "protected", return_value=True
        ), patch.object(
            CONTROL.PAUSE, "project_lock", return_value=nullcontext()
        ), patch.object(
            CONTROL, "state", side_effect=lambda _p: current["state"]
        ), patch.object(
            CONTROL, "replace_state", side_effect=replace
        ), patch.object(
            CONTROL, "drain", return_value=False
        ), patch.object(
            CONTROL,
            "history",
            return_value={
                "schema": 1,
                "pause_started_at": None,
                "last_resumed_at": None,
            },
        ), patch.object(
            CONTROL, "replace_history", return_value=True
        ):
            result = CONTROL.run(args)
        self.assertEqual(result["status"], "STOP")
        self.assertEqual(current["state"], "paused")

    def test_second_pause_preserves_first_boundary_for_join_reconciliation(self):
        current = {"state": "active"}
        first = "2026-11-15T00:00:00+00:00"
        written = []
        args = Namespace(
            phase="pause",
            confirm="pause",
            project_directory=Path("/synthetic"),
            bearer_report=[],
            monitor_report=None,
        )
        with patch.object(CONTROL.os, "geteuid", return_value=0), patch.object(
            CONTROL.PAUSE.GATE, "protected", return_value=True
        ), patch.object(
            CONTROL.PAUSE, "project_lock", return_value=nullcontext()
        ), patch.object(
            CONTROL, "state", side_effect=lambda _p: current["state"]
        ), patch.object(
            CONTROL,
            "replace_state",
            side_effect=lambda _p, value: current.update(state=value) or True,
        ), patch.object(
            CONTROL,
            "history",
            return_value={
                "schema": 1,
                "pause_started_at": first,
                "last_resumed_at": "2026-11-15T01:00:00+00:00",
            },
        ), patch.object(
            CONTROL,
            "replace_history",
            side_effect=lambda _p, value: written.append(value) or True,
        ), patch.object(
            CONTROL, "drain", return_value=True
        ):
            result = CONTROL.run(args)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(written[0]["pause_started_at"], first)

    def test_resume_without_evidence_never_reactivates(self):
        args = Namespace(
            phase="resume",
            confirm="resume",
            project_directory=Path("/synthetic"),
            bearer_report=[],
            monitor_report=None,
        )
        with patch.object(CONTROL.os, "geteuid", return_value=0), patch.object(
            CONTROL.PAUSE.GATE, "protected", return_value=True
        ), patch.object(
            CONTROL.PAUSE, "project_lock", return_value=nullcontext()
        ), patch.object(
            CONTROL, "state", return_value="paused"
        ), patch.object(
            CONTROL, "drain", return_value=True
        ), patch.object(
            CONTROL, "resume_evidence", return_value=False
        ), patch.object(
            CONTROL,
            "history",
            return_value={
                "schema": 1,
                "pause_started_at": "2026-11-15T00:00:00+00:00",
                "last_resumed_at": None,
            },
        ), patch.object(
            CONTROL, "replace_state"
        ) as replace, patch.object(
            Path, "stat"
        ) as metadata:
            metadata.return_value.st_mtime = 100.0
            result = CONTROL.run(args)
        self.assertEqual(result["status"], "STOP")
        replace.assert_not_called()

    def test_resume_records_boundary_before_reopening_admission(self):
        args = Namespace(
            phase="resume",
            confirm="resume",
            project_directory=Path("/synthetic"),
            bearer_report=[],
            monitor_report=None,
        )
        calls = []
        prior = {
            "schema": 1,
            "pause_started_at": "2026-11-15T00:00:00+00:00",
            "last_resumed_at": None,
        }
        with patch.object(CONTROL.os, "geteuid", return_value=0), patch.object(
            CONTROL.PAUSE.GATE, "protected", return_value=True
        ), patch.object(
            CONTROL.PAUSE, "project_lock", return_value=nullcontext()
        ), patch.object(
            CONTROL, "state", return_value="paused"
        ), patch.object(
            CONTROL, "drain", return_value=True
        ), patch.object(
            CONTROL, "resume_evidence", return_value=True
        ), patch.object(
            CONTROL, "history", return_value=prior
        ), patch.object(
            CONTROL,
            "replace_history",
            side_effect=lambda _p, _v: calls.append("history") or True,
        ), patch.object(
            CONTROL,
            "replace_state",
            side_effect=lambda _p, _v: calls.append("state") or True,
        ), patch.object(
            Path, "stat"
        ) as metadata:
            metadata.return_value.st_mtime = 100.0
            result = CONTROL.run(args)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(calls, ["history", "state"])

    def test_pause_replace_is_atomic_and_survives_new_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            control = project / "control"
            control.mkdir()
            state = control / "state"
            state.write_bytes(b"active\n")
            state.chmod(0o644)
            real_fstat = os.fstat

            def root_fstat(fd):
                info = real_fstat(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0)

            with patch.object(CONTROL, "root_owned", return_value=True), patch.object(
                CONTROL.os, "fstat", side_effect=root_fstat
            ):
                original_inode = state.stat().st_ino
                self.assertEqual(CONTROL.state(project), "active")
                self.assertTrue(CONTROL.replace_state(project, "paused"))
                self.assertEqual(CONTROL.state(project), "paused")
                self.assertNotEqual(original_inode, state.stat().st_ino)
                self.assertEqual(list(control.iterdir()), [state])

    def test_pause_history_is_atomic_and_rejects_invalid_shape(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            control = project / "control"
            control.mkdir()
            path = control / "history.json"
            initial = {"schema": 1, "pause_started_at": None, "last_resumed_at": None}
            path.write_text(json.dumps(initial) + "\n")
            path.chmod(0o644)
            real_fstat = os.fstat

            def root_fstat(fd):
                info = real_fstat(fd)
                return SimpleNamespace(
                    st_mode=info.st_mode, st_uid=0, st_size=info.st_size
                )

            with patch.object(CONTROL, "root_owned", return_value=True), patch.object(
                CONTROL.os, "fstat", side_effect=root_fstat
            ):
                self.assertEqual(CONTROL.history(project), initial)
                paused = {
                    "schema": 1,
                    "pause_started_at": "2026-11-15T00:00:00+00:00",
                    "last_resumed_at": None,
                }
                old_inode = path.stat().st_ino
                self.assertTrue(CONTROL.replace_history(project, paused))
                self.assertEqual(CONTROL.history(project), paused)
                self.assertNotEqual(path.stat().st_ino, old_inode)
                path.write_text('{"schema":2}\n')
                self.assertIsNone(CONTROL.history(project))

    def test_invalid_or_missing_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            control = project / "control"
            control.mkdir()
            state = control / "state"
            real_fstat = os.fstat

            def root_fstat(fd):
                info = real_fstat(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_uid=0)

            with patch.object(CONTROL, "root_owned", return_value=True), patch.object(
                CONTROL.os, "fstat", side_effect=root_fstat
            ):
                self.assertIsNone(CONTROL.state(project))
                state.write_bytes(b"active\npaused\n")
                state.chmod(0o644)
                self.assertIsNone(CONTROL.state(project))
                self.assertFalse(CONTROL.replace_state(project, "paused"))

    def test_drain_requires_three_paused_zero_inflight_samples(self):
        with patch.object(CONTROL, "state", return_value="paused"), patch.object(
            CONTROL,
            "service_status",
            side_effect=[
                {"paused": True, "inflight": 1},
                {"paused": True, "inflight": 0},
                {"paused": True, "inflight": 0},
                {"paused": True, "inflight": 0},
                {"paused": True, "inflight": 0},
                {"paused": True, "inflight": 0},
                {"paused": True, "inflight": 0},
                {"paused": True, "inflight": 0},
            ],
        ), patch.object(CONTROL.time, "sleep"):
            self.assertTrue(CONTROL.drain(Path("/synthetic"), attempts=4))

    def test_resume_requires_both_directions_and_fresh_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sidecar = root / "sidecar.json"
            reverse = root / "reverse.json"
            monitor = root / "monitor.json"
            for path in (sidecar, reverse, monitor):
                path.write_text("{}")
            paused_at = (
                min(path.stat().st_mtime for path in (sidecar, reverse, monitor)) - 1
            )
            evidence = {
                path: {
                    "phase": "bearer-activate",
                    "status": "PASS",
                    "pair": pair,
                    "checks": {
                        "baseline_current": True,
                        "writers_stopped": True,
                        "candidate_installed": True,
                        "paired_verification": True,
                        "public_health": True,
                    },
                    "verification": {
                        "snapshot": True,
                        "exact_images_and_targeted_recreation": True,
                        "sidecar_accepted": True,
                        "reverse-api_accepted": True,
                        f"{pair}_stale_rejected": True,
                    },
                }
                for path, pair in ((sidecar, "sidecar"), (reverse, "reverse-api"))
            }
            evidence[monitor] = {
                "phase": "public-monitor",
                "status": "PASS",
                "observation_passed": True,
                "delivery_acknowledged": True,
            }
            later = max(sidecar.stat().st_mtime, reverse.stat().st_mtime) + 1
            os.utime(monitor, (later, later))
            with patch.object(
                CONTROL, "private_report", side_effect=lambda p: evidence[p]
            ):
                self.assertTrue(
                    CONTROL.resume_evidence([sidecar, reverse], monitor, paused_at)
                )
                evidence[reverse]["verification"]["reverse-api_stale_rejected"] = False
                self.assertFalse(
                    CONTROL.resume_evidence([sidecar, reverse], monitor, paused_at)
                )
                evidence[reverse]["verification"]["reverse-api_stale_rejected"] = True
                os.utime(monitor, (paused_at, paused_at))
                self.assertFalse(
                    CONTROL.resume_evidence([sidecar, reverse], monitor, paused_at)
                )


if __name__ == "__main__":
    unittest.main()
