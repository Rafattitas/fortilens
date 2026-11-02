"""Failure injection uses private temporary configurations and a fake engine."""

import json
import sys
import tempfile
import time
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from test_management import FakeDocker, fake_device

    from management.common import Problem, atomic
    from management.configuration import Configuration
    from management.models import Apply
    from management.runtime import Docker, Operations
except ImportError:
    raise unittest.SkipTest("Runtime tests require management/requirements-test.txt.")

SECRET = "synthetic-runtime-private-failure"


class RuntimeErrorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.config = Configuration(root / "stack", root / "control", project="error-fixture")
        self.docker = FakeDocker(self.config)
        self.operations = Operations(self.config, self.docker, fake_device)
        self.addCleanup(self.operations.executor.shutdown, wait=True)

    def plan(self, *, existing=False):
        if existing:
            first = self.plan()
            self.config.install_files(first.plan_id)
            self.config.complete(first.plan_id)
        view = self.config.view()
        settings = deepcopy(view["settings"])
        settings["fortigate_host"] = "192.0.2.1"
        settings["site_name"] = "Synthetic " + str(view["revision"])
        saved = self.config.save(
            {
                "revision": view["revision"],
                "settings": settings,
                "api_token": "synthetic-api-token",
                "replace_token": True,
            }
        )
        candidate = self.config.prepare(saved["revision"])
        return Apply(plan_id=candidate["plan_id"], revision=saved["revision"], confirm=True)

    def wait(self, identity):
        until = time.monotonic() + 8
        while time.monotonic() < until:
            value = self.operations.read_job(identity)
            if value["status"] in ("complete", "failed") and not self.operations.busy.locked():
                return value
            time.sleep(0.01)
        self.fail("Synthetic deployment did not finish")

    def test_queue_disk_failure_releases_lock_before_any_engine_operation(self):
        request = Apply(plan_id="a" * 32, revision=0, confirm=True)
        with patch("management.runtime.atomic", side_effect=OSError(SECRET)):
            with self.assertRaises(Problem) as caught:
                self.operations.apply(request)
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn(SECRET, caught.exception.message)
        self.assertFalse(self.operations.busy.locked())
        self.assertEqual(self.docker.calls, [])

    def test_executor_rejection_releases_apply_and_rollback_locks(self):
        request = Apply(plan_id="a" * 32, revision=0, confirm=True)
        for name in ("apply", "rollback"):
            with self.subTest(operation=name):
                with patch.object(
                    self.operations.executor, "submit", side_effect=RuntimeError(SECRET)
                ):
                    with self.assertRaises(Problem) as caught:
                        getattr(self.operations, name)(request)
                self.assertEqual(caught.exception.status, 503)
                self.assertNotIn(SECRET, caught.exception.message)
                self.assertFalse(self.operations.busy.locked())

    def test_partial_configuration_install_is_restored_before_start(self):
        request = self.plan(existing=True)
        _, plan = self.config.plan(request.plan_id)
        before = {
            row["path"]: (self.config.root / row["path"]).read_bytes()
            for row in plan["files"]
            if row["before"] is not None
        }
        original = self.config.write_target
        calls = 0

        def fail_once(row, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError(SECRET)
            return original(row, data)

        with patch.object(self.config, "write_target", side_effect=fail_once):
            job = self.operations.apply(request)
            result = self.wait(job["job_id"])
        self.assertEqual(result["status"], "failed")
        self.assertTrue(result["configuration_restored"])
        self.assertNotIn(SECRET, json.dumps(result))
        self.assertFalse(any(call[0] == "start" for call in self.docker.calls))
        for name, data in before.items():
            self.assertEqual((self.config.root / name).read_bytes(), data)

    def test_rejected_old_applied_plan_does_not_restore_configuration(self):
        request = self.plan()
        self.config.install_files(request.plan_id)
        self.config.complete(request.plan_id)
        # A later saved revision makes the old request invalid. The APPLIED
        # marker belongs to the prior operation, not a partial write this time.
        self.plan()
        before = self.config.state()
        with patch.object(self.config, "restore", wraps=self.config.restore) as restore:
            job = self.operations.apply(request)
            result = self.wait(job["job_id"])
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["configuration_restored"])
        restore.assert_not_called()
        self.assertEqual(self.config.state(), before)
        self.assertEqual(self.config.plan(request.plan_id)[1]["status"], "APPLIED")

    def test_same_revision_replay_does_not_restore_an_already_started_plan(self):
        request = self.plan(existing=True)
        self.config.install_files(request.plan_id)
        self.config.complete(request.plan_id)
        folder, plan = self.config.plan(request.plan_id)
        before = self.config.state()
        before_files = {
            row["path"]: (self.config.root / row["path"]).read_bytes() for row in plan["files"]
        }
        self.assertEqual(before["revision"], request.revision)
        for status in ("APPLIED", "APPLYING", "FAILED"):
            with self.subTest(plan_status=status):
                plan["status"] = status
                atomic(folder / "plan.json", plan)
                with patch.object(self.config, "restore", wraps=self.config.restore) as restore:
                    job = self.operations.apply(request)
                    result = self.wait(job["job_id"])
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["message"], "Prepare and review the latest revision.")
                self.assertFalse(result["configuration_restored"])
                self.assertFalse(result["rollback_available"])
                restore.assert_not_called()
                self.assertEqual(self.config.state(), before)
                self.assertEqual(self.config.plan(request.plan_id)[1]["status"], status)
                for name, data in before_files.items():
                    self.assertEqual((self.config.root / name).read_bytes(), data)
        self.assertFalse(any(call[0] == "start" for call in self.docker.calls))

    def test_failed_job_remains_observable_when_failure_journal_cannot_be_saved(self):
        request = self.plan(existing=True)
        self.docker.fail = True
        original = atomic

        def failed_journal(path, value, **kwargs):
            if isinstance(value, dict) and value.get("phase") == "failed":
                raise OSError(SECRET)
            return original(path, value, **kwargs)

        with patch("management.runtime.atomic", side_effect=failed_journal):
            job = self.operations.apply(request)
            result = self.wait(job["job_id"])
        self.assertEqual(result["status"], "failed")
        self.assertFalse(self.operations.busy.locked())
        self.assertNotIn(SECRET, json.dumps(result))

    def test_completion_journal_failure_does_not_undo_committed_configuration(self):
        request = self.plan(existing=True)
        original = atomic

        def failed_journal(path, value, **kwargs):
            if isinstance(value, dict) and value.get("phase") == "complete":
                raise OSError(SECRET)
            return original(path, value, **kwargs)

        with (
            patch("management.runtime.atomic", side_effect=failed_journal),
            patch.object(self.operations, "diagnostics", return_value={"checks": []}),
        ):
            job = self.operations.apply(request)
            result = self.wait(job["job_id"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.config.state()["applied_revision"], request.revision)
        self.assertEqual(self.config.plan(request.plan_id)[1]["status"], "APPLIED")

    def test_corrupt_job_history_does_not_prevent_helper_startup_or_status(self):
        path = self.config.control / "jobs" / ("b" * 32 + ".json")
        path.parent.mkdir(exist_ok=True)
        path.write_text('{"private":"' + SECRET, encoding="utf-8")
        other = Operations(self.config, self.docker, fake_device)
        self.addCleanup(other.executor.shutdown, wait=True)
        self.assertIsNone(other.status()["latest_job"])
        with self.assertRaises(Problem) as caught:
            other.read_job("b" * 32)
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn(SECRET, caught.exception.message)
        self.assertTrue(path.exists())

    def test_storage_probe_or_cache_failure_does_not_take_down_status(self):
        with (
            patch("management.runtime.shutil.disk_usage", side_effect=OSError(SECRET)),
            patch("management.runtime.atomic", side_effect=OSError(SECRET)),
        ):
            result = self.operations.status()
        self.assertEqual(result["disk"], {"total": None, "used": None, "available": None})
        self.assertEqual(result["docker"], "Available")
        self.assertNotIn(SECRET, json.dumps(result))

    def test_invalid_docker_status_or_inspection_is_sanitized(self):
        docker = Docker(self.config)
        (self.config.root).mkdir(exist_ok=True)
        (self.config.root / self.config.compose_name).write_text("{}")
        with patch.object(docker, "run", return_value=json.dumps({"private": SECRET})):
            with self.assertRaises(Problem) as caught:
                docker.statuses()
        self.assertEqual(caught.exception.status, 503)
        with patch.object(docker, "run", side_effect=["synthetic-id", "null"]):
            with self.assertRaises(Problem) as caught:
                docker.address("grafana", 3000)
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn(SECRET, caught.exception.message)

    def test_service_network_failure_closes_connection_and_hides_details(self):
        docker = Docker(self.config)
        connection = Mock()
        connection.request.side_effect = TimeoutError(SECRET)
        with (
            patch.object(docker, "address", return_value=("192.0.2.1", 3000)),
            patch("management.runtime.http.client.HTTPConnection", return_value=connection),
        ):
            with self.assertRaises(Problem) as caught:
                docker.http("grafana", 3000, "/api/health")
        connection.close.assert_called_once()
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn(SECRET, caught.exception.message)

    def test_corrupt_configuration_is_not_reinitialized_or_returned(self):
        self.config.state_path.write_text("[1, 2]", encoding="utf-8")
        with self.assertRaises(Problem) as caught:
            self.config.state()
        self.assertEqual(caught.exception.code, "configuration_state_invalid")
        self.assertEqual(self.config.state_path.read_text(), "[1, 2]")

    def test_malformed_plan_is_rejected_before_target_files_change(self):
        request = self.plan()
        folder, plan = self.config.plan(request.plan_id)
        plan["files"] = ["invalid-row"]
        atomic(folder / "plan.json", plan)
        with self.assertRaises(Problem) as caught:
            self.config.install_files(request.plan_id)
        self.assertEqual(caught.exception.code, "plan_invalid")
        self.assertFalse((self.config.root / "compose.yaml").exists())

    def test_audit_append_failure_does_not_reverse_a_saved_revision(self):
        original = Path.open

        def unavailable(path, *args, **kwargs):
            if path.name == "audit.jsonl":
                raise OSError(SECRET)
            return original(path, *args, **kwargs)

        before = self.config.state()["revision"]
        with patch.object(Path, "open", unavailable):
            request = self.plan()
        self.assertGreater(request.revision, before)
        self.assertEqual(self.config.state()["revision"], request.revision)


if __name__ == "__main__":
    unittest.main()
