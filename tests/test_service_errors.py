"""Synthetic transport/storage failures remain unavailable, private and bounded."""

import base64
import contextlib
import http.client
import io
import json
import sqlite3
import ssl
import sys
import tempfile
import threading
import unittest
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting
import doctor
import interfaces
import portal
import usage_service as usage
from test_accounting import CONFIG, INVENTORY
from test_doctor import CONFIG as DOCTOR_CONFIG
from test_doctor import NOW, FakeRequest, docker_result

SECRET = "synthetic-transport-secret-must-not-escape"
PASSWORD = "synthetic-report-password"


class OneIteration:
    def __init__(self):
        self.waits = 0

    def is_set(self):
        return self.waits > 0

    def wait(self, seconds):
        self.waits += 1


class ServiceErrorTests(unittest.TestCase):
    def test_broken_service_log_handler_cannot_mask_the_original_failure(self):
        with patch.object(usage.LOGGER, "warning", side_effect=OSError(SECRET)):
            usage.log_failure("accounting_poll_failed", "accounting_storage_unavailable")
        with patch.object(portal.LOGGER, "error", side_effect=OSError(SECRET)):
            portal.log_failure("portal_request_failed")

    def test_store_constructor_closes_connection_and_preserves_primary_failure(self):
        for stage in ("schema", "contract"):
            with self.subTest(stage=stage):
                connection = MagicMock()
                failure = sqlite3.OperationalError(SECRET)
                connection.close.side_effect = sqlite3.OperationalError("secondary-close")
                if stage == "schema":
                    connection.executescript.side_effect = failure
                    state = contextlib.nullcontext()
                else:
                    state = patch.object(accounting.Store, "state", side_effect=failure)
                with (
                    patch.object(accounting.sqlite3, "connect", return_value=connection),
                    patch.object(accounting, "detect_storage_format", return_value="legacy"),
                    patch.object(
                        accounting, "open_ledger", return_value=MagicMock(format="legacy")
                    ),
                    state,
                ):
                    with self.assertRaises(sqlite3.OperationalError) as caught:
                        accounting.Store("synthetic.db", CONFIG)
                self.assertIs(caught.exception, failure)
                connection.close.assert_called_once()

    def test_invalid_overflowing_wan_value_remains_unknown(self):
        import wan_health

        self.assertIsNone(wan_health.finite(10**4000))
        self.assertEqual(wan_health.finite(12.5), 12.5)

    def server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(close)
        return server

    def request(self, server, path, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def engine(self):
        store = accounting.Store(":memory:", CONFIG, INVENTORY)
        self.addCleanup(store.close)
        return usage.Engine(store)

    def response(self, body, status=200):
        response = Mock(status=status)
        response.read.return_value = body
        response.getheaders.return_value = []
        return response

    def test_json_transport_rejects_nonobjects_and_private_malformed_payloads(self):
        for payload in (b"[]", b"null", SECRET.encode(), b"\xff"):
            with self.subTest(payload=payload):
                opener = MagicMock()
                response = opener.open.return_value.__enter__.return_value
                response.read.return_value = payload
                with patch.object(usage.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(usage.ServiceError) as caught:
                        usage.fetch_json("http://fixture.invalid", "/query", budget=32)
                self.assertEqual(
                    str(caught.exception),
                    "invalid_response" if len(payload) <= 32 else "response_budget_exceeded",
                )
                self.assertNotIn(SECRET, usage.safe_error(caught.exception))
                response.read.assert_called_once_with(33)
                opener.open.return_value.__exit__.assert_called_once()

    def test_malformed_loki_pages_fail_before_checkpoint_advancement(self):
        engine = self.engine()
        with engine.store.db:
            engine.store.set_state("checkpoint_ns", 100)
        malformed = [
            None,
            {},
            {"data": None},
            {"data": {"resultType": "streams", "result": [{"values": [[SECRET, "line"]]}]}},
            {"data": {"resultType": "streams", "result": [{"values": [[101, None]]}]}},
        ]
        for payload in malformed:
            with self.subTest(payload=payload):
                engine.loki.fetch = lambda *a, **kw: payload
                with self.assertRaises(usage.ServiceError) as caught:
                    engine.poll(1000)
                self.assertEqual(usage.safe_error(caught.exception), "unexpected_loki_response")
                self.assertEqual(engine.store.state("checkpoint_ns"), 100)

    def test_error_codes_never_copy_arbitrary_exception_messages(self):
        cases = [
            (usage.ServiceError(SECRET), "upstream_unavailable"),
            (
                urllib.error.HTTPError("http://fixture.invalid", SECRET, SECRET, {}, None),
                "upstream_unavailable",
            ),
            (sqlite3.OperationalError(SECRET), "accounting_storage_unavailable"),
            (urllib.error.URLError(TimeoutError(SECRET)), "timeout"),
            (ssl.SSLCertVerificationError(SECRET), "tls_verification_failed"),
            (json.JSONDecodeError(SECRET, SECRET, 0), "invalid_response"),
        ]
        for exc, expected in cases:
            with self.subTest(exception=type(exc).__name__):
                self.assertEqual(usage.safe_error(exc), expected)
                self.assertNotIn(SECRET, usage.safe_error(exc))

    def test_worker_survives_database_failure_while_recording_failure(self):
        store = Mock(config=CONFIG, inventory={})
        store.db = MagicMock()
        store.state.side_effect = sqlite3.OperationalError(SECRET)
        store.set_state.side_effect = sqlite3.OperationalError(SECRET)
        engine = usage.Engine(store)
        engine.stop = OneIteration()
        with self.assertLogs(usage.LOGGER, level="WARNING") as logs:
            engine.run()
        self.assertEqual(engine.stop.waits, 1)
        self.assertFalse(engine.last_poll_ok)
        self.assertEqual(engine.last_error, "accounting_storage_unavailable")
        self.assertIsNotNone(engine.last_attempt)
        self.assertNotIn(SECRET, "\n".join(logs.output))
        self.assertIn("accounting_failure_state_unavailable", "\n".join(logs.output))

    def test_public_template_and_authenticated_storage_errors_are_safe_http_errors(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as root:
            server = self.server(usage.handler(engine, PASSWORD, root))
            with self.assertLogs(usage.LOGGER, level="WARNING"):
                status, body = self.request(server, "/")
            self.assertEqual(status, 503)
            self.assertNotIn(root.encode(), body)
            self.assertEqual(self.request(server, "/health")[0], 200)
            auth = "Basic " + base64.b64encode(("admin:" + PASSWORD).encode()).decode()
            with patch.object(engine, "report", side_effect=sqlite3.OperationalError(SECRET)):
                with self.assertLogs(usage.LOGGER, level="WARNING") as logs:
                    status, body = self.request(server, "/api/report", {"Authorization": auth})
            self.assertEqual(status, 503)
            self.assertEqual(json.loads(body)["error"], "accounting_storage_unavailable")
            self.assertNotIn(SECRET.encode(), body)
            self.assertNotIn(SECRET, "\n".join(logs.output))

    def test_non_ascii_reporting_authorization_is_rejected_without_exception(self):
        engine = self.engine()
        with tempfile.TemporaryDirectory() as root:
            server = self.server(usage.handler(engine, PASSWORD, root))
            status, body = self.request(server, "/api/report", {"Authorization": "Basic caf\xe9"})
        self.assertEqual(status, 401)
        self.assertEqual(json.loads(body)["error"], "authentication_required")

    def test_usage_client_disconnect_does_not_write_second_response(self):
        handler = usage.handler(self.engine(), PASSWORD, ".")
        request = handler.__new__(handler)
        request.path = "/health"
        request.send_response = Mock()
        request.send_header = Mock()
        request.end_headers = Mock()
        request.wfile = Mock()
        request.wfile.write.side_effect = BrokenPipeError(SECRET)
        request.do_GET()
        request.send_response.assert_called_once_with(200)
        self.assertTrue(request.close_connection)

    def test_usage_bind_failure_closes_store_before_any_worker_starts(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "site.json").write_text(json.dumps(CONFIG))
            (root / "password").write_text(PASSWORD)
            store = Mock()
            store.state.return_value = {}
            stderr = io.StringIO()
            with (
                patch.object(usage.accounting, "Store", return_value=store),
                patch.object(usage, "ThreadingHTTPServer", side_effect=OSError(SECRET)),
                patch.object(usage.threading, "Thread") as worker,
                contextlib.redirect_stderr(stderr),
            ):
                with self.assertRaises(SystemExit) as caught:
                    usage.main(
                        [
                            "--config",
                            str(root / "site.json"),
                            "--database",
                            str(root / "usage.sqlite"),
                            "--password-file",
                            str(root / "password"),
                        ]
                    )
            self.assertEqual(caught.exception.code, 1)
            self.assertNotIn(SECRET, stderr.getvalue())
            worker.assert_not_called()
            store.close.assert_called_once()

    def test_usage_shutdown_does_not_close_a_database_still_used_by_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "site.json").write_text(json.dumps(CONFIG))
            (root / "password").write_text(PASSWORD)
            store = Mock()
            store.state.return_value = {}
            server = Mock()
            server.serve_forever.side_effect = KeyboardInterrupt()
            worker = Mock()
            worker.is_alive.return_value = True
            with (
                patch.object(usage.accounting, "Store", return_value=store),
                patch.object(usage, "ThreadingHTTPServer", return_value=server),
                patch.object(usage.threading, "Thread", return_value=worker),
                self.assertLogs(usage.LOGGER, level="ERROR") as logs,
            ):
                result = usage.main(
                    [
                        "--config",
                        str(root / "site.json"),
                        "--database",
                        str(root / "usage.sqlite"),
                        "--password-file",
                        str(root / "password"),
                    ]
                )
            self.assertEqual(result, 1)
            worker.join.assert_called_once_with(timeout=30)
            store.close.assert_not_called()
            server.server_close.assert_called_once()
            self.assertIn("accounting_worker_shutdown_timed_out", "\n".join(logs.output))

    def test_portal_rejects_incomplete_or_ambiguous_request_body(self):
        handler = portal.handler(portal.Portal(PASSWORD, "."))
        request = handler.__new__(handler)
        request.headers = http.client.HTTPMessage()
        request.headers["Content-Length"] = "5"
        request.rfile = io.BytesIO(b"{}")
        with self.assertRaises(ValueError):
            request.body(4096)
        request.headers["Content-Length"] = "2"
        with self.assertRaises(ValueError):
            request.body(4096)

    def test_partial_gateway_stream_failure_closes_without_second_response(self):
        gateway = portal.Portal(PASSWORD, ".")
        handler = portal.handler(gateway)
        request = handler.__new__(handler)
        request.command = "GET"
        request.path = "/api/report"
        request.headers = {"Authorization": gateway.report_auth}
        request.send_response = Mock()
        request.send_header = Mock()
        request.end_headers = Mock()
        request.wfile = Mock()
        response = Mock(status=200)
        response.getheader.side_effect = lambda key: (
            "application/json" if key == "Content-Type" else None
        )
        response.read.side_effect = http.client.IncompleteRead(SECRET.encode(), len(SECRET))
        connection = Mock()
        connection.getresponse.return_value = response
        with patch.object(gateway, "connect", return_value=connection):
            request.do_GET()
        request.send_response.assert_called_once_with(200)
        self.assertTrue(request.close_connection)
        connection.close.assert_called_once()

    def test_portal_invalid_viewer_payload_fails_closed_and_closes_upstream(self):
        gateway = portal.Portal(PASSWORD, ".")
        connection = Mock()
        connection.getresponse.side_effect = [
            self.response(b""),
            self.response(b"[]"),
            self.response(b"[1]"),
        ]
        closed = threading.Event()
        connection.close.side_effect = closed.set
        gateway.connect = Mock(return_value=connection)
        server = self.server(portal.handler(gateway))
        cookie = portal.COOKIE + "=" + gateway.issue()
        status, body = self.request(server, "/api/monitoring-status", {"Cookie": cookie})
        self.assertEqual(status, 503)
        self.assertEqual(json.loads(body)["error"], "upstream_unavailable")
        self.assertTrue(closed.wait(5), "Upstream cleanup did not finish")
        connection.close.assert_called_once()

    def test_portal_redacts_unknown_upstream_failure_but_preserves_safe_report_contract(self):
        gateway = portal.Portal(PASSWORD, ".")
        server = self.server(portal.handler(gateway))
        headers = {"Authorization": gateway.report_auth}
        for payload, expected in (
            (SECRET.encode(), {"error": "upstream_unavailable"}),
            (
                json.dumps(
                    {"error": "accounting_storage_limit", "message": portal.REPORT_FAILURE_MESSAGE}
                ).encode(),
                {"error": "accounting_storage_limit", "message": portal.REPORT_FAILURE_MESSAGE},
            ),
            (
                json.dumps({"error": "accounting_storage_limit", "message": SECRET}).encode(),
                {"error": "upstream_unavailable"},
            ),
        ):
            with self.subTest(payload=payload):
                connection = Mock()
                closed = threading.Event()
                connection.close.side_effect = closed.set
                connection.getresponse.return_value = self.response(payload, 503)
                with patch.object(gateway, "connect", return_value=connection):
                    status, body = self.request(server, "/api/report", headers)
                self.assertEqual(status, 503)
                self.assertEqual(json.loads(body), expected)
                self.assertNotIn(SECRET.encode(), body)
                # Reading a complete response can precede the server thread's
                # finally block. Synchronize cleanup rather than race it.
                self.assertTrue(closed.wait(5), "Upstream cleanup did not finish")
                connection.close.assert_called_once()

    def test_portal_unexpected_handler_exception_is_sanitized(self):
        gateway = portal.Portal(PASSWORD, ".")
        server = self.server(portal.handler(gateway))
        with patch.object(gateway, "authenticated", side_effect=RuntimeError(SECRET)):
            with self.assertLogs(portal.LOGGER, level="ERROR") as logs:
                status, body = self.request(server, "/api/session")
        self.assertEqual(status, 503)
        self.assertNotIn(SECRET.encode(), body)
        self.assertNotIn(SECRET, "\n".join(logs.output))

    def test_portal_startup_failure_is_sanitized(self):
        stderr = io.StringIO()
        with (
            patch.object(Path, "read_text", side_effect=OSError(SECRET)),
            contextlib.redirect_stderr(stderr),
        ):
            with self.assertRaises(SystemExit) as caught:
                portal.main(["--password-file", "private-password"])
        self.assertEqual(caught.exception.code, 1)
        self.assertNotIn(SECRET, stderr.getvalue())

    def test_inventory_shape_and_custom_transport_errors_do_not_escape(self):
        for data in (
            None,
            [],
            {"resultType": "matrix", "result": [None]},
            {
                "resultType": "matrix",
                "result": [{"metric": {"name": "port1"}, "values": [[SECRET, "1"]]}],
            },
        ):
            with self.subTest(data=data), self.assertRaises(interfaces.InventoryError) as caught:
                interfaces.from_metrics({"status": "success", "data": data})
            self.assertEqual(caught.exception.code, "metrics_invalid")
        config = {"fortigate_host": "fixture.invalid", "vdom": "root"}
        with patch.object(
            interfaces, "cmdb_get", side_effect=interfaces.InventoryError("api_http_403", SECRET)
        ):
            report = interfaces.collect(config, "synthetic-token")
        self.assertNotIn(SECRET, json.dumps(report))
        self.assertIn("api_http_403", report["notes"][-1])
        self.assertEqual(report["interfaces"], [])

    def test_doctor_malformed_nested_payloads_are_unknown_not_outages(self):
        delegate = FakeRequest()

        def request(p, config, route, *args, **kwargs):
            if route.startswith("/api/datasources/proxy/"):
                return {"status": "success", "data": None}
            if route == "/api/ds/query":
                return {"results": None}
            return delegate(p, config, route, *args, **kwargs)

        with patch.object(doctor.subprocess, "run", return_value=docker_result()):
            result = doctor.run(
                Path("."),
                DOCTOR_CONFIG,
                request,
                lambda *args: ["docker"],
                "fm-prometheus",
                "fm-loki",
                now=NOW,
            )
        by_id = {row["id"]: row for row in result["checks"]}
        self.assertEqual(by_id["sla_metrics"]["status"], "UNKNOWN")
        self.assertEqual(by_id["recent_logs"]["status"], "UNKNOWN")
        self.assertNotIn(SECRET, json.dumps(result))

    def test_invalid_docker_names_do_not_prove_a_service_stopped(self):
        output = Mock(returncode=0, stdout=json.dumps([{"Name": None, "Service": "grafana"}]))
        with patch.object(doctor.subprocess, "run", return_value=output):
            checks = doctor.service_checks(Path("."), lambda *args: ["docker"])
        self.assertTrue(checks)
        self.assertTrue(all(row["status"] == "UNKNOWN" for row in checks))

    def test_unavailable_service_reader_does_not_prevent_other_diagnostics(self):
        def unavailable(*args):
            raise OSError(SECRET)

        result = doctor.run(
            Path("."),
            DOCTOR_CONFIG,
            FakeRequest(),
            lambda *args: ["docker"],
            "fm-prometheus",
            "fm-loki",
            now=NOW,
            service_reader=unavailable,
        )
        by_id = {row["id"]: row for row in result["checks"]}
        self.assertEqual(by_id["service:grafana"]["status"], "UNKNOWN")
        self.assertEqual(by_id["grafana"]["status"], "PASS")
        self.assertNotIn(SECRET, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
