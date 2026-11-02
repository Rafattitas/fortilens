"""Management failures use synthetic resources and never expose sensitive input."""

import io
import json
import sqlite3
import struct
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from fastapi.testclient import TestClient

    from management import app as web
    from management import auth, common, helper, recovery
except ImportError:
    raise unittest.SkipTest("Management error tests require management/requirements-test.txt.")

SENSITIVE = "synthetic-sensitive-error-detail"
BOOTSTRAP = "synthetic-bootstrap-error-tests-0123456789"
PASSWORD = "Synthetic-error-test-password-123"


class ManagementErrorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def application(self):
        token = self.root / "web/bootstrap.token"
        common.atomic(token, BOOTSTRAP.encode())
        control = Mock()
        control.call.return_value = {}
        app = web.create_app(self.root / "web", token, control, origin="http://testserver")
        self.addCleanup(app.state.auth.close)
        client = TestClient(app)
        self.addCleanup(client.close)
        headers = {"X-FortiLens-Request": "1", "Origin": "http://testserver"}
        created = client.post(
            "/api/bootstrap",
            json={
                "username": "operator",
                "password": PASSWORD,
                "confirmation": PASSWORD,
                "bootstrap_token": BOOTSTRAP,
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 200)
        signed_in = client.post(
            "/api/login", json={"username": "operator", "password": PASSWORD}, headers=headers
        )
        self.assertEqual(signed_in.status_code, 200)
        return app, client, control

    def assert_safe_response(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"], code)
        self.assertNotIn(SENSITIVE, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_unexpected_web_error_has_safe_json_headers_and_redacted_diagnostics(self):
        _, client, control = self.application()
        control.call.side_effect = RuntimeError(SENSITIVE)
        with self.assertLogs("fortilens.management", level="ERROR") as log:
            self.assert_safe_response(client.get("/api/settings"), 500, "internal_error")
        self.assertNotIn(SENSITIVE, " ".join(log.output))
        self.assertIn("event=web_unexpected category=unexpected", log.output[0])
        self.assertEqual(client.get("/health").status_code, 200)

    def test_web_storage_and_io_failures_are_unavailable_not_success(self):
        _, client, control = self.application()
        for exception, code in (
            (sqlite3.OperationalError(SENSITIVE), "storage_unavailable"),
            (PermissionError(SENSITIVE), "service_unavailable"),
        ):
            with self.subTest(code=code):
                control.call.side_effect = exception
                with self.assertLogs("fortilens.management", level="ERROR") as log:
                    self.assert_safe_response(client.get("/api/settings"), 503, code)
                self.assertNotIn(SENSITIVE, " ".join(log.output))

    def test_auth_storage_failure_fails_closed_without_bypassing_session(self):
        app, client, control = self.application()
        with (
            patch.object(app.state.auth, "session", side_effect=sqlite3.DatabaseError(SENSITIVE)),
            self.assertLogs("fortilens.management", level="ERROR"),
        ):
            self.assert_safe_response(client.get("/api/settings"), 503, "storage_unavailable")
        control.call.assert_not_called()

    def test_existing_problem_and_invalid_request_contracts_are_preserved(self):
        _, client, control = self.application()
        control.call.side_effect = common.Problem("fixture_rejection", "Safe explanation.", 409)
        response = client.get("/api/settings")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(), {"error": "fixture_rejection", "message": "Safe explanation."}
        )
        control.call.side_effect = ValueError(SENSITIVE)
        self.assert_safe_response(client.get("/api/settings"), 400, "invalid_request")

    def test_diagnostic_handler_failure_cannot_replace_primary_failure(self):
        with patch.object(common.logging, "getLogger") as logger:
            logger.return_value.error.side_effect = RuntimeError(SENSITIVE)
            common.report_failure("fixed_event", ValueError(SENSITIVE))
        self.assertEqual(
            logger.return_value.error.call_args.args,
            ("management_failure event=%s category=%s", "fixed_event", "invalid_data"),
        )

    def test_auth_initialization_failure_closes_open_database(self):
        database = sqlite3.connect(":memory:")
        with patch.object(auth.sqlite3, "connect", return_value=database):
            with self.assertRaises(common.Problem) as caught:
                auth.Auth(self.root / "state", self.root / "missing-bootstrap")
        self.assertEqual(caught.exception.code, "bootstrap_missing")
        with self.assertRaises(sqlite3.ProgrammingError):
            database.execute("SELECT 1")

    def test_missing_web_assets_fail_before_opening_auth_state(self):
        with patch.object(web, "STATIC", self.root / "missing-assets"):
            with self.assertRaises(RuntimeError):
                web.create_app(self.root / "web", self.root / "bootstrap", Mock())
        self.assertFalse((self.root / "web/auth.sqlite").exists())

    def test_corrupt_password_hash_fails_closed(self):
        for stored in ("", "not-a-password-hash", None, 123, "scrypt$32768$8$1$not-hex"):
            with self.subTest(stored=stored):
                self.assertFalse(auth.verify(PASSWORD, stored))

    def test_post_commit_token_cleanup_failure_does_not_reopen_bootstrap(self):
        token = self.root / "state/bootstrap.token"
        common.atomic(token, BOOTSTRAP.encode())
        account = auth.Auth(token.parent, token)
        self.addCleanup(account.close)
        with (
            patch.object(Path, "unlink", side_effect=PermissionError(SENSITIVE)),
            self.assertLogs("fortilens.management", level="ERROR") as log,
        ):
            account.create("operator", PASSWORD, PASSWORD, BOOTSTRAP)
        self.assertTrue(account.exists())
        self.assertTrue(token.exists())
        self.assertNotIn(SENSITIVE, " ".join(log.output))
        with self.assertRaises(common.Problem) as caught:
            account.create("operator", PASSWORD, PASSWORD, BOOTSTRAP)
        self.assertEqual(caught.exception.code, "bootstrap_closed")

    def helper_client(self, response):
        key = self.root / "helper.key"
        key.write_text("synthetic-helper-key", encoding="utf-8")
        return helper.Client(self.root / "unused.sock", key), response

    def test_helper_client_rejects_malformed_envelopes_and_closes_connection(self):
        for status, value in (
            (200, [SENSITIVE]),
            (200, None),
            (500, {"error": [SENSITIVE], "message": SENSITIVE}),
            (500, {"error": "fixture", "message": [SENSITIVE]}),
            (500, {"error": "fixture", "message": "bad\n" + SENSITIVE}),
            (302, {}),
        ):
            with self.subTest(status=status, value=value):
                response = Mock(status=status)
                response.read.return_value = json.dumps(value).encode()
                client, _ = self.helper_client(response)
                with (
                    patch.object(helper.http.client.HTTPConnection, "request"),
                    patch.object(
                        helper.http.client.HTTPConnection, "getresponse", return_value=response
                    ),
                    patch.object(helper.http.client.HTTPConnection, "close") as close,
                    self.assertLogs("fortilens.management", level="ERROR") as log,
                    self.assertRaises(common.Problem) as caught,
                ):
                    client.call("view")
                self.assertEqual(caught.exception.code, "helper_unavailable")
                self.assertEqual(caught.exception.status, 503)
                self.assertNotIn(SENSITIVE, caught.exception.message + " ".join(log.output))
                close.assert_called_once()

    def test_helper_client_keeps_valid_rejection_and_cleanup_cannot_mask_it(self):
        response = Mock(status=409)
        response.read.return_value = b'{"error":"revision_conflict","message":"Reload first."}'
        client, _ = self.helper_client(response)
        with (
            patch.object(helper.http.client.HTTPConnection, "request"),
            patch.object(helper.http.client.HTTPConnection, "getresponse", return_value=response),
            patch.object(
                helper.http.client.HTTPConnection, "close", side_effect=OSError(SENSITIVE)
            ),
            self.assertLogs("fortilens.management", level="ERROR"),
            self.assertRaises(common.Problem) as caught,
        ):
            client.call("view")
        self.assertEqual(caught.exception.code, "revision_conflict")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(caught.exception.message, "Reload first.")

    def helper_handler(self, operations, raw=None):
        raw = raw or b'{"operation":"view","data":{}}'
        handler = helper.handler(operations, "synthetic-key", 42).__new__(
            helper.handler(operations, "synthetic-key", 42)
        )
        handler.path = "/operation"
        handler.headers = Message()
        handler.headers["Authorization"] = "Bearer synthetic-key"
        handler.headers["Content-Length"] = str(len(raw))
        handler.connection = Mock()
        handler.connection.getsockopt.return_value = struct.pack("3i", 1, 42, 42)
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        return handler

    def test_helper_dispatch_failures_have_safe_classification(self):
        for exception, status in (
            (ValueError(SENSITIVE), 400),
            (sqlite3.OperationalError(SENSITIVE), 503),
            (OSError(SENSITIVE), 503),
            (RuntimeError(SENSITIVE), 500),
        ):
            with self.subTest(status=status, exception=type(exception)):
                operations = Mock()
                operations.dispatch.side_effect = exception
                handler = self.helper_handler(operations)
                with self.assertLogs("fortilens.management", level="ERROR") as log:
                    handler.do_POST()
                handler.send_response.assert_called_once_with(status)
                self.assertNotIn(SENSITIVE.encode(), handler.wfile.getvalue())
                self.assertNotIn(SENSITIVE, " ".join(log.output))

    def test_helper_rejects_incomplete_body_before_dispatch(self):
        operations = Mock()
        handler = self.helper_handler(operations)
        handler.headers.replace_header("Content-Length", str(len(handler.rfile.getvalue()) + 1))
        handler.do_POST()
        operations.dispatch.assert_not_called()
        handler.send_response.assert_called_once_with(400)
        self.assertEqual(json.loads(handler.wfile.getvalue())["error"], "invalid_body")

    def test_helper_non_ascii_authentication_fails_closed(self):
        operations = Mock()
        handler = self.helper_handler(operations)
        handler.headers.replace_header("Authorization", "Bearer \u00ff")
        handler.do_POST()
        operations.dispatch.assert_not_called()
        handler.send_response.assert_called_once_with(403)
        self.assertEqual(json.loads(handler.wfile.getvalue())["error"], "helper_auth_required")

    def test_helper_caps_output_and_handles_disconnected_reader(self):
        operations = Mock()
        operations.dispatch.return_value = {"large": "x" * 1024}
        handler = self.helper_handler(operations)
        with patch.object(helper, "MAX_RESPONSE", 256):
            handler.do_POST()
        handler.send_response.assert_called_once_with(503)
        self.assertEqual(json.loads(handler.wfile.getvalue())["error"], "helper_response_limit")
        operations.dispatch.return_value = {}
        handler = self.helper_handler(operations)
        handler.wfile = Mock()
        handler.wfile.write.side_effect = BrokenPipeError(SENSITIVE)
        with self.assertLogs("fortilens.management", level="ERROR") as log:
            handler.do_POST()
        self.assertNotIn(SENSITIVE, " ".join(log.output))

    def test_helper_listener_closes_accepted_socket_when_timeout_setup_fails(self):
        server = helper.Server.__new__(helper.Server)
        connection = Mock()
        connection.settimeout.side_effect = OSError(SENSITIVE)
        with patch.object(
            helper.socketserver.TCPServer, "get_request", return_value=(connection, None)
        ):
            with self.assertRaises(OSError):
                server.get_request()
        connection.close.assert_called_once()

    def test_helper_server_fallback_diagnostics_omit_exception_and_peer(self):
        server = helper.Server.__new__(helper.Server)
        with (
            patch.object(
                helper.sys, "exc_info", return_value=(RuntimeError, RuntimeError(SENSITIVE), None)
            ),
            self.assertLogs("fortilens.management", level="ERROR") as log,
        ):
            server.handle_error(None, (SENSITIVE, 1234))
        self.assertNotIn(SENSITIVE, " ".join(log.output))
        self.assertIn("event=helper_connection category=unexpected", log.output[0])

    def test_recovery_input_failure_is_safe_and_closes_auth(self):
        (self.root / "auth.sqlite").touch()
        account = Mock()
        with (
            patch.object(sys, "argv", ["recovery", "--data", str(self.root)]),
            patch.object(recovery, "Auth", return_value=account),
            patch.object(recovery.getpass, "getpass", side_effect=EOFError(SENSITIVE)),
            self.assertRaises(SystemExit) as caught,
        ):
            recovery.main()
        self.assertNotIn(SENSITIVE, str(caught.exception))
        self.assertIn("input ended", str(caught.exception))
        account.recover.assert_not_called()
        account.close.assert_called_once()

    def test_bootstrap_rotation_closes_probe_and_auth_connections(self):
        token = self.root / "bootstrap.token"
        common.atomic(token, BOOTSTRAP.encode())
        account = auth.Auth(self.root, token)
        account.close()
        token.unlink()
        original_connect = sqlite3.connect
        opened = []

        def connect(*args, **kwargs):
            database = original_connect(*args, **kwargs)
            opened.append(database)
            return database

        with (
            patch.object(sys, "argv", ["recovery", "--data", str(self.root), "--rotate-bootstrap"]),
            patch.object(recovery.sqlite3, "connect", side_effect=connect),
            patch.object(sys, "stdout", io.StringIO()),
        ):
            recovery.main()
        self.assertEqual(len(opened), 2)
        self.assertTrue(token.is_file())
        for database in opened:
            with self.assertRaises(sqlite3.ProgrammingError):
                database.execute("SELECT 1")

    def test_recovery_storage_failure_never_exposes_details_or_retries(self):
        (self.root / "auth.sqlite").touch()
        account = Mock()
        account.recover.side_effect = sqlite3.OperationalError(SENSITIVE)
        with (
            patch.object(sys, "argv", ["recovery", "--data", str(self.root)]),
            patch.object(recovery, "Auth", return_value=account),
            patch.object(recovery.getpass, "getpass", side_effect=[PASSWORD, PASSWORD]),
            self.assertLogs("fortilens.management", level="ERROR") as log,
            self.assertRaises(SystemExit) as caught,
        ):
            recovery.main()
        self.assertNotIn(SENSITIVE, str(caught.exception) + " ".join(log.output))
        account.recover.assert_called_once_with(PASSWORD)
        account.close.assert_called_once()

    def test_startup_failures_use_safe_cli_messages(self):
        for module, operation in ((web, "_run"), (helper, "_serve")):
            with (
                self.subTest(module=module.__name__),
                patch.object(module, operation, side_effect=RuntimeError(SENSITIVE)),
                self.assertLogs("fortilens.management", level="ERROR") as log,
                self.assertRaises(SystemExit) as caught,
            ):
                module.main()
            self.assertNotIn(SENSITIVE, str(caught.exception) + " ".join(log.output))
            self.assertIn("could not start safely", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
