"""Readiness must distinguish missing data, probe failures and auth boundaries."""
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("doctor", ROOT / "portable/doctor.py")
doctor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(doctor)
NOW = 1800000000
CONFIG = {"fortigate_host": "fw.example.test", "api_port": 8443, "vdom": "Office",
          "sla": "ISP_Health", "wan_interfaces": ["uplink.100"], "api_ca_file": "", "api_tls_verify": True}


def point(metric, value=1, age=10, **labels):
    return {"metric": {"__name__": metric, "job": "fortigate", "instance": "fw.example.test", **labels},
            "values": [[NOW - age, str(value)]]}


def healthy_points():
    return [point("up"), point("probe_success"),
            point("fortigate_virtual_wan_status", 0, vdom="Office", sla="ISP_Health", interface="uplink.100", state="up"),
            point("fortigate_interface_link_up", 0, name="uplink.100")]


class FakeRequest:
    def __init__(self, points=None, logs=1):
        self.points = healthy_points() if points is None else points
        self.logs = logs
        self.calls = []

    def __call__(self, p, c, route, method="GET", body=None, timeout=5):
        self.calls.append((route, method, body))
        if route == "/api/health":
            return {"database": "ok"}
        if route.endswith("/health"):
            return {"status": "OK"}
        if route.startswith("/api/datasources/proxy/"):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(route).query)["query"][0]
            assert '[600s]' in query and 'instance="fw.example.test"' in query
            return {"status": "success", "data": {"resultType": "matrix", "result": self.points}}
        if route == "/api/ds/query":
            assert method == "POST" and "count_over_time" in body["queries"][0]["expr"]
            frames = [] if self.logs is None else [{"schema": {"fields": [{"type": "time"}, {"type": "number"}]},
                      "data": {"values": [[NOW * 1000], [self.logs]]}}]
            return {"results": {"A": {"status": 200, "frames": frames}}}
        raise AssertionError("Unexpected or mutating request: " + route)


def docker_result():
    rows = [{"Name": "test-" + name + "-1", "Service": name, "State": "running", "Health": ""} for name in doctor.SERVICES]
    return subprocess.CompletedProcess([], 0, "\n".join(json.dumps(x) for x in rows), "")


