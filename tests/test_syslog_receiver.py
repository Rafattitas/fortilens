"""Native UDP formatting and bounded transport tests use synthetic records only."""

import io
import json
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stderr
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import syslog_receiver as receiver

BODY = (
    'date=2026-11-03 time=01:00:00 devname="FG-FIXTURE" devid="FG-FIXTURE" '
    'vd="root" type="traffic" subtype="forward" logid="0000000013" '
    "eventtime=1793667600123456789 sentbyte=12 rcvdbyte=34 "
    'msg="quoted \\"value\\" and \\\\ path — اختبار" '
).encode("utf-8")
DATAGRAM = b"<189>" + BODY
STAMP = 1700000000.25


class Transport:
    def __init__(self, *, fail=False, sent=None, finish=None):
        self.fail = fail
        self.sent = sent if sent is not None else []
        self.finish = finish
        self.closed = False
        self.timeout = None

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, value):
        self.sent.append(value)
        if self.fail:
            raise OSError("synthetic-private-transport-detail")
        if self.finish:
            self.finish()

    def close(self):
        self.closed = True


class NativeFormatterTests(unittest.TestCase):
    def rejection(self, data, reason, **options):
        with self.assertRaises(receiver.RejectedDatagram) as caught:
            receiver.format_message(data, **options)
        self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(str(caught.exception), reason)

    def test_receipt_envelope_preserves_native_body_and_eventtime_exactly(self):
        message = receiver.format_message(DATAGRAM, STAMP)
        self.assertTrue(
            message.startswith(b"<189>1 2023-11-14T22:13:20.250000Z - fortigate - - - ")
        )
        self.assertTrue(message.endswith(BODY))
        self.assertIn(b"eventtime=1793667600123456789", message)
        self.assertEqual(message.split(b" - fortigate - - - ", 1)[1], BODY)

    def test_octet_count_uses_utf8_bytes_and_preserves_multiple_frame_boundaries(self):
        first = receiver.format_message(DATAGRAM, STAMP)
        second = receiver.format_message(DATAGRAM.replace(b"sentbyte=12", b"sentbyte=99"), STAMP)
        wire = receiver.frame_message(first) + receiver.frame_message(second)
        for expected in (first, second):
            size, rest = wire.split(b" ", 1)
            length = int(size)
            self.assertEqual(length, len(expected))
            self.assertGreater(length, len(expected.decode("utf-8")))
            self.assertEqual(rest[:length], expected)
            wire = rest[length:]
        self.assertEqual(wire, b"")

    def test_only_trailing_transport_delimiters_are_removed(self):
        message = receiver.format_message(DATAGRAM + b"\r\n\x00\n", STAMP)
        self.assertTrue(message.endswith(BODY))
        self.assertTrue(message.endswith(b'" '), "Ordinary trailing spaces are body data")
        for suffix in (b"\nmsg=second", b"\x00msg=second", b"\tmsg=second", b"\x7f"):
            with self.subTest(suffix=suffix):
                self.rejection(DATAGRAM + suffix, "control")
        self.rejection(DATAGRAM + "\u0085".encode(), "control")

    def test_utf8_size_and_complete_message_limits_are_explicit(self):
        self.rejection(DATAGRAM + b"\xff", "utf8")
        exact = DATAGRAM + b"x" * (receiver.MAX_DATAGRAM - len(DATAGRAM))
        message = receiver.format_message(exact, STAMP)
        self.assertLessEqual(len(message), receiver.MAX_MESSAGE)
        self.rejection(exact + b"x", "oversize")
        with self.assertRaises(receiver.RejectedDatagram):
            receiver.frame_message(b"x" * (receiver.MAX_MESSAGE + 1))

    def test_non_native_or_invalid_priority_is_rejected_without_invented_metadata(self):
        for prefix in (b"", b"<>", b"<-1>", b"<192>", b"<999>", b"<019>"):
            with self.subTest(prefix=prefix):
                self.rejection(prefix + BODY, "priority")
        for prefix in (b"<0>", b"<191>"):
            with self.subTest(prefix=prefix):
                self.assertTrue(
                    receiver.format_message(prefix + BODY, STAMP).startswith(prefix + b"1 ")
                )
        for body in (
            b"Nov  3 01:00:00 fixture msg=synthetic",
            b"1 2026-11-03T01:00:00Z fixture - - - - msg=synthetic",
            b'{"date":"2026-11-03"}',
            b"date=2026-02-30 time=01:00:00 msg=synthetic",
            b"date=2026-11-03 time=25:00:00 msg=synthetic",
        ):
            with self.subTest(body=body):
                self.rejection(b"<189>" + body, "format")
        self.rejection(DATAGRAM, "timestamp", received_at=float("nan"))


