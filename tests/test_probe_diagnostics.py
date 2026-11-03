"""Selected probes and polling success must not imply omitted/source coverage."""

import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
spec = importlib.util.spec_from_file_location("probe_repair_setup", ROOT / "portable/setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

import doctor
from test_doctor import CONFIG, NOW, FakeRequest, healthy_points, point


def native_receiver_report(**overrides):
    report = {
        "service": "running",
        "component": "native-syslog-receiver",
        "ready": True,
        "udp_bound": True,
        "worker_alive": True,
        "upstream_connected": True,
        "storage_verified": False,
        "queue_depth": 0,
        "queue_capacity": 256,
        "received_datagrams": 0,
        "handed_to_tcp": 0,
        "rejected_datagrams": 0,
        "rejected_reasons": {
            key: 0
            for key in ("source", "oversize", "utf8", "control", "format", "priority", "timestamp")
        },
        "queue_drops": 0,
        "send_errors": 0,
        "last_forwarding_seconds": None,
    }
    report.update(overrides)
    return report


class ProbeRepairTests(unittest.TestCase):
    def native_checks(self, details=None, *, callback=None, config=None, logs=None):
        if details is not None:
            callback = lambda *args: details
        report = doctor.run(
            Path("/synthetic"),
            config or {**CONFIG, "syslog_format": "fortigate"},
            FakeRequest(logs=logs),
            lambda *args: [],
            "fm-prometheus",
            "fm-loki",
            now=NOW,
            service_reader=lambda *args: [],
            native_receiver_request=callback,
        )
        return {item["id"]: item for item in report["checks"]}

    def test_native_readiness_without_input_does_not_establish_delivery(self):
        checks = self.native_checks(native_receiver_report())
        self.assertEqual(checks["native_receiver"]["status"], "PASS")
        self.assertTrue(checks["native_receiver"]["evidence"]["upstream_connected"])
        self.assertEqual(checks["native_handoff"]["status"], "UNKNOWN")
        self.assertEqual(checks["native_handoff"]["code"], "native_handoff_unobserved")
        self.assertFalse(checks["native_handoff"]["evidence"]["storage_verified"])
        self.assertEqual(checks["recent_logs"]["status"], "UNKNOWN")

    def test_native_recent_handoff_does_not_establish_loki_storage(self):
        checks = self.native_checks(
            native_receiver_report(
                received_datagrams=12,
                handed_to_tcp=12,
                upstream_connected=True,
                last_forwarding_seconds=NOW - 10,
            )
        )
        self.assertEqual(checks["native_receiver"]["status"], "PASS")
        handoff = checks["native_handoff"]
        self.assertEqual((handoff["status"], handoff["code"]), ("PASS", "native_handoff_observed"))
        self.assertFalse(handoff["evidence"]["storage_verified"])
        self.assertEqual(handoff["evidence"]["traffic_coverage"], "UNKNOWN")
        self.assertIn("does not establish", handoff["action"])
        self.assertEqual(checks["recent_logs"]["status"], "UNKNOWN")

    def test_native_historical_queue_loss_is_visible_after_forwarding_recovers(self):
        checks = self.native_checks(
            native_receiver_report(
                received_datagrams=9,
                handed_to_tcp=8,
                upstream_connected=True,
                last_forwarding_seconds=NOW - 10,
                queue_drops=1,
            )
        )
        self.assertEqual(checks["native_receiver"]["status"], "PASS")
        self.assertEqual(checks["native_handoff"]["status"], "PASS")
        self.assertEqual(checks["native_queue_loss"]["status"], "WARN")
        self.assertIn("cumulative", checks["native_queue_loss"]["action"])

    def test_native_disconnected_queued_input_and_failed_worker_do_not_pass_readiness(self):
        waiting = self.native_checks(
            native_receiver_report(
                ready=False, upstream_connected=False, received_datagrams=1, queue_depth=1
            )
        )
        self.assertEqual(waiting["native_receiver"]["status"], "FAIL")
        for flag in ("ready", "udp_bound", "worker_alive", "upstream_connected"):
            with self.subTest(flag=flag):
                checks = self.native_checks(native_receiver_report(**{flag: False}))
                self.assertEqual(checks["native_receiver"]["status"], "FAIL")

    def test_native_source_rejections_do_not_imply_authorized_input_loss(self):
        details = native_receiver_report(rejected_datagrams=3)
        details["rejected_reasons"]["source"] = 3
        checks = self.native_checks(details)
        self.assertEqual(checks["native_receiver"]["status"], "PASS")
        self.assertEqual(checks["native_receiver"]["evidence"]["source_rejections"], 3)
        self.assertNotIn("native_input_rejections", checks)
        self.assertEqual(checks["native_source_filter"]["status"], "WARN")
        self.assertFalse(
            checks["native_source_filter"]["evidence"]["authorized_input_loss_verified"]
        )
        self.assertIn("Docker UDP proxy/NAT", checks["native_source_filter"]["action"])
        details["rejected_reasons"].update(oversize=1, utf8=1, control=1, format=1)
        checks = self.native_checks(details)
        self.assertEqual(checks["native_input_rejections"]["status"], "WARN")
        self.assertNotIn(
            "source", checks["native_input_rejections"]["evidence"]["rejected_reasons"]
        )

    def test_native_old_or_future_handoff_is_not_recent(self):
        for stamp in (NOW - 301, NOW + 31):
            with self.subTest(stamp=stamp):
                checks = self.native_checks(
                    native_receiver_report(
                        received_datagrams=1, handed_to_tcp=1, last_forwarding_seconds=stamp
                    )
                )
                self.assertEqual(checks["native_handoff"]["status"], "WARN")

    def test_native_callback_failures_hide_response_and_exception_text(self):
        errors = [
            (ValueError("synthetic-private-token"), "UNKNOWN"),
            (OSError("synthetic-private-token"), "UNKNOWN"),
            (
                urllib.error.HTTPError("http://local", 503, "synthetic-private-token", {}, None),
                "FAIL",
            ),
            (
                urllib.error.HTTPError("http://local", 401, "synthetic-private-token", {}, None),
                "FAIL",
            ),
        ]
        for error, status in errors:
            with self.subTest(error=type(error).__name__, status=status):

                def failed(*args):
                    raise error

                checks = self.native_checks(callback=failed)
                self.assertEqual(checks["native_receiver"]["status"], status)
                self.assertNotIn("synthetic-private-token", json.dumps(checks))
        self.assertEqual(self.native_checks()["native_receiver"]["status"], "UNKNOWN")

    def test_native_invalid_diagnostics_fail_closed_without_echoing_payload(self):
        for patch in (
            {"ready": "synthetic-private-token"},
            {"component": "synthetic-private-token"},
            {"storage_verified": True},
            {"queue_depth": 257},
            {"queue_capacity": True},
            {"received_datagrams": -1},
            {"last_forwarding_seconds": float("inf")},
            {"last_forwarding_seconds": True},
            {"last_forwarding_seconds": 10**4000},
            {"rejected_reasons": {"source": "synthetic-private-token"}},
        ):
            with self.subTest(keys=list(patch)):
                checks = self.native_checks(native_receiver_report(**patch))
                self.assertEqual(checks["native_receiver"]["status"], "UNKNOWN")
                self.assertNotIn("synthetic-private-token", json.dumps(checks))

    def test_legacy_profile_never_calls_native_hook_or_requests_native_service(self):
        for profile in (None, "rfc5424"):
            config = dict(CONFIG)
            if profile is not None:
                config["syslog_format"] = profile
            seen_services = []

            def services(p, compose, names):
                seen_services.extend(names)
                return []

            def unexpected(*args):
                self.fail("Legacy profiles must not query the optional native receiver")

            report = doctor.run(
                Path("/synthetic"),
                config,
                FakeRequest(),
                lambda *args: [],
                "fm-prometheus",
                "fm-loki",
                now=NOW,
                service_reader=services,
                native_receiver_request=unexpected,
            )
            self.assertNotIn("fortilens-syslog", seen_services)
            self.assertFalse(any(item["id"].startswith("native_") for item in report["checks"]))

    def sla_check(self, config, points):
        samples = doctor.metric_snapshot(
            Path("/synthetic"), config, FakeRequest(points), "fm-prometheus", NOW, 300
        )
        return next(
            item
            for item in doctor.metrics_checks(samples, config, 300)
            if item["id"] == "sla_metrics"
        )

    def test_blank_sla_requires_selection_without_adopting_an_observed_check(self):
        config = {**CONFIG, "sla": ""}
        before = deepcopy(config)
        points = [
            point(
                "fortigate_virtual_wan_status",
                vdom="Office",
                sla=name,
                interface="uplink.100",
                state=state,
            )
            for name, state in (("google", "error"), ("ping", "up"), ("ping1", "down"))
        ]
        item = self.sla_check(config, points)
        self.assertEqual((item["status"], item["code"]), ("WARN", "sla_selection_required"))
        self.assertEqual(item["evidence"]["selected_sla"], "")
        self.assertEqual(item["evidence"]["observed_sla_names"], ["google", "ping", "ping1"])
        self.assertIn("no check was selected automatically", item["action"])
        self.assertEqual(config, before)

    def test_observed_sla_names_require_fresh_matching_vdom_samples(self):
        points = [
            point(
                "fortigate_virtual_wan_status", vdom="Office", sla="current", interface="uplink.100"
            ),
            point(
                "fortigate_virtual_wan_status",
                age=450,
                vdom="Office",
                sla="stale",
                interface="uplink.100",
            ),
            point(
                "fortigate_virtual_wan_status",
                age=-90,
                vdom="Office",
                sla="future",
                interface="uplink.100",
            ),
            point(
                "fortigate_virtual_wan_status",
                vdom="Other",
                sla="other-vdom",
                interface="uplink.100",
            ),
        ]
        item = self.sla_check({**CONFIG, "sla": ""}, points)
        self.assertEqual(item["evidence"]["observed_sla_names"], ["current"])
        empty = self.sla_check({**CONFIG, "sla": ""}, [])
        self.assertEqual((empty["status"], empty["code"]), ("WARN", "sla_selection_required"))
        self.assertEqual(empty["evidence"]["observed_sla_names"], [])

    def test_selected_sla_never_falls_back_to_another_fresh_probe(self):
        config = {**CONFIG, "sla": "google"}
        available = point(
            "fortigate_virtual_wan_status",
            vdom="Office",
            sla="ping",
            interface="uplink.100",
            state="up",
        )
        for selected, code in (
            (None, "metrics_missing"),
            (
                point(
                    "fortigate_virtual_wan_status",
                    age=450,
                    vdom="Office",
                    sla="google",
                    interface="uplink.100",
                ),
                "metrics_stale",
            ),
            (
                point(
                    "fortigate_virtual_wan_status",
                    vdom="Other",
                    sla="google",
                    interface="uplink.100",
                ),
                "metrics_missing",
            ),
            (
                point(
                    "fortigate_virtual_wan_status",
                    age=-90,
                    vdom="Office",
                    sla="google",
                    interface="uplink.100",
                ),
                "clock_skew",
            ),
        ):
            with self.subTest(code=code, selected=selected):
                item = self.sla_check(config, [available, *([selected] if selected else [])])
                self.assertNotEqual(item["status"], "PASS")
                self.assertEqual(item["code"], code)
                self.assertEqual(item["evidence"]["selected_sla"], "google")
                self.assertEqual(item["evidence"]["observed_sla_names"], ["ping"])

    def test_selected_unhealthy_sla_is_observed_without_substitution(self):
        config = {**CONFIG, "sla": "google"}
        points = [
            point(
                "fortigate_virtual_wan_status",
                vdom="Office",
                sla="google",
                interface="uplink.100",
                state="error",
            ),
            point(
                "fortigate_virtual_wan_status",
                vdom="Office",
                sla="ping",
                interface="uplink.100",
                state="up",
            ),
        ]
        item = self.sla_check(config, points)
        self.assertEqual((item["status"], item["code"]), ("PASS", "metrics_received"))
        self.assertEqual(item["evidence"]["selected_sla"], "google")
        self.assertEqual(config["sla"], "google")
        self.assertNotIn("Internet is up", item["message"])

    def test_observed_sla_names_are_bounded_and_do_not_echo_control_text(self):
        points = [
            point(
                "fortigate_virtual_wan_status",
                vdom="Office",
                sla=f"check-{i:02}",
                interface="uplink.100",
            )
            for i in range(70)
        ]
        points.extend(
            point("fortigate_virtual_wan_status", vdom="Office", sla=name, interface="uplink.100")
            for name in ("private-control\ntext", "x" * 129)
        )
        item = self.sla_check({**CONFIG, "sla": ""}, points)
        self.assertEqual(len(item["evidence"]["observed_sla_names"]), 64)
        self.assertNotIn("private-control", json.dumps(item))
        self.assertNotIn("x" * 129, json.dumps(item))

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
