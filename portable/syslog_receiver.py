"""Bounded native FortiGate UDP adapter for the existing Alloy receiver.

This explicit profile accepts ``<PRI>date=YYYY-MM-DD time=HH:MM:SS ...``.
Only a canonical priority in 0..191 is removed. The remaining UTF-8 body is
preserved, including eventtime and byte counters. Trailing CR/LF/NUL delimiters
are removed; embedded control characters, other formats and oversize messages
are rejected. Receipt-time RFC5424 envelopes use octet-counted TCP framing.

The source filter is an IPv4 allowlist resolved from fortigate_host at startup,
not sender authentication. UDP and the bounded memory queue are best effort.
A completed TCP send is not evidence of Loki storage or accounting eligibility.
No raw messages, secrets or disk queues are written by this process.
"""

import argparse
import ipaddress
import json
import math
import os
import queue
import re
import select
import signal
import socket
import sys
import threading
import time
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

UDP_ADDRESS = ("0.0.0.0", 1514)
HTTP_ADDRESS = ("0.0.0.0", 8080)
UPSTREAM = ("alloy", 1514)
MAX_DATAGRAM = 64000
MAX_MESSAGE = 65536
QUEUE_CAPACITY = 256
CONNECT_TIMEOUT = 2.0
INITIAL_BACKOFF = 0.25
MAX_BACKOFF = 5.0
SHUTDOWN_TIMEOUT = 4.0
REASONS = ("source", "oversize", "utf8", "control", "format", "priority", "timestamp")
_PRIORITY = re.compile(rb"^<(0|[1-9][0-9]{0,2})>")
_NATIVE = re.compile(
    rb"^date=([0-9]{4})-([0-9]{2})-([0-9]{2}) "
    rb"time=(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?: |$)"
)


class ConfigurationError(ValueError):
    """A bounded startup failure whose message contains no configuration data."""


class RejectedDatagram(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _permitted_address(value):
    address = ipaddress.ip_address(value)
    if (
        address.version != 4
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or str(address) == "255.255.255.255"
    ):
        raise ConfigurationError("Invalid source address")
    return str(address)


def resolve_sources(config, resolver=None):
    """Resolve only the configured FortiGate host; never broaden to a subnet."""
    if not isinstance(config, dict):
        raise ConfigurationError("Invalid configuration")
    host = config.get("fortigate_host")
    if not isinstance(host, str) or not host or len(host) > 253:
        raise ConfigurationError("Invalid source host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        labels = host.rstrip(".").split(".")
        if any(
            not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in labels
        ):
            raise ConfigurationError("Invalid source host") from None
    else:
        return frozenset({_permitted_address(host)})
    resolver = resolver or socket.getaddrinfo
    try:
        rows = resolver(host, 1514, family=socket.AF_INET, type=socket.SOCK_DGRAM)
        addresses = frozenset(_permitted_address(row[4][0]) for row in rows)
    except (OSError, ValueError, TypeError, IndexError):
        raise ConfigurationError("Source resolution failed") from None
    if not addresses or len(addresses) > 64:
        raise ConfigurationError("Source resolution failed")
    return addresses


def load_config(path):
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            raise ConfigurationError("Invalid configuration")
        return resolve_sources(json.loads(data))
    except (OSError, ValueError, UnicodeError, TypeError):
        raise ConfigurationError("Receiver configuration unavailable") from None


def format_message(datagram, received_at=None):
    """Return an RFC5424 message without reserializing the native logfmt body."""
    if not isinstance(datagram, bytes):
        raise RejectedDatagram("format")
    if len(datagram) > MAX_DATAGRAM:
        raise RejectedDatagram("oversize")
    data = datagram.rstrip(b"\r\n\x00")
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise RejectedDatagram("utf8") from None
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in text):
        raise RejectedDatagram("control")
    priority = _PRIORITY.match(data)
    if not priority or int(priority[1]) > 191:
        raise RejectedDatagram("priority")
    body = data[priority.end() :]
    native = _NATIVE.match(body)
    if not native:
        raise RejectedDatagram("format")
    try:
        date(*(int(part) for part in native.groups()))
    except ValueError:
        raise RejectedDatagram("format") from None
    stamp = time.time() if received_at is None else received_at
    try:
        if isinstance(stamp, bool) or not math.isfinite(stamp):
            raise ValueError()
        timestamp = datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="microseconds")
    except (ValueError, TypeError, OSError, OverflowError):
        raise RejectedDatagram("timestamp") from None
    header = (
        "<"
        + priority[1].decode("ascii")
        + ">1 "
        + timestamp.replace("+00:00", "Z")
        + " - fortigate - - - "
    ).encode("ascii")
    message = header + body
    if len(message) > MAX_MESSAGE:
        raise RejectedDatagram("oversize")
    return message


