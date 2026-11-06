"""Storage planning uses disposable files and a fake registered Docker project."""

import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from management.models import Settings
    from management.runtime import Operations
except ImportError:
    raise unittest.SkipTest("Storage runtime tests require management/requirements-test.txt.")


class StorageDocker:
    def __init__(self, owner):
        self.owner = owner
        self.services = {"loki", "prometheus", "fortilens-usage"}
        self.running = True
        self.calls = []

    def command(self):
        return ["docker", "compose", "-p", self.owner.project]

    def run(self, args, timeout=10):
        self.calls.append(args)
        if "inspect" not in args:
            return args[-1] if args[-1] in self.services else ""
        service = args[-1]
        destination = {
            "loki": "/loki",
            "prometheus": "/prometheus",
            "fortilens-usage": "/data",
        }[service]
        return json.dumps(
            [
                {
                    "Config": {"Labels": {"com.docker.compose.project": self.owner.project}},
                    "State": {"Running": self.running},
                    "Mounts": [
                        {
                            "Type": "volume",
                            "Source": str(self.owner.volumes[service]),
                            "Destination": destination,
                            "Name": "synthetic-" + service,
                        }
                    ],
                }
            ]
        )


class StorageRuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        control = self.root / "control"
        control.mkdir()
        self.applied = Settings(fortigate_host="192.0.2.1").model_dump(mode="json")
        self.state = {
            "configured": True,
            "applied": self.applied,
            "settings": deepcopy(self.applied),
        }
        self.config = SimpleNamespace(
            control=control, project="synthetic-storage", state=lambda: self.state
        )
        self.volumes = {}
        for service in ("loki", "prometheus", "fortilens-usage"):
            self.volumes[service] = self.root / service
            self.volumes[service].mkdir()
        self.docker = StorageDocker(self)
        self.project = self.config.project
        self.operations = Operations(self.config, self.docker)
        self.addCleanup(self.operations.executor.shutdown, wait=True)
        self.now = 10000
        self.backlog = False
        self.healthy = True
        self.failure = None
        self.failure_epoch = None
        self.health = patch.object(self.operations, "accounting_diagnostics", self.diagnostics)
        self.health.start()
        self.addCleanup(self.health.stop)

    def diagnostics(self, _config):
        return {
            "last_poll_ok": self.healthy,
            "last_error": self.failure,
            "last_failure_seconds": self.failure_epoch,
            "checkpoint_ns": int((self.now - (500 if self.backlog else 5)) * 1e9),
        }

    def sample(self, seconds=0, size=1000, free=10**12):
        self.now = 10000 + seconds
        for volume in self.volumes.values():
            (volume / "synthetic-data").write_bytes(b"x" * size)
        with (
            patch("management.runtime.time.time", return_value=self.now),
            patch("management.runtime.shutil.disk_usage", return_value=SimpleNamespace(free=free)),
        ):
            return self.operations.storage()

    def trend(self, initial_size=1000, free=10**12):
        result = None
        for index in range(4):
            result = self.sample(index * 1200, initial_size + index * 100, free)
        return result

    def test_24_hour_growth_does_not_multiply_accounting_summary_retention(self):
        result = self.trend()
        accounting = result["components"]["fortilens-usage"]
        self.assertEqual(accounting["projection_bytes"], 7200)
        self.assertEqual(accounting["horizon_seconds"], 86400)
        self.assertEqual(accounting["bytes"], 1300)
        self.assertEqual(accounting["ledger_days"], 3)
        self.assertEqual(accounting["summary_days"], 400)
        self.assertEqual(accounting["capacity_limit_bytes"], 4 * 1024**3)
        self.assertFalse(result["partial"])

    def test_refresh_keeps_original_observed_window(self):
        before = self.trend()["components"]["loki"]
        after = self.sample(3660, 1400)["components"]["loki"]
        self.assertEqual(after["projection_bytes"], before["projection_bytes"])
        self.assertEqual(after["projection_at_seconds"], before["projection_at_seconds"])
        self.assertEqual(after["bytes"], 1400)

    def test_backlog_immediately_invalidates_a_cached_accounting_trend(self):
        self.trend()
        self.backlog = True
        result = self.sample(3660, 1400)
        accounting = result["components"]["fortilens-usage"]
        self.assertNotIn("projection_bytes", accounting)
        self.assertIn("backlog", accounting["forecast_message"])
        self.assertTrue(result["partial"])
        self.assertEqual(result["components"]["loki"]["projection_bytes"], 7200)
        self.backlog = False
        accounting = self.sample(3720, 1500)["components"]["fortilens-usage"]
        self.assertNotIn("projection_bytes", accounting)

    def test_failed_processing_and_missing_checkpoint_do_not_establish_a_trend(self):
        for failure in ("timeout", None):
            with self.subTest(failure=failure):
                self.failure = failure
                self.healthy = False
                value = self.sample()["components"]["fortilens-usage"]
                self.assertNotIn("projection_bytes", value)
                self.assertEqual(value["forecast_status"], "unavailable")
        self.healthy = True
        with patch.object(
            self.operations,
            "accounting_diagnostics",
            return_value={"last_poll_ok": True, "last_error": None, "checkpoint_ns": None},
        ):
            value = self.sample()["components"]["fortilens-usage"]
        self.assertNotIn("projection_bytes", value)

    def test_uses_applied_retention_instead_of_unapplied_draft(self):
        self.trend()
        self.state["settings"]["retention"].update(
            ledger_days=365, summary_days=3650, database_bytes=9 * 1024**3
        )
        value = self.sample(3660, 1400)["components"]["fortilens-usage"]
        self.assertEqual(value["projection_bytes"], 7200)
        self.assertEqual(value["ledger_days"], 3)
        self.assertEqual(value["summary_days"], 400)
        self.assertEqual(value["capacity_limit_bytes"], 4 * 1024**3)
        self.applied["retention"]["summary_days"] = 401
        self.assertNotIn(
            "projection_bytes", self.sample(3720, 1500)["components"]["fortilens-usage"]
        )

    def test_legacy_and_corrupt_histories_do_not_hide_measurements(self):
        for content in ('{"fortilens-usage":{"bytes":100,"at":0}}', '{"private":"broken'):
            with self.subTest(content=content):
                (self.config.control / "storage-samples.json").write_text(content)
                result = self.sample()
                self.assertEqual(len(result["components"]), 3)
                self.assertNotIn("projection_bytes", result["components"]["fortilens-usage"])
                self.assertNotIn("private", json.dumps(result))

    def test_free_space_warning_counts_additional_growth_only(self):
        self.docker.services = {"loki"}
        result = self.trend(initial_size=100000, free=7500)
        self.assertEqual(result["components"]["loki"]["projection_bytes"], 7200)
        self.assertNotIn("Warning:", result["message"])
        result = self.sample(3660, 100400, free=7100)
        self.assertIn("Warning:", result["message"])

    def test_capacity_warning_uses_the_volume_filesystem(self):
        self.docker.services = {"loki"}
        self.trend()
        self.now = 13660
        with (
            patch("management.runtime.time.time", return_value=self.now),
            patch(
                "management.runtime.shutil.disk_usage",
                side_effect=lambda path: SimpleNamespace(
                    free=10**12 if Path(path) == self.config.control else 100
                ),
            ),
        ):
            result = self.operations.storage()
        self.assertIn("measured volume filesystem", result["message"])

    def test_stopped_service_keeps_size_but_has_no_forecast(self):
        self.trend()
        self.docker.running = False
        result = self.sample(3660, 1400)
        self.assertFalse(result["available"])
        for value in result["components"].values():
            self.assertEqual(value["bytes"], 1400)
            self.assertNotIn("projection_bytes", value)

    def test_history_write_failure_is_safe_and_sanitized(self):
        with patch("management.runtime.atomic", side_effect=OSError("synthetic-private-error")):
            result = self.sample()
        self.assertEqual(len(result["components"]), 3)
        self.assertIn("could not be saved", result["message"])
        self.assertNotIn("synthetic-private-error", json.dumps(result))

    def test_volume_replacement_resets_the_old_forecast(self):
        self.trend()
        replacement = self.root / "replacement-volume"
        replacement.mkdir()
        self.volumes["loki"] = replacement
        value = self.sample(3660, 1400)["components"]["loki"]
        self.assertNotIn("projection_bytes", value)

    def test_volume_recreated_at_the_same_path_has_a_new_identity(self):
        self.trend()
        volume = self.volumes["loki"]
        old_volume = self.root / "old-loki"
        self.assertTrue(volume.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(old_volume.resolve().is_relative_to(self.root.resolve()))
        volume.rename(old_volume)
        volume.mkdir()
        value = self.sample(3660, 1400)["components"]["loki"]
        self.assertNotIn("projection_bytes", value)

    def test_first_run_storage_does_not_require_a_configured_device_address(self):
        self.state["configured"] = False
        self.state["settings"]["fortigate_host"] = ""
        result = self.sample()
        self.assertEqual(len(result["components"]), 3)

    def test_disabled_accounting_has_no_growth_forecast(self):
        self.trend()
        self.applied["monitoring"]["accounting"] = False
        result = self.sample(3660, 1400)
        value = result["components"]["fortilens-usage"]
        self.assertNotIn("projection_bytes", value)
        self.assertIn("disabled", value["forecast_message"])

    def test_new_failure_between_observations_resets_the_accounting_window(self):
        self.trend()
        self.failure_epoch = 13601
        value = self.sample(3660, 1400)["components"]["fortilens-usage"]
        self.assertNotIn("projection_bytes", value)

    def test_health_check_uses_fresh_time_after_slow_volume_probes(self):
        self.docker.services = {"fortilens-usage"}
        self.sample()
        with (
            patch("management.runtime.time.time", side_effect=[10000, 10020]),
            patch.object(
                self.operations,
                "accounting_diagnostics",
                return_value={
                    "last_poll_ok": True,
                    "last_error": None,
                    "checkpoint_ns": 10015 * 10**9,
                },
            ),
        ):
            result = self.operations.storage()
        value = result["components"]["fortilens-usage"]
        self.assertEqual(value["forecast_status"], "collecting")

    def test_ceiling_warning_does_not_cap_the_projected_storage_demand(self):
        self.docker.services = {"fortilens-usage"}
        ceiling = 67108864
        self.applied["retention"]["database_bytes"] = ceiling
        for index in range(4):
            self.now = 10000 + index * 1200
            with (self.volumes["fortilens-usage"] / "synthetic-data").open("wb") as stream:
                stream.truncate(ceiling - 1000 + index * 100)
            with (
                patch("management.runtime.time.time", return_value=self.now),
                patch(
                    "management.runtime.shutil.disk_usage",
                    return_value=SimpleNamespace(free=10**12),
                ),
            ):
                result = self.operations.storage()
        value = result["components"]["fortilens-usage"]
        self.assertEqual(value["projection_bytes"], 7200)
        self.assertIn("stops ingestion", value["capacity_warning"])


if __name__ == "__main__":
    unittest.main()