class SourceAndQueueTests(unittest.TestCase):
    def test_literal_and_dns_sources_do_not_broaden_to_subnets(self):
        self.assertEqual(receiver.resolve_sources({"fortigate_host": "192.0.2.10"}), {"192.0.2.10"})
        resolver = Mock(
            return_value=[
                (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.0.2.10", 1514)),
                (socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.0.2.11", 1514)),
            ]
        )
        self.assertEqual(
            receiver.resolve_sources({"fortigate_host": "firewall.example.test"}, resolver),
            {"192.0.2.10", "192.0.2.11"},
        )
        resolver.assert_called_once_with(
            "firewall.example.test",
            1514,
            family=socket.AF_INET,
            type=socket.SOCK_DGRAM,
        )
        for host in (
            "127.0.0.1",
            "0.0.0.0",
            "169.254.169.254",
            "224.0.0.1",
            "::1",
            "192.0.2.0/24",
            "https://private@example.test",
        ):
            with self.subTest(host=host), self.assertRaises(receiver.ConfigurationError):
                receiver.resolve_sources({"fortigate_host": host})

    def test_source_resolution_failure_is_bounded_and_does_not_echo_details(self):
        resolver = Mock(side_effect=OSError("synthetic-private-dns-detail"))
        with self.assertRaises(receiver.ConfigurationError) as caught:
            receiver.resolve_sources({"fortigate_host": "firewall.example.test"}, resolver)
        self.assertNotIn("private", str(caught.exception))
        with self.assertRaises(receiver.ConfigurationError):
            receiver.resolve_sources(
                {"fortigate_host": "firewall.example.test"}, Mock(return_value=[])
            )

    def test_disallowed_source_and_queue_overflow_do_not_claim_tcp_delivery(self):
        service = receiver.Receiver({"192.0.2.10"}, clock=lambda: STAMP, queue_capacity=1)
        self.assertFalse(service.accept_datagram(DATAGRAM, "192.0.2.11"))
        self.assertTrue(service.accept_datagram(DATAGRAM, "192.0.2.10"))
        self.assertFalse(service.accept_datagram(DATAGRAM, "192.0.2.10"))
        snapshot = service.snapshot()
        self.assertEqual(snapshot["received_datagrams"], 3)
        self.assertEqual(snapshot["handed_to_tcp"], 0)
        self.assertEqual(snapshot["rejected_reasons"]["source"], 1)
        self.assertEqual(snapshot["queue_drops"], 1)
        self.assertEqual(snapshot["queue_depth"], 1)
        self.assertIsNone(snapshot["last_forwarding_seconds"])
        self.assertNotIn("FG-FIXTURE", json.dumps(snapshot))
        service.close()
        self.assertEqual(service.snapshot()["queue_depth"], 0)
        with self.assertRaises(receiver.ConfigurationError):
            receiver.Receiver({"192.0.2.10"}, queue_capacity=257)


class ForwarderTests(unittest.TestCase):
    def test_shutdown_during_connect_closes_new_socket_without_claiming_delivery(self):
        service = receiver.Receiver({"192.0.2.10"})
        connection = Transport()

        def connect(*_args, **_options):
            service.stop.set()
            return connection

        service.connector = connect
        service._run_forwarder()
        self.assertTrue(connection.closed)
        self.assertFalse(service.failed)
        self.assertFalse(service.snapshot()["upstream_connected"])
        self.assertEqual(service.snapshot()["handed_to_tcp"], 0)

    def test_retry_reuses_the_original_frame_and_has_fixed_upstream(self):
        attempted, sent, delays, calls = [], [], [], []
        service = receiver.Receiver(
            {"192.0.2.10"},
            clock=lambda: STAMP,
            wait=lambda delay: delays.append(delay) or False,
        )
        broken = Transport(fail=True, sent=attempted)
        good = Transport(sent=sent, finish=service.stop.set)

        def connect(address, timeout):
            calls.append((address, timeout))
            if len(calls) == 1:
                raise OSError("synthetic-private-connect-detail")
            return broken if len(calls) == 2 else good

        service.connector = connect
        self.assertTrue(service.accept_datagram(DATAGRAM, "192.0.2.10"))
        service._run_forwarder()
        self.assertEqual(delays, [0.25, 0.5])
        self.assertEqual(len(calls), 3)
        self.assertTrue(
            all(call == (receiver.UPSTREAM, receiver.CONNECT_TIMEOUT) for call in calls)
        )
        self.assertEqual(attempted, sent)
        self.assertTrue(sent[0].endswith(BODY))
        snapshot = service.snapshot()
        self.assertEqual(snapshot["send_errors"], 2)
        self.assertEqual(snapshot["handed_to_tcp"], 1)
        self.assertEqual(snapshot["last_forwarding_seconds"], STAMP)
        self.assertFalse(snapshot["storage_verified"])
        self.assertTrue(broken.closed and good.closed)

    def test_idle_peer_close_reconnects_without_sending_a_synthetic_message(self):
        connections, delays = [], []
        service = receiver.Receiver(
            {"192.0.2.10"},
            wait=lambda delay: delays.append(delay) or False,
        )

        def connect(*_args, **_options):
            connection = Transport()
            connections.append(connection)
            return connection

        def closed(connection):
            if connection is connections[0]:
                return True
            service.stop.set()
            return False

        service.connector, service.peer_closed = connect, closed
        service._run_forwarder()
        self.assertEqual(len(connections), 2)
        self.assertEqual(delays, [receiver.INITIAL_BACKOFF])
        self.assertEqual([connection.sent for connection in connections], [[], []])
        self.assertEqual(service.snapshot()["handed_to_tcp"], 0)

    def test_outage_backoff_is_capped_and_can_stop_without_raw_exception_output(self):
        delays = []
        service = receiver.Receiver({"192.0.2.10"})

        def wait(delay):
            delays.append(delay)
            if len(delays) == 8:
                service.stop.set()
                return True
            return False

        service.connector = Mock(side_effect=OSError("synthetic-private-connect-detail"))
        service.wait = wait
        output = io.StringIO()
        with redirect_stderr(output):
            service._run_forwarder()
        self.assertEqual(delays, [0.25, 0.5, 1, 2, 4, 5, 5, 5])
        self.assertEqual(service.snapshot()["send_errors"], 8)
        self.assertEqual(output.getvalue(), "")


class HealthAndStartupTests(unittest.TestCase):
    def test_listener_startup_failure_closes_resources_without_echoing_exception(self):
        udp = Mock()
        udp.bind.side_effect = OSError("synthetic-private-listener-detail")
        server = Mock()
        output = io.StringIO()
        with (
            patch.object(receiver, "load_config", return_value={"192.0.2.10"}),
            patch.object(receiver, "ThreadingHTTPServer", return_value=server),
            patch.object(receiver.socket, "socket", return_value=udp),
            redirect_stderr(output),
        ):
            self.assertEqual(receiver.main(["--config", "unused.json"]), 1)
        udp.close.assert_called_once()
        server.server_close.assert_called_once()
        server.shutdown.assert_not_called()
        self.assertNotIn("synthetic-private", output.getvalue())

    def test_health_requires_transport_readiness_but_never_claims_loki_storage(self):
        service = receiver.Receiver({"192.0.2.10"})
        server = ThreadingHTTPServer(("127.0.0.1", 0), receiver.handler(service))
        thread = threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = "http://127.0.0.1:" + str(server.server_port)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(base + "/health", timeout=2)
        self.assertEqual(caught.exception.code, 503)
        self.assertFalse(json.load(caught.exception)["ready"])
        service.udp_bound = True
        service.worker_thread = Mock(is_alive=Mock(return_value=True))
        service._set_connected(True)
        with opener.open(base + "/health", timeout=2) as response:
            data = json.load(response)
        self.assertTrue(data["ready"])
        self.assertFalse(data["storage_verified"])
        self.assertEqual(data["handed_to_tcp"], 0)
        with opener.open(base + "/metrics", timeout=2) as response:
            metrics = response.read().decode()
        self.assertIn("fortilens_native_syslog_handed_to_tcp_total 0\n", metrics)
        self.assertIn(
            'fortilens_native_syslog_rejected_datagrams_total{reason="source"} 0\n', metrics
        )
        self.assertNotIn("FG-FIXTURE", metrics)

    def test_fixed_listener_addresses_and_bounded_close(self):
        udp = Mock()
        threads = []

        def make_thread(**options):
            thread = Mock()
            thread.is_alive.return_value = False
            threads.append((thread, options))
            return thread

        service = receiver.Receiver({"192.0.2.10"})
        with (
            patch.object(receiver.socket, "socket", return_value=udp),
            patch.object(receiver.threading, "Thread", side_effect=make_thread),
        ):
            service.start()
        udp.bind.assert_called_once_with(("0.0.0.0", 1514))
        self.assertEqual(len(threads), 2)
        self.assertTrue(service.udp_bound)
        service.close()
        udp.close.assert_called_once()
        self.assertFalse(service.snapshot()["ready"])

    def test_configuration_and_cli_failures_do_not_echo_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "site.json"
            config.write_text(
                json.dumps({"fortigate_host": "192.0.2.10", "ignored": "synthetic-private"})
            )
            self.assertEqual(receiver.load_config(config), {"192.0.2.10"})
            config.write_text('{"fortigate_host":"https://synthetic-private@example.test"}')
            output = io.StringIO()
            with redirect_stderr(output):
                self.assertEqual(receiver.main(["--config", str(config)]), 2)
                self.assertEqual(receiver.main(["--unknown", "synthetic-private"]), 2)
            self.assertNotIn("synthetic-private", output.getvalue())
            self.assertNotIn(directory, output.getvalue())

    def test_metrics_omit_forwarding_time_until_a_message_is_forwarded(self):
        snapshot = {
            "received_datagrams": 0,
            "handed_to_tcp": 0,
            "queue_drops": 0,
            "send_errors": 0,
            "last_forwarding_seconds": None,
            "ready": False,
            "udp_bound": True,
            "upstream_connected": False,
            "queue_depth": 0,
            "rejected_reasons": {},
        }

        class Snapshot:
            def __init__(self, values):
                self.values = values

            def snapshot(self):
                return self.values

        self.assertNotIn("last_forwarding_seconds", receiver.metrics(Snapshot(snapshot)))
        snapshot["last_forwarding_seconds"] = 1700000000.5
        self.assertIn(
            "fortilens_native_syslog_last_forwarding_seconds 1700000000.5",
            receiver.metrics(Snapshot(snapshot)),
        )


if __name__ == "__main__":
    unittest.main()