def frame_message(message):
    if not isinstance(message, bytes) or not message or len(message) > MAX_MESSAGE:
        raise RejectedDatagram("oversize")
    return str(len(message)).encode("ascii") + b" " + message


def _peer_closed(connection):
    ready, _, _ = select.select([connection], [], [], 0)
    if not ready:
        return False
    # The fixed upstream is a one-way syslog sink, not a request/response service.
    # EOF or unexpected inbound bytes both require a new transport connection.
    connection.recv(1, socket.MSG_PEEK)
    return True


class Receiver:
    def __init__(
        self,
        sources,
        *,
        connector=None,
        clock=None,
        wait=None,
        peer_closed=None,
        queue_capacity=QUEUE_CAPACITY,
        udp_address=None,
        upstream=None,
    ):
        if not sources or not 1 <= queue_capacity <= QUEUE_CAPACITY:
            raise ConfigurationError("Invalid receiver bounds")
        self.sources = frozenset(sources)
        self.udp_address = udp_address or UDP_ADDRESS
        self.upstream = upstream or UPSTREAM
        self.queue = queue.Queue(maxsize=queue_capacity)
        self.connector = connector or socket.create_connection
        self.clock = clock or time.time
        self.peer_closed = peer_closed or _peer_closed
        self.stop = threading.Event()
        self.wait = wait or self.stop.wait
        self.lock = threading.Lock()
        self.connection_lock = threading.Lock()
        self.connection = None
        self.udp_socket = None
        self.udp_bound = False
        self.upstream_connected = False
        self.failed = False
        self.receiver_thread = None
        self.worker_thread = None
        self.received = 0
        self.handed = 0
        self.rejected = dict.fromkeys(REASONS, 0)
        self.queue_drops = 0
        self.send_errors = 0
        self.last_forwarding = None

    def accept_datagram(self, data, source, received_at=None):
        with self.lock:
            self.received += 1
        if source not in self.sources:
            with self.lock:
                self.rejected["source"] += 1
            return False
        try:
            message = format_message(data, self.clock() if received_at is None else received_at)
            frame = frame_message(message)
        except RejectedDatagram as exc:
            with self.lock:
                self.rejected[exc.reason] += 1
            return False
        try:
            if self.stop.is_set():
                raise queue.Full()
            self.queue.put_nowait(frame)
        except queue.Full:
            with self.lock:
                self.queue_drops += 1
            return False
        return True

    def _set_connected(self, value):
        with self.lock:
            self.upstream_connected = value

    def _close_connection(self):
        with self.connection_lock:
            connection, self.connection = self.connection, None
        self._set_connected(False)
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass

    def _run_forwarder(self):
        pending = None
        delay = INITIAL_BACKOFF
        try:
            while not self.stop.is_set():
                try:
                    connection = self.connection
                    if connection is None:
                        connection = self.connector(self.upstream, timeout=CONNECT_TIMEOUT)
                        with self.connection_lock:
                            self.connection = connection
                        connection.settimeout(CONNECT_TIMEOUT)
                        if self.stop.is_set():
                            break
                        self._set_connected(True)
                    if pending is None:
                        try:
                            pending = self.queue.get(timeout=0.25)
                        except queue.Empty:
                            if self.peer_closed(connection):
                                raise ConnectionError("Upstream closed")
                            continue
                    connection.sendall(pending)
                    with self.lock:
                        self.handed += 1
                        stamp = self.clock()
                        self.last_forwarding = stamp if math.isfinite(stamp) else None
                    self.queue.task_done()
                    pending = None
                    delay = INITIAL_BACKOFF
                except (OSError, ValueError):
                    self._close_connection()
                    if not self.stop.is_set():
                        with self.lock:
                            self.send_errors += 1
                        if self.wait(delay):
                            break
                        delay = min(MAX_BACKOFF, delay * 2)
        except Exception:
            # Never put socket exception text or a queued raw frame in logs.
            if not self.stop.is_set():
                self.failed = True
                self.stop.set()
        finally:
            self._close_connection()
            if pending is not None:
                self.queue.task_done()
                with self.lock:
                    self.queue_drops += 1

    def _run_receiver(self):
        try:
            while not self.stop.is_set():
                try:
                    data, address = self.udp_socket.recvfrom(65535)
                except socket.timeout:
                    continue
                if not self.stop.is_set():
                    self.accept_datagram(data, address[0])
        except Exception:
            if not self.stop.is_set():
                self.failed = True
                self.stop.set()
        finally:
            with self.lock:
                self.udp_bound = False

    def start(self, before_threads=None):
        if self.worker_thread is not None:
            raise RuntimeError("Receiver already started")
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.udp_socket.settimeout(0.25)
        self.udp_socket.bind(self.udp_address)
        with self.lock:
            self.udp_bound = True
        if before_threads is not None:
            before_threads()
        self.worker_thread = threading.Thread(target=self._run_forwarder, daemon=True)
        self.receiver_thread = threading.Thread(target=self._run_receiver, daemon=True)
        self.worker_thread.start()
        self.receiver_thread.start()

    def close(self):
        self.stop.set()
        if self.udp_socket is not None:
            self.udp_socket.close()
        self._close_connection()
        deadline = time.monotonic() + SHUTDOWN_TIMEOUT
        for thread in (self.receiver_thread, self.worker_thread):
            if thread is not None and thread.is_alive():
                thread.join(max(0, deadline - time.monotonic()))
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                with self.lock:
                    self.queue_drops += 1
            except queue.Empty:
                break
        with self.lock:
            self.udp_bound = False

    def snapshot(self):
        with self.lock:
            worker_alive = bool(self.worker_thread and self.worker_thread.is_alive())
            ready = (
                self.udp_bound
                and worker_alive
                and self.upstream_connected
                and not self.stop.is_set()
            )
            result = {
                "service": "running",
                "component": "native-syslog-receiver",
                "ready": ready,
                "udp_bound": self.udp_bound,
                "worker_alive": worker_alive,
                "upstream_connected": self.upstream_connected,
                "storage_verified": False,
                "received_datagrams": self.received,
                "handed_to_tcp": self.handed,
                "rejected_datagrams": sum(self.rejected.values()),
                "rejected_reasons": dict(self.rejected),
                "queue_drops": self.queue_drops,
                "send_errors": self.send_errors,
                "last_forwarding_seconds": self.last_forwarding,
            }
        result.update(queue_depth=self.queue.qsize(), queue_capacity=self.queue.maxsize)
        return result


