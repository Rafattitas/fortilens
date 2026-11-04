"""Source-preserving reception: synthetic configuration and isolated sockets only."""

import contextlib
import io
import json
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import setup
import syslog_receiver as receiver


class NativeHostTests(unittest.TestCase):
    def test_privilege_drop_failure_prevents_all_message_threads(self):
        service = receiver.Receiver({"192.0.2.10"})
        udp = Mock()

        def rejected_bootstrap():
            self.assertTrue(service.udp_bound)
            self.assertIsNone(service.receiver_thread)
            self.assertIsNone(service.worker_thread)
            raise receiver.ConfigurationError("Synthetic privilege drop failure")

        with (
            patch.object(receiver.socket, "socket", return_value=udp),
            patch.object(receiver.threading, "Thread") as thread,
        ):
            with self.assertRaises(receiver.ConfigurationError):
                service.start(before_threads=rejected_bootstrap)
            thread.assert_not_called()
            service.close()
        udp.close.assert_called_once()

    def config(self, **changes):
        return setup.validate_config(
            {
                "fortigate_host": "192.0.2.10",
                "syslog_protocol": "udp",
                "syslog_format": "fortigate",
                "syslog_network": "host",
                "syslog_port": 5514,
                **changes,
            }
        )

    def test_host_profile_has_private_handoff_health_and_narrow_privileges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "installation"
            with contextlib.redirect_stdout(io.StringIO()):
                setup.render(self.config(), root, "synthetic-token")
            compose = setup.load(root / "compose.yaml")
            native = compose["services"]["fortilens-syslog"]
            self.assertEqual(native["network_mode"], "host")
            self.assertNotIn("ports", native)
            self.assertEqual(native["user"], "0:0")  # Bind-only bootstrap, verified dropped below.
            self.assertEqual(native["cap_drop"], ["ALL"])
            self.assertEqual(native["cap_add"], ["NET_BIND_SERVICE", "SETUID", "SETGID"])
            self.assertTrue(native["read_only"])
            self.assertNotIn("secrets", native)
            self.assertEqual(compose["services"]["alloy"]["ports"], ["127.0.0.1:5514:1514/tcp"])
            self.assertEqual(
                receiver.host_endpoints(root / "site.json"),
                (("0.0.0.0", 5514), ("127.0.0.1", 5514), ("127.0.0.1", 5515)),
            )
            self.assertEqual(receiver.load_config(root / "site.json"), {"192.0.2.10"})
            prom = setup.load(root / "prometheus/prometheus.yml")
            self.assertFalse(
                any(job["job_name"] == "fortilens-syslog" for job in prom["scrape_configs"])
            )
            self.assertIn("127.0.0.1:5515/health", native["healthcheck"]["test"][-1])

    def test_host_profile_rejects_ambiguous_sources_protocols_and_collisions(self):
        for changes in (
            {"fortigate_host": "firewall.example.test"},
            {"fortigate_host": "2001:db8::1"},
            {"syslog_protocol": "tcp"},
            {"syslog_format": "rfc5424"},
            {"syslog_port": 65535},
            {"syslog_port": 3001},
            {"syslog_port": 3000},
            {"syslog_port": 3002},
            {"syslog_network": "arbitrary"},
        ):
            with self.subTest(changes=changes), self.assertRaises(setup.SetupError):
                self.config(**changes)

    def test_host_endpoint_parser_fails_closed_without_echoing_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.json"
            for config in (
                {},
                {"syslog_port": True},
                {**self.config(), "syslog_bind": "hostname"},
                {**self.config(), "syslog_network": "bridge"},
            ):
                path.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaises(receiver.ConfigurationError):
                    receiver.host_endpoints(path)

    def test_direct_udp_bind_keeps_exact_source_filter_and_body(self):
        # Loopback is a synthetic test source supplied directly to Receiver;
        # production resolve_sources still rejects it.
        sink = socket.socket()
        sink.bind(("127.0.0.1", 0))
        sink.listen(1)
        sink.settimeout(4)
        service = receiver.Receiver(
            {"127.0.0.2"}, udp_address=("127.0.0.1", 0), upstream=sink.getsockname()
        )
        try:
            service.start()
            connection, _ = sink.accept()
            connection.settimeout(4)
            body = b"date=2026-11-03 time=01:00:00 sentbyte=12 rcvdbyte=34 eventtime=123456789"
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                sender.bind(("127.0.0.2", 0))
                sender.sendto(b"<134>" + body, service.udp_socket.getsockname())
            received = b""
            while not received.endswith(body):
                chunk = connection.recv(65536)
                self.assertTrue(chunk)
                received += chunk
            self.assertIn(b" - fortigate - - - " + body, received)
            self.assertEqual(service.snapshot()["received_datagrams"], 1)
            service.accept_datagram(b"<134>" + body, "127.0.0.1")
            self.assertEqual(service.snapshot()["rejected_reasons"]["source"], 1)
            connection.close()
        finally:
            service.close()
            sink.close()


try:
    import test_repair_runtime as fixtures
    import yaml

    from management.common import Problem
    from management.runtime import Docker
except (ImportError, unittest.SkipTest):
    fixtures = None


