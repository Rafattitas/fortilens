"""Authenticated Unix-socket helper. Only the installed web UID may connect."""

import argparse
import hmac
import http.client
from http.server import BaseHTTPRequestHandler
import json
import os
import re
from pathlib import Path
import socket
import socketserver
import struct
import sqlite3
import sys
from .common import Problem, load, report_failure, setup
from .configuration import Configuration
from .runtime import Operations

MAX_REQUEST = 4 * 1024 * 1024
MAX_RESPONSE = 48 * 1024 * 1024


class Client:
    def __init__(self, path, key_file):
        self.path = str(path)
        self.key_file = Path(key_file)

    def call(self, operation, data=None):
        class Local(http.client.HTTPConnection):
            def connect(inner):
                inner.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                inner.sock.settimeout(45)
                inner.sock.connect(self.path)

        connection = Local("localhost")
        try:
            connection.request(
                "POST",
                "/operation",
                json.dumps({"operation": operation, "data": data or {}}),
                {
                    "Authorization": "Bearer " + self.key_file.read_text().strip(),
                    "Content-Type": "application/json",
                },
            )
            r = connection.getresponse()
            payload = r.read(MAX_RESPONSE + 1)
            if len(payload) > MAX_RESPONSE:
                raise Problem(
                    "helper_response_limit", "The management response exceeded its safe limit.", 503
                )
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("Invalid helper envelope")
            if r.status != 200:
                code = value.get("error", "helper_rejected")
                message = value.get("message", "The helper rejected this operation.")
                if (
                    not 400 <= r.status <= 599
                    or not isinstance(code, str)
                    or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code)
                    or not isinstance(message, str)
                    or len(message) > 1024
                    or any(ord(c) < 32 for c in message)
                ):
                    raise ValueError("Invalid helper rejection")
                raise Problem(code, message, r.status)
            return value
        except (OSError, ValueError, http.client.HTTPException) as exc:
            report_failure("helper_client", exc)
            raise Problem(
                "helper_unavailable",
                "The management helper is unavailable. Sign-in still works; check its system service.",
                503,
            ) from None
        finally:
            try:
                connection.close()
            except (OSError, http.client.HTTPException) as exc:
                report_failure("helper_client_cleanup", exc)


def handler(operations, key, web_uid):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                if hasattr(socket, "SO_PEERCRED"):
                    _, uid, _ = struct.unpack(
                        "3i", self.connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    )
                    if uid not in (0, web_uid):
                        raise Problem("peer_rejected", "Local peer is not authorized.", 403)
                if self.path != "/operation" or not hmac.compare_digest(
                    self.headers.get("Authorization", "").encode("utf-8"),
                    ("Bearer " + key).encode("utf-8"),
                ):
                    raise Problem(
                        "helper_auth_required", "Local management authentication failed.", 403
                    )
                if self.headers.get("Transfer-Encoding"):
                    raise Problem("invalid_body", "Chunked helper requests are not supported.")
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_REQUEST:
                    raise Problem("request_limit", "The operation exceeded its size limit.")
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise Problem(
                        "invalid_body", "The complete operation request was not received."
                    )
                value = json.loads(raw)
                if (
                    not isinstance(value, dict)
                    or set(value) != {"operation", "data"}
                    or not isinstance(value["data"], dict)
                ):
                    raise Problem("invalid_operation", "Choose a supported management operation.")
                result = operations.dispatch(value["operation"], value["data"])
                status = 200
                payload = json.dumps(result).encode()
                if len(payload) > MAX_RESPONSE:
                    raise Problem(
                        "helper_response_limit",
                        "The management response exceeded its safe limit.",
                        503,
                    )
            except Problem as e:
                result = {"error": e.code, "message": e.message}
                status = e.status
            except setup.SetupError as exc:
                report_failure("helper_configuration_invalid", exc)
                # Recognize a fixed local validation cause; never forward raw
                # exception text, which may contain submitted or host values.
                if str(exc) == "A custom CA requires api_tls_verify=true":
                    result = {
                        "error": "tls_ca_conflict",
                        "message": "A custom CA cannot be actively used while TLS verification is disabled. Explicitly remove the custom CA or enable certificate verification.",
                    }
                else:
                    result = {
                        "error": "configuration_invalid",
                        "message": "Configuration validation failed. Review the saved connection, monitoring, retention and certificate settings.",
                    }
                status = 400
            except (ValueError, TypeError, KeyError) as exc:
                report_failure("helper_invalid_operation", exc)
                result = {
                    "error": "operation_failed",
                    "message": "The operation could not be completed safely. Review saved settings and diagnostics.",
                }
                status = 400
            except (OSError, sqlite3.Error) as exc:
                report_failure("helper_storage", exc)
                result = {
                    "error": "operation_unavailable",
                    "message": "A required local resource is unavailable. Review service health, permissions and storage before trying again.",
                }
                status = 503
            except Exception as exc:
                report_failure("helper_unexpected", exc)
                result = {
                    "error": "operation_failed",
                    "message": "The operation could not be completed safely. Review saved settings and diagnostics.",
                }
                status = 500
            if status != 200:
                payload = json.dumps(result).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except OSError as exc:
                report_failure("helper_response_disconnect", exc)

    return Handler