def metrics(receiver):
    snapshot = receiver.snapshot()
    prefix = "fortilens_native_syslog_"
    fields = {
        "received_datagrams_total": snapshot["received_datagrams"],
        "handed_to_tcp_total": snapshot["handed_to_tcp"],
        "queue_drops_total": snapshot["queue_drops"],
        "tcp_send_errors_total": snapshot["send_errors"],
        "ready": int(snapshot["ready"]),
        "udp_bound": int(snapshot["udp_bound"]),
        "upstream_connected": int(snapshot["upstream_connected"]),
        "queue_depth": snapshot["queue_depth"],
    }
    if snapshot["last_forwarding_seconds"] is not None:
        fields["last_forwarding_seconds"] = snapshot["last_forwarding_seconds"]
    output = [prefix + name + " " + str(value) for name, value in fields.items()]
    output.extend(
        prefix + 'rejected_datagrams_total{reason="' + reason + '"} ' + str(value)
        for reason, value in snapshot["rejected_reasons"].items()
    )
    return "\n".join(output) + "\n"


def handler(receiver):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(2)
            super().setup()

        def log_message(self, *args):
            pass

        def respond(self, status, body, kind="application/json; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            try:
                if self.path == "/health":
                    snapshot = receiver.snapshot()
                    self.respond(
                        200 if snapshot["ready"] else 503, json.dumps(snapshot, allow_nan=False)
                    )
                elif self.path == "/metrics":
                    self.respond(200, metrics(receiver), "text/plain; version=0.0.4; charset=utf-8")
                else:
                    self.respond(404, '{"error":"not_found"}')
            except (OSError, ValueError):
                self.close_connection = True

        def do_POST(self):
            self.respond(405, '{"error":"method_not_allowed"}')

    return Handler


class _Arguments(argparse.ArgumentParser):
    def error(self, message):
        raise ConfigurationError("Invalid receiver arguments")


def host_endpoints(path):
    """Linux host profile: public UDP, private TCP handoff and health only.

    Never use a caller-selected forwarding host or broaden the source filter.
    The adjacent health port makes independent installations configurable without
    relying on Docker DNS or globally reserved auxiliary ports.
    """
    try:
        with Path(path).open('rb') as stream:
            data = stream.read(256 * 1024 + 1)
        if len(data) > 256 * 1024:
            raise ValueError()
        config = json.loads(data)
        port = config['syslog_port']
        bind = str(ipaddress.IPv4Address(config['syslog_bind']))
        if (config.get('syslog_network') != 'host'
                or config.get('syslog_format') != 'fortigate'
                or config.get('syslog_protocol') != 'udp'
                or type(port) is not int or not 1 <= port < 65535):
            raise ValueError()
        return (bind, port), ('127.0.0.1', port), ('127.0.0.1', port + 1)
    except (OSError, ValueError, KeyError, TypeError):
        raise ConfigurationError('Invalid host receiver configuration') from None


def drop_host_privileges():
    """Permanently drop the Linux bind bootstrap before handling input."""
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise ConfigurationError('Host receiver requires the managed Linux bootstrap')
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    if (os.getuid() != 10001 or os.geteuid() != 10001
            or os.getgid() != 10001 or os.getegid() != 10001 or os.getgroups()
            or status.get('NoNewPrivs','').strip() != '1'
            or any(int(status[key].strip(), 16) for key in ('CapInh', 'CapPrm', 'CapEff', 'CapAmb'))):
        raise ConfigurationError('Host receiver privilege drop failed')


def main(argv=None):
    receiver = server = http_thread = None
    old_handlers = {}
    try:
        parser = _Arguments(prog="fortilens-native-syslog", description=__doc__)
        parser.add_argument("--config", required=True, type=Path)
        parser.add_argument("--host-network", action="store_true")
        args = parser.parse_args(argv)
        options = {}
        http_address = HTTP_ADDRESS
        if args.host_network:
            udp, upstream, http_address = host_endpoints(args.config)
            options = {'udp_address': udp, 'upstream': upstream}
        receiver = Receiver(load_config(args.config), **options)
        server = ThreadingHTTPServer(http_address, handler(receiver))
        server.daemon_threads = True
        receiver.start(before_threads=drop_host_privileges if args.host_network else None)
        http_thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
        )
        http_thread.start()
        for name in (signal.SIGTERM, signal.SIGINT):
            old_handlers[name] = signal.signal(name, lambda *_args: receiver.stop.set())
        while not receiver.stop.wait(0.25):
            if (
                not receiver.receiver_thread.is_alive()
                or not receiver.worker_thread.is_alive()
                or not http_thread.is_alive()
            ):
                raise RuntimeError("Receiver worker stopped")
        if receiver.failed:
            raise RuntimeError("Receiver stopped")
        return 0
    except ConfigurationError:
        print("Native syslog receiver configuration is unavailable or invalid.", file=sys.stderr)
        return 2
    except Exception:
        print(
            "Native syslog receiver could not start or continue. Review listener readiness.",
            file=sys.stderr,
        )
        return 1
    finally:
        for name, previous in old_handlers.items():
            signal.signal(name, previous)
        if receiver is not None:
            receiver.close()
        if server is not None:
            if http_thread is not None and http_thread.is_alive():
                server.shutdown()
                http_thread.join(1)
            server.server_close()


if __name__ == "__main__":
    raise SystemExit(main())
