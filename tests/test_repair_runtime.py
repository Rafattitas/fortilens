"""Draft readiness and applied diagnostics use synthetic, disposable state."""

import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import test_runtime_errors as fixtures
    import yaml

    from management.common import Problem, atomic
    from management.configuration import optional_probe_policy
    from management.models import Settings
    from management.runtime import Docker, enabled_services
except ImportError:
    raise unittest.SkipTest("Management test dependencies are required.")


class RepairRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RuntimeErrorTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.c = self.fixture.config
        self.ops = self.fixture.operations

    def prepare_settings(self, **changes):
        view = self.c.view()
        settings = deepcopy(view["settings"])
        settings.update(fortigate_host="192.0.2.1", **changes)
        saved = self.c.save(
            {
                "revision": view["revision"],
                "settings": settings,
                "api_token": "synthetic-token",
                "replace_token": True,
            }
        )
        review = self.c.prepare(saved["revision"])
        folder, plan = self.c.plan(review["plan_id"])
        return folder / "candidate", plan

    def test_native_fresh_candidate_and_legacy_roundtrip_preserve_state(self):
        candidate, plan = self.prepare_settings(
            syslog_format="fortigate", syslog_protocol="udp", syslog_port=514
        )
        compose = yaml.safe_load((candidate / self.c.compose_name).read_text())
        receiver = compose["services"]["fortilens-syslog"]
        self.assertEqual(receiver["ports"], ["0.0.0.0:514:1514/udp"])
        self.assertEqual(receiver["image"], "fortilens-syslog:error-fixture")
        self.assertNotIn("ports", compose["services"]["alloy"])
        self.assertTrue((candidate / "syslog/syslog_receiver.py").is_file())
        volumes = deepcopy(compose["volumes"])
        self.c.install_files(plan["id"])
        self.c.complete(plan["id"])
        candidate, second = self.prepare_settings(
            syslog_format="rfc5424", syslog_protocol="tcp", syslog_port=1514
        )
        legacy = yaml.safe_load((candidate / self.c.compose_name).read_text())
        self.assertEqual(legacy["services"]["alloy"]["ports"], ["0.0.0.0:1514:1514/tcp"])
        self.assertEqual(legacy["services"]["fortilens-syslog"]["ports"], [])
        active, disabled = enabled_services(Settings.model_validate(second["settings"]), legacy)
        self.assertIn("fortilens-syslog", disabled)
        self.assertNotIn("fortilens-syslog", active)
        self.assertEqual(legacy["volumes"], volumes)
        self.assertNotIn("max_message_length", (candidate / "alloy/config.alloy").read_text())
        self.c.install_files(second["id"])
        self.c.complete(second["id"])
        self.c.restore(second["id"])
        self.assertEqual(self.c.state()["applied"]["syslog_format"], "fortigate")
        self.assertEqual(
            yaml.safe_load((self.c.root / self.c.compose_name).read_text())["volumes"], volumes
        )

    def test_existing_native_migration_preserves_auth_and_dashboard_bytes(self):
        request = self.fixture.plan(existing=True)
        # Finish the saved candidate so a later change has no pending token.
        self.c.install_files(request.plan_id)
        self.c.complete(request.plan_id)
        auth_path = self.c.root / "secrets/fortigate-key.yaml"
        auth_bytes = auth_path.read_bytes()
        dashboard_bytes = (self.c.root / "grafana/dashboard.json").read_bytes()
        view = self.c.view()
        settings = deepcopy(view["settings"])
        settings.update(syslog_format="fortigate", syslog_protocol="udp", syslog_port=514)
        saved = self.c.save({"revision": view["revision"], "settings": settings})
        review = self.c.prepare(saved["revision"])
        folder, plan = self.c.plan(review["plan_id"])
        paths = [row["path"] for row in plan["files"]]
        self.assertNotIn("secrets/fortigate-key.yaml", paths)
        self.assertNotIn("grafana/dashboard.json", paths)
        self.c.install_files(plan["id"])
        self.c.complete(plan["id"])
        self.assertEqual(auth_path.read_bytes(), auth_bytes)
        self.assertEqual((self.c.root / "grafana/dashboard.json").read_bytes(), dashboard_bytes)
        # Reapplying native mode must keep the public UDP port on the bridge.
        candidate, again = self.prepare_settings(
            syslog_format="fortigate", syslog_protocol="udp", syslog_port=5514
        )
        compose = yaml.safe_load((candidate / self.c.compose_name).read_text())
        self.assertEqual(
            compose["services"]["fortilens-syslog"]["ports"], ["0.0.0.0:5514:1514/udp"]
        )
        self.assertEqual(compose["services"]["alloy"]["ports"], [])

    def test_optional_policy_replaces_only_known_exclusions_and_keeps_credentials(self):
        name = "System/SensorInfo"
        record = {
            "token": "synthetic-token",
            "probes": {"include": ["System/Status"], "exclude": ["Custom/Probe", name]},
        }
        before = deepcopy(record)
        result = optional_probe_policy(record, [])
        self.assertEqual(
            result,
            {
                "token": "synthetic-token",
                "probes": {"include": ["System/Status"], "exclude": ["Custom/Probe"]},
            },
        )
        self.assertEqual(record, before)
        self.assertEqual(
            optional_probe_policy({"token": "synthetic-token", "probes": None}, [name])["probes"][
                "exclude"
            ],
            [name],
        )
        candidate, plan = self.prepare_settings(optional_exporter_exclusions=[name])
        auth = yaml.safe_load((candidate / "secrets/fortigate-key.yaml").read_text())
        self.assertEqual(next(iter(auth.values()))["probes"]["exclude"], [name])
        self.c.install_files(plan["id"])
        self.c.complete(plan["id"])
        self.assertEqual(self.c.view()["exporter_policy"]["optional_exclusions"], [name])
        self.assertNotIn("synthetic-token", json.dumps(self.c.view()))

    def test_native_changes_preserve_unrelated_alloy_ports_and_reject_custom_receiver(self):
        _, plan = self.prepare_settings(syslog_format="fortigate", syslog_protocol="udp")
        self.c.install_files(plan["id"])
        self.c.complete(plan["id"])
        path = self.c.root / self.c.compose_name
        compose = yaml.safe_load(path.read_text())
        extra = "127.0.0.1:6514:6514/tcp"
        compose["services"]["alloy"]["ports"] = [extra]
        # The trusted portable image tag remains supported after adoption.
        compose["services"]["fortilens-syslog"]["image"] = "fortilens-syslog:local"
        atomic(path, yaml.safe_dump(compose).encode())
        candidate, _ = self.prepare_settings(syslog_format="fortigate", syslog_protocol="udp")
        rendered = yaml.safe_load((candidate / self.c.compose_name).read_text())
        self.assertEqual(rendered["services"]["alloy"]["ports"], [extra])
        self.assertEqual(
            rendered["services"]["fortilens-syslog"]["image"], "fortilens-syslog:local"
        )
        candidate, _ = self.prepare_settings(syslog_format="rfc5424", syslog_protocol="tcp")
        self.assertIn(
            extra,
            yaml.safe_load((candidate / self.c.compose_name).read_text())["services"]["alloy"][
                "ports"
            ],
        )
        compose["services"]["fortilens-syslog"]["environment"] = {"CUSTOM": "preserve"}
        atomic(path, yaml.safe_dump(compose).encode())
        with self.assertRaises(Problem) as caught:
            self.prepare_settings(syslog_format="fortigate", syslog_protocol="udp")
        self.assertEqual(caught.exception.code, "custom_syslog_review")

    def test_native_port_transfer_is_limited_to_registered_syslog_services(self):
        _, plan = self.prepare_settings(
            syslog_format="fortigate", syslog_protocol="udp", syslog_port=5514
        )
        self.c.install_files(plan["id"])
        self.c.complete(plan["id"])
        docker = Docker(self.c)
        info = [
            {
                "Config": {"Labels": {"com.docker.compose.project": self.c.project}},
                "NetworkSettings": {
                    "Ports": {"1514/udp": [{"HostIp": "0.0.0.0", "HostPort": "5514"}]}
                },
            }
        ]
        candidate = {"services": {"fortilens-syslog": {"ports": ["0.0.0.0:5514:1514/udp"]}}}
        with (
            patch.object(
                docker,
                "statuses",
                return_value=[{"id": "synthetic", "service": "alloy", "status": "running"}],
            ),
            patch.object(docker, "run", return_value=json.dumps(info)) as command,
            patch("management.runtime.socket.socket") as sock,
        ):
            docker.check_ports(candidate)
            sock.assert_not_called()
            info[0]["Config"]["Labels"]["com.docker.compose.project"] = "unrelated"
            command.return_value = json.dumps(info)
            with self.assertRaises(Problem) as caught:
                docker.check_ports(candidate)
            self.assertEqual(caught.exception.code, "project_mismatch")

    def test_applied_sla_scope_is_retained_while_draft_changes(self):
        candidate, plan = self.prepare_settings(sla="google")
        self.c.install_files(plan["id"])
        self.c.complete(plan["id"])
        view = self.c.view()
        settings = deepcopy(view["settings"])
        settings["sla"] = "different"
        self.c.save({"revision": view["revision"], "settings": settings})
        self.assertEqual(self.c.view()["active_sla"], "google")
        self.assertIn("var-sla=google", self.ops.status()["links"]["dashboard"])

    def test_native_diagnostics_callback_is_fixed_and_contains_service_errors(self):
        candidate, plan = self.prepare_settings(syslog_format="fortigate", syslog_protocol="udp")
        self.c.install_files(plan["id"])
        self.c.complete(plan["id"])
        with patch(
            "management.runtime.doctor.run", return_value={"checks": [], "status": "UNKNOWN"}
        ) as run:
            self.ops.diagnostics()
        callback = run.call_args.kwargs["native_receiver_request"]
        with patch.object(self.ops.docker, "http", return_value={"ready": True}) as request:
            self.assertEqual(callback(None, None), {"ready": True})
            request.assert_called_once_with("fortilens-syslog", 8080, "/health")

    def wan_draft(self):
        view = self.c.view()
        settings = dict(view["settings"])
        settings.update(
            fortigate_host="192.0.2.1", sla="ISP", wan_interfaces=["uplink-a"], wan_confirmed=True
        )
        return self.ops.dispatch(
            "save",
            {
                "revision": view["revision"],
                "settings": settings,
                "api_token": "synthetic-token",
                "replace_token": True,
            },
        )

    def test_readiness_requires_discovery_after_connection_or_credential_change(self):
        saved = self.wan_draft()
        self.assertTrue(saved["discovery"]["required"])
        self.assertFalse(saved["discovery"]["current"])
        result = self.ops.dispatch("discover", {"revision": saved["revision"]})
        self.assertTrue(result["discovery"]["current"])
        view = self.ops.dispatch("view", {})
        saved = self.ops.dispatch(
            "save",
            {
                "revision": view["revision"],
                "settings": view["settings"],
                "api_token": "synthetic-replacement",
                "replace_token": True,
            },
        )
        self.assertFalse(saved["discovery"]["current"])
        self.assertNotIn("synthetic-replacement", str(saved["discovery"]))

    def test_corrupt_discovery_record_is_actionable_and_not_overwritten(self):
        self.wan_draft()
        path = self.c.control / "inventory.json"
        atomic(path, ["invalid"])
        self.assertFalse(self.ops.view()["discovery"]["current"])
        self.assertTrue(path.exists())

    def test_diagnostics_observe_applied_connection_not_unapplied_tls_draft(self):
        self.fixture.plan(existing=True)
        view = self.c.view()
        settings = dict(view["settings"])
        settings["api_tls_verify"] = False
        self.c.save(
            {"revision": view["revision"], "settings": settings, "acknowledge_insecure_tls": True}
        )
        with patch(
            "management.runtime.doctor.run", return_value={"checks": [], "status": "UNKNOWN"}
        ) as run:
            result = self.ops.diagnostics()
        self.assertTrue(run.call_args.args[1]["api_tls_verify"])
        self.assertEqual(result["observed_revision"], self.c.state()["applied_revision"])
        self.assertEqual(result["saved_revision"], self.c.state()["revision"])

    def test_completed_jobs_record_requested_and_applied_revision(self):
        request = self.fixture.plan(existing=True)
        with patch.object(self.ops, "diagnostics", return_value={"checks": []}):
            job = self.ops.apply(request)
            result = self.fixture.wait(job["job_id"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["requested_revision"], request.revision)
        self.assertEqual(result["applied_revision"], self.c.state()["applied_revision"])