class DoctorTests(unittest.TestCase):
    def run_report(self, request=None, **kwargs):
        with patch.object(doctor.subprocess, "run", return_value=docker_result()) as run, \
             patch.object(doctor, "api_check") as api:
            r = doctor.run(Path("/test"), CONFIG, request or FakeRequest(), lambda p, *a: ["docker", "compose", *a],
                           "fm-prometheus", "fm-loki", now=NOW, **kwargs)
            self.assertEqual(run.call_args.args[0], ["docker", "compose", "ps", "--all", "--format", "json"])
            if not kwargs.get("direct_api"):
                api.assert_not_called()
            return r

    def item(self, report, name):
        return next(x for x in report["checks"] if x["id"] == name)

    def test_received_zero_wan_state_is_data_not_collection_failure(self):
        fake = FakeRequest()
        r = self.run_report(fake)
        self.assertEqual(r["status"], "PASS")
        self.assertTrue(r["read_only"])
        self.assertFalse(r["direct_api_requested"])
        for route, method, _ in fake.calls:
            self.assertTrue(method == "GET" or route == "/api/ds/query")

    def test_missing_data_and_quiet_logs_remain_unknown(self):
        r = self.run_report(FakeRequest([], None))
        self.assertEqual(r["status"], "UNKNOWN")
        self.assertEqual(self.item(r, "fortigate_api")["status"], "UNKNOWN")
        self.assertEqual(self.item(r, "recent_logs")["code"], "logs_not_observed")
        self.assertFalse(any(x["status"] == "FAIL" for x in r["checks"]))

    def test_old_samples_do_not_pass_using_query_evaluation_time(self):
        points = healthy_points()
        for row in points:
            row["values"][0][0] = NOW - 450
        r = self.run_report(FakeRequest(points))
        for name in ["fortigate_scrape", "fortigate_api", "sla_metrics", "interface_metrics"]:
            self.assertEqual(self.item(r, name)["code"], "metrics_stale")

    def test_probe_failure_is_not_misdiagnosed_as_bad_token(self):
        points = healthy_points()
        points[1]["values"][0][1] = "0"
        r = self.run_report(FakeRequest(points))
        self.assertEqual(r["status"], "FAIL")
        self.assertEqual(self.item(r, "fortigate_scrape")["status"], "PASS")
        self.assertEqual(self.item(r, "fortigate_api")["code"], "probe_failed")
        self.assertNotIn("api_auth_rejected", json.dumps(r))

    def test_probe_success_before_failed_scrape_does_not_claim_api_ready(self):
        points = healthy_points()
        points[0]["values"][0][1] = "0"
        r = self.run_report(FakeRequest(points))
        self.assertEqual(self.item(r, "fortigate_api")["code"], "probe_not_current")

    def test_wrong_vdom_or_partial_interfaces_do_not_mean_wan_down(self):
        points = healthy_points()
        points[2]["metric"]["vdom"] = "Other"
        points[3]["metric"]["name"] = "differentPort"
        r = self.run_report(FakeRequest(points))
        self.assertEqual(self.item(r, "sla_metrics")["status"], "UNKNOWN")
        self.assertEqual(self.item(r, "interface_metrics")["status"], "WARN")

    def test_future_timestamps_are_not_fresh_evidence(self):
        points = healthy_points()
        points[1]["values"][0][0] = NOW + 600
        r = self.run_report(FakeRequest(points))
        self.assertEqual(self.item(r, "fortigate_api")["code"], "clock_skew")

    def test_grafana_auth_does_not_become_fortigate_auth_and_hides_body(self):
        def rejected(*args, **kwargs):
            raise urllib.error.HTTPError("http://grafana/", 401, "SECRET_IN_REASON", {}, io.BytesIO(b"SECRET_IN_BODY"))
        r = self.run_report(rejected)
        self.assertEqual(self.item(r, "grafana")["code"], "grafana_auth_rejected")
        self.assertEqual(self.item(r, "fortigate_api")["status"], "UNKNOWN")
        self.assertNotIn("SECRET", json.dumps(r))

    def test_docker_not_accessible_does_not_claim_services_stopped(self):
        with patch.object(doctor.subprocess, "run", side_effect=FileNotFoundError("SECRET")):
            checks = doctor.service_checks(Path("/test"), lambda p, *a: ["docker", *a])
        self.assertTrue(all(x["status"] == "UNKNOWN" for x in checks))
        self.assertNotIn("SECRET", json.dumps(checks))

    def test_stopped_and_missing_services_are_reported(self):
        result = docker_result()
        rows = [json.loads(x) for x in result.stdout.splitlines()]
        rows[0]["State"] = "restarting"
        rows = rows[:-1]
        result.stdout = json.dumps(rows)
        with patch.object(doctor.subprocess, "run", return_value=result):
            checks = doctor.service_checks(Path("/test"), lambda p, *a: ["docker", *a])
        self.assertEqual(checks[0]["code"], "service_not_ready")
        self.assertEqual(checks[-1]["code"], "service_missing")

    def test_direct_api_status_and_network_errors_are_distinguished(self):
        with tempfile.TemporaryDirectory() as parent:
            p = Path(parent); (p / "secrets").mkdir()
            token = "PRIVATE_TEST_TOKEN"
            (p / "secrets/fortigate-key.yaml").write_text(json.dumps({"https://fw.example.test:8443": {"token": token}}))
            errors = [(urllib.error.HTTPError("https://fw/", 401, token, {}, None), "api_auth_rejected"),
                      (urllib.error.HTTPError("https://fw/", 403, token, {}, None), "api_access_forbidden"),
                      (urllib.error.HTTPError("https://fw/", 302, token, {}, None), "api_redirect_rejected"),
                      (urllib.error.URLError(ssl.SSLCertVerificationError(token)), "api_tls_error"),
                      (urllib.error.URLError(socket.gaierror(token)), "api_dns_error"),
                      (urllib.error.URLError(TimeoutError(token)), "api_timeout")]
            for error, expected in errors:
                with self.subTest(expected=expected), patch.object(doctor.urllib.request, "build_opener") as build:
                    build.return_value.open.side_effect = error
                    item = doctor.api_check(p, CONFIG)
                    self.assertEqual(item["code"], expected)
                    self.assertNotIn(token, json.dumps(item))
                    req = build.return_value.open.call_args.args[0]
                    self.assertNotIn(token, req.full_url)
                    self.assertEqual(req.get_header("Authorization"), "Bearer " + token)
                    self.assertTrue(any(isinstance(x, doctor.NoRedirect) for x in build.call_args.args))

    def test_exit_codes_and_json_output(self):
        r = self.run_report()
        for status, code in [("PASS", 0), ("FAIL", 1), ("WARN", 2), ("UNKNOWN", 2)]:
            r["status"] = status
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(doctor.print_report(r, as_json=True), code)
            self.assertEqual(json.loads(out.getvalue())["status"], status)

    def test_human_readiness_report_is_in_english(self):
        report = self.run_report()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(doctor.print_report(report), 0)
        self.assertIn("FortiLens readiness check", out.getvalue())
        self.assertIn("[Ready]", out.getvalue())
        self.assertIn("No direct API request was sent.", out.getvalue())
        self.assertNotRegex(out.getvalue(), r"[\u0600-\u06ff]")


if __name__ == "__main__":
    unittest.main()