@unittest.skipIf(fixtures is None, "Management dependencies required")
class NativeHostManagementTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RepairRuntimeTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.c = self.fixture.c

    def install(self, plan):
        self.c.install_files(plan["id"])
        self.c.complete(plan["id"])

    def test_bridge_host_port_change_and_legacy_roundtrip_preserve_data_identities(self):
        candidate, plan = self.fixture.prepare_settings(
            syslog_format="fortigate", syslog_protocol="udp", syslog_port=5514
        )
        original = yaml.safe_load((candidate / self.c.compose_name).read_text())
        self.install(plan)
        for network, port in (("host", 5514), ("host", 6514), ("bridge", 7514), ("host", 8514)):
            candidate, plan = self.fixture.prepare_settings(
                syslog_format="fortigate",
                syslog_protocol="udp",
                syslog_network=network,
                syslog_port=port,
            )
            compose = yaml.safe_load((candidate / self.c.compose_name).read_text())
            self.assertEqual(compose["volumes"], original["volumes"])
            self.assertEqual(compose["services"]["grafana"], original["services"]["grafana"])
            if network == "host":
                self.assertEqual(compose["services"]["fortilens-syslog"]["network_mode"], "host")
                self.assertEqual(
                    compose["services"]["alloy"]["ports"], [f"127.0.0.1:{port}:1514/tcp"]
                )
            else:
                self.assertNotIn("network_mode", compose["services"]["fortilens-syslog"])
                self.assertEqual(
                    compose["services"]["fortilens-syslog"]["ports"], [f"0.0.0.0:{port}:1514/udp"]
                )
            self.install(plan)
        candidate, plan = self.fixture.prepare_settings(
            syslog_format="rfc5424",
            syslog_protocol="tcp",
            syslog_network="bridge",
            syslog_port=1514,
        )
        compose = yaml.safe_load((candidate / self.c.compose_name).read_text())
        self.assertEqual(compose["services"]["alloy"]["ports"], ["0.0.0.0:1514:1514/tcp"])
        self.install(plan)
        # A disabled definition must remain re-enableable in either profile.
        self.fixture.prepare_settings(
            syslog_format="fortigate",
            syslog_protocol="udp",
            syslog_network="host",
            syslog_port=5514,
        )

    def test_host_health_uses_registered_identity_and_loopback_only(self):
        candidate, plan = self.fixture.prepare_settings(
            syslog_format="fortigate",
            syslog_protocol="udp",
            syslog_network="host",
            syslog_port=5514,
        )
        self.install(plan)
        docker = Docker(self.c)
        info = [
            {
                "Config": {"Labels": {"com.docker.compose.project": self.c.project}},
                "HostConfig": {"NetworkMode": "host"},
            }
        ]
        with patch.object(docker, "run", side_effect=["fixture-id", json.dumps(info)]):
            self.assertEqual(docker.address("fortilens-syslog", 8080), ("127.0.0.1", 5515))

    def test_custom_host_handoff_is_not_silently_removed(self):
        candidate, plan = self.fixture.prepare_settings(
            syslog_format="fortigate",
            syslog_protocol="udp",
            syslog_network="host",
            syslog_port=5514,
        )
        self.install(plan)
        path = self.c.root / self.c.compose_name
        compose = yaml.safe_load(path.read_text())
        compose["services"]["alloy"]["ports"].append("127.0.0.1:6514:1514/tcp")
        path.write_text(yaml.safe_dump(compose), encoding="utf-8")
        with self.assertRaises(Problem) as caught:
            self.fixture.prepare_settings(
                syslog_format="fortigate",
                syslog_protocol="udp",
                syslog_network="bridge",
                syslog_port=5514,
            )
        self.assertEqual(caught.exception.code, "custom_ports_review")

    def test_host_port_check_detects_private_health_conflict(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1] - 1
            candidate, plan = self.fixture.prepare_settings(
                syslog_format="fortigate",
                syslog_protocol="udp",
                syslog_network="host",
                syslog_port=port,
            )
            compose = yaml.safe_load((candidate / self.c.compose_name).read_text())
            # Keep only the two relevant enabled services in this isolated check.
            compose["services"] = {
                k: v for k, v in compose["services"].items() if k in ("alloy", "fortilens-syslog")
            }
            docker = Docker(self.c)
            with (
                patch.object(docker, "statuses", return_value=[]),
                patch("management.runtime.platform.system", return_value="Linux"),
                patch.object(docker, "run", return_value=json.dumps({"OSType": "linux"})),
            ):
                with self.assertRaises(Problem) as caught:
                    docker.check_ports(compose)
                self.assertEqual(caught.exception.code, "port_in_use")

    def test_rootless_and_desktop_engines_are_rejected_before_binding(self):
        candidate, plan = self.fixture.prepare_settings(
            syslog_format="fortigate",
            syslog_protocol="udp",
            syslog_network="host",
            syslog_port=5514,
        )
        compose = yaml.safe_load((candidate / self.c.compose_name).read_text())
        compose["services"] = {"fortilens-syslog": compose["services"]["fortilens-syslog"]}
        docker = Docker(self.c)
        for engine in (
            {"OSType": "linux", "SecurityOptions": ["name=rootless"]},
            {"OSType": "linux", "OperatingSystem": "Docker Desktop"},
            {"OSType": "windows"},
        ):
            with (
                self.subTest(engine=engine),
                patch.object(docker, "statuses", return_value=[]),
                patch("management.runtime.platform.system", return_value="Linux"),
                patch.object(docker, "run", return_value=json.dumps(engine)),
            ):
                with self.assertRaises(Problem) as caught:
                    docker.check_ports(compose)
                self.assertEqual(caught.exception.code, "native_host_unsupported")