class Server(
    socketserver.ThreadingMixIn, getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)
):
    daemon_threads = True

    def get_request(self):
        sock, address = super().get_request()
        try:
            sock.settimeout(30)
        except OSError:
            try:
                sock.close()
            except OSError as exc:
                report_failure("helper_connection_cleanup", exc)
            raise
        return sock, address

    def handle_error(self, request, client_address):
        # socketserver's default prints exception text and client details.
        report_failure("helper_connection", sys.exc_info()[1])


def _serve():
    if not hasattr(socketserver, "UnixStreamServer") or not hasattr(socket, "SO_PEERCRED"):
        raise SystemExit("The management helper requires Linux Unix sockets and peer credentials.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", required=True, type=Path)
    args = parser.parse_args()
    # The registry is created by install.py, protected from the web UID. Never
    # permit a web-selected root, Compose file or project name on this socket.
    if (
        args.registry.is_symlink()
        or args.registry.stat().st_mode & 0o022
        or args.registry.stat().st_uid != os.geteuid()
    ):
        raise SystemExit("The helper registry must be owner-writable only.")
    config = load(args.registry)
    c = Configuration(
        config["installation"],
        config["control"],
        project=config["project"],
        compose_file=config["compose_file"],
    )
    path = Path(config["socket"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_socket():
            raise SystemExit("Refusing to replace a non-socket runtime file")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(path))
        except ConnectionRefusedError:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            raise Problem(
                "helper_socket_unavailable",
                "The registered helper socket could not be checked safely. Review its ownership and service state.",
                503,
            ) from None
        else:
            raise SystemExit("A helper is already listening")
        finally:
            probe.close()
    operations = Operations(c, dashboard_uid=config.get("dashboard_uid", "forti-monitor"))
    server = Server(
        str(path), handler(operations, Path(config["key"]).read_text().strip(), config["web_uid"])
    )
    try:
        os.chmod(path, 0o660)
        os.chown(path, -1, config["web_gid"])
        server.serve_forever()
    finally:
        try:
            server.server_close()
        except OSError as exc:
            report_failure("helper_server_cleanup", exc)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            report_failure("helper_socket_cleanup", exc)


def main():
    try:
        _serve()
    except Problem as exc:
        raise SystemExit(exc.message) from None
    except Exception as exc:
        report_failure("helper_startup", exc)
        raise SystemExit(
            "The management helper could not start safely. Review its registered configuration, permissions and service health."
        ) from None


if __name__ == "__main__":
    main()
