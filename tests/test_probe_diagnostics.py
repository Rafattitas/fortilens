"""Selected probes and polling success must not imply omitted/source coverage."""

import importlib.util
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
spec = importlib.util.spec_from_file_location("probe_repair_setup", ROOT / "portable/setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

import doctor
from test_doctor import CONFIG, NOW, FakeRequest, healthy_points


class ProbeRepairTests(unittest.TestCase):
    def test_insecure_tls_remains_visible_despite_successful_metrics(self):
        report = doctor.run(
            Path("."),
            {**CONFIG, "api_tls_verify": False},
            FakeRequest(),
            lambda *a: [],
            "prom",
            "loki",
            now=NOW,
            service_reader=lambda *a: [],
        )
        warning = next(c for c in report["checks"] if c["id"] == "fortigate_tls")
        self.assertEqual(warning["status"], "WARN")
        self.assertFalse(warning["evidence"]["verification_enabled"])

    def test_configured_tls_does_not_fabricate_a_successful_handshake(self):
        report = doctor.run(
            Path("."),
            {**CONFIG, "api_tls_verify": True},
            FakeRequest(),
            lambda *a: [],
            "prom",
            "loki",
            now=NOW,
            service_reader=lambda *a: [],
        )
        self.assertFalse(any(c["id"] == "fortigate_tls" for c in report["checks"]))

    def test_explicit_optional_profile_preserves_credentials_and_required_probes(self):
        record = {"token": "synthetic-private-token", "probes": {"exclude": ["Wifi"]}}
        before = deepcopy(record)
        names = [
            "Log/Fortianalyzer/Status",
            "System/Interface/Transceivers",
            "System/SensorInfo",
        ]
        result = setup.exclude_optional_probes(record, names)
        self.assertEqual(record, before)
        self.assertEqual(result["token"], record["token"])
        self.assertEqual(result["probes"]["exclude"], ["Wifi", *names])
        # The pinned upstream selector is a case-sensitive prefix match.
        for required in (
            "System/Interface",
            "VirtualWAN/HealthCheck",
            "System/Status",
            "System/Performance/Status",
            "System/Resource/Usage",
        ):
            self.assertFalse(any(required.startswith(prefix) for prefix in names))
        self.assertEqual(setup.exclude_optional_probes(result, names), result)

    def test_existing_include_policy_and_noop_are_preserved(self):
        record = {"token": "synthetic", "probes": {"include": ["System", "VirtualWAN"]}}
        self.assertEqual(setup.exclude_optional_probes(record, []), record)
        result = setup.exclude_optional_probes(record, ["System/SensorInfo"])
        self.assertEqual(result["probes"]["include"], ["System", "VirtualWAN"])

    def test_broad_or_unknown_new_exclusions_are_rejected_without_secret_text(self):
        for names in (["System"], ["VirtualWAN"], [""], ["synthetic-private-token"], "System"):
            with self.subTest(names=names), self.assertRaises(setup.SetupError) as caught:
                setup.exclude_optional_probes({"token": "synthetic-private-token"}, names)
            self.assertNotIn("synthetic-private-token", str(caught.exception))

    def test_malformed_existing_profile_fails_closed(self):
        for profile in (["System"], {"exclude": "System"}, {"unexpected": []}):
            with self.subTest(profile=profile), self.assertRaises(setup.SetupError):
                setup.exclude_optional_probes(
                    {"token": "synthetic", "probes": profile}, ["System/SensorInfo"]
                )

    def test_portable_profile_reader_does_not_expose_credentials_or_guess_yaml(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "secrets" / "fortigate-key.yaml"
            path.parent.mkdir()
            path.write_text(json.dumps({setup.target_url(CONFIG): {"token": "synthetic"}}))
            self.assertEqual(setup.exporter_probe_exclusions(folder, CONFIG), [])
            path.write_text("custom: yaml\n")
            self.assertIsNone(setup.exporter_probe_exclusions(folder, CONFIG))

    def test_partial_probe_failure_stays_fail_and_has_bounded_observation_evidence(self):
        points = healthy_points()
        points[1]["values"] = [[NOW - 200, "1"], [NOW - 10, "0"]]
        samples = doctor.metric_snapshot(
            Path("/synthetic"), CONFIG, FakeRequest(points), "fm-prometheus", NOW, 300
        )
        checks = doctor.metrics_checks(samples, CONFIG, 300)
        item = next(row for row in checks if row["id"] == "fortigate_api")
        self.assertEqual((item["status"], item["code"]), ("FAIL", "probe_failed"))
        self.assertTrue(item["evidence"]["partial_metrics_received"])
        self.assertEqual(item["evidence"]["last_success_at"], NOW - 200)
        self.assertEqual(item["evidence"]["last_failure_at"], NOW - 10)
        self.assertEqual(item["evidence"]["observation_window_seconds"], 600)
        self.assertNotIn("invalid token", item["message"].lower())

    def test_optional_capability_omission_is_separate_from_aggregate_probe_success(self):
        item = doctor.probe_policy_check(["System/SensorInfo"])[0]
        self.assertEqual(item["status"], "WARN")
        self.assertEqual(item["code"], "exporter_optional_probes_excluded")
        self.assertIn("System/SensorInfo", item["message"])
        self.assertEqual(doctor.probe_policy_check([]), [])
        self.assertEqual(doctor.probe_policy_check(None), [])
        unknown = doctor.probe_policy_check(["synthetic-private-token"])[0]
        self.assertNotIn("synthetic-private-token", json.dumps(unknown))
        self.assertTrue(unknown["evidence"]["custom_exclusions_present"])

    def test_poll_can_pass_without_any_delivery_observation(self):
        report = {
            "last_attempt": NOW - 10,
            "last_poll_ok": True,
            "processing_lag_seconds": 5,
            "last_delivery_ns": None,
        }
        checks = {item["id"]: item for item in doctor.accounting_checks(report, NOW, 300)}
        self.assertEqual(checks["accounting_poll"]["status"], "PASS")
        self.assertEqual(checks["accounting_delivery"]["status"], "UNKNOWN")
        self.assertIn("not zero", checks["accounting_delivery"]["action"])

    def test_actual_delivery_does_not_certify_complete_or_eligible_usage(self):
        item = doctor.accounting_delivery_check({"last_delivery_ns": (NOW - 10) * 10**9}, NOW, 300)
        self.assertEqual(item["status"], "PASS")
        self.assertEqual(item["evidence"]["traffic_coverage"], "UNKNOWN")
        for stamp in ((NOW - 1000) * 10**9, (NOW + 1000) * 10**9):
            self.assertEqual(
                doctor.accounting_delivery_check({"last_delivery_ns": stamp}, NOW, 300)["status"],
                "WARN",
            )

    def test_invalid_delivery_timestamps_never_crash_or_become_zero_usage(self):
        for stamp in (None, True, "synthetic-private-token", float("nan"), float("inf"), 10**4000):
            with self.subTest(kind=type(stamp).__name__):
                item = doctor.accounting_delivery_check({"last_delivery_ns": stamp}, NOW, 300)
                self.assertEqual(item["status"], "UNKNOWN")
                self.assertNotIn("synthetic-private-token", json.dumps(item))


if __name__ == "__main__":
    unittest.main()
