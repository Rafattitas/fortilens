"""CLI failures use synthetic files and never run host installation operations."""

import contextlib
import http.client
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))


def load_module(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


setup = load_module("cli_error_setup", "portable/setup.py")
upgrade = load_module("cli_error_upgrade", "portable/upgrade.py")
installer = load_module("cli_error_installer", "management/install.py")
release = load_module("cli_error_release", "tools/release.py")
checks = load_module("cli_error_checks", "tools/check_repository.py")
SECRET = "PRIVATE_ERROR_FIXTURE_DO_NOT_ECHO"


class CliErrorTests(unittest.TestCase):
    def test_installer_invalid_text_preserves_target_and_closes_any_new_descriptor(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "asset"
            target.write_bytes(b"preserved")
            opened = []
            original_open = installer.os.open

            def track_open(*args, **kwargs):
                descriptor = original_open(*args, **kwargs)
                opened.append(descriptor)
                return descriptor

            try:
                with patch.object(installer.os, "open", side_effect=track_open):
                    with self.assertRaises(UnicodeEncodeError):
                        installer.put(target, "\ud800")
                self.assertEqual(target.read_bytes(), b"preserved")
                for descriptor in opened:
                    with self.assertRaises(OSError):
                        installer.os.fstat(descriptor)
                self.assertEqual(list(Path(directory).glob(".asset.new-*")), [])
            finally:
                for descriptor in opened:
                    try:
                        installer.os.close(descriptor)
                    except OSError:
                        pass

    def test_installer_atomic_write_preserves_binary_assets_and_utf8_text(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "asset"
            for data in (b"wOF2\x00\xff\xfe\r\n", "source caf\u00e9\n"):
                with self.subTest(kind=type(data).__name__):
                    with patch.object(installer.os, "chown", create=True) as owner:
                        installer.put(target, data, mode=0o640, uid=123, gid=456)
                    expected = data if isinstance(data, bytes) else data.encode("utf-8")
                    self.assertEqual(target.read_bytes(), expected)
                    owner.assert_called_once()
                    self.assertEqual(owner.call_args.args[1:], (123, 456))
                    self.assertEqual(list(Path(directory).glob(".asset.new-*")), [])

    def test_setup_expected_failures_are_nonzero_and_do_not_echo_private_details(self):
        failures = (
            OSError(SECRET),
            PermissionError(SECRET),
            ValueError(SECRET),
            UnicodeError(SECRET),
            EOFError(SECRET),
            urllib.error.URLError(SECRET),
            http.client.IncompleteRead(SECRET.encode(), 10),
            subprocess.CalledProcessError(1, [SECRET], stderr=SECRET),
            subprocess.TimeoutExpired([SECRET], 1, output=SECRET),
        )
        for failure in failures:
            with self.subTest(kind=type(failure).__name__):
                output = io.StringIO()
                with (
                    patch.object(setup, "wizard", side_effect=failure),
                    contextlib.redirect_stderr(output),
                ):
                    self.assertEqual(setup.main(["init"]), 1)
                self.assertIn("Setup failed:", output.getvalue())
                self.assertNotIn(SECRET, output.getvalue())
                self.assertNotIn("Traceback", output.getvalue())

    def test_setup_http_status_and_trusted_domain_error_messages_are_preserved(self):
        import interfaces
        import upgrade as runtime_upgrade

        failures = (
            (
                urllib.error.HTTPError(
                    "https://example.invalid/?token=" + SECRET, 403, SECRET, {}, None
                ),
                "HTTP 403",
            ),
            (setup.SetupError("Choose a supported option"), "Choose a supported option"),
            (interfaces.InventoryError("unsupported", "Review inventory"), "Review inventory"),
            (runtime_upgrade.UpgradeError("Review the saved plan"), "Review the saved plan"),
        )
        for failure, expected in failures:
            output = io.StringIO()
            with (
                patch.object(setup, "wizard", side_effect=failure),
                contextlib.redirect_stderr(output),
            ):
                self.assertEqual(setup.main(["init"]), 1)
            self.assertIn(expected, output.getvalue())
            self.assertNotIn(SECRET, output.getvalue())

    def test_setup_cancellation_stays_nonzero_and_programming_errors_are_not_hidden(self):
        with (
            patch.object(setup, "wizard", side_effect=KeyboardInterrupt),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(setup.main(["init"]), 1)
        with patch.object(setup, "wizard", side_effect=RuntimeError("programming failure")):
            with self.assertRaises(RuntimeError):
                setup.main(["init"])

    def test_compose_failure_output_is_captured_and_operations_are_bounded(self):
        output = io.StringIO()
        failure = subprocess.CalledProcessError(1, ["docker"], output=SECRET, stderr=SECRET)
        with (
            patch.object(setup.subprocess, "run", side_effect=failure) as run,
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(output),
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                setup.run_compose(Path("."), "config", "--quiet", timeout=30)
        self.assertNotIn(SECRET, output.getvalue())
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)

    def test_failed_bundle_removes_only_its_new_output(self):
        import zipfile

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "candidate.zip"
            with patch.object(zipfile.ZipFile, "write", side_effect=OSError(SECRET)):
                with self.assertRaises(OSError):
                    setup.bundle(destination)
            self.assertFalse(destination.exists())
            destination.write_bytes(b"existing archive")
            with self.assertRaises(setup.SetupError):
                setup.bundle(destination)
            self.assertEqual(destination.read_bytes(), b"existing archive")

    def test_grafana_wrong_or_oversized_envelopes_fail_with_private_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "secrets").mkdir()
            (root / "secrets/grafana_admin_password").write_text(SECRET)
            config = {"grafana_bind": "127.0.0.1", "grafana_port": 3001}
            for value in (json.dumps(SECRET).encode(), b"x" * (16 * 1024 * 1024 + 1)):
                opener = MagicMock()
                opener.open.return_value.__enter__.return_value.read.return_value = value
                with patch.object(setup.urllib.request, "build_opener", return_value=opener):
                    with self.assertRaises(setup.SetupError) as error:
                        setup.grafana_request(root, config, "/api/health")
                self.assertNotIn(SECRET, str(error.exception))

    def test_unsupported_dashboard_or_discovery_payloads_do_not_mutate_services(self):
        for payload in ([], {"spec": SECRET}):
            with patch.object(setup, "grafana_request", return_value=payload) as request:
                with self.assertRaises(setup.SetupError):
                    setup.import_dashboard(Path("."), {})
                self.assertEqual(request.call_count, 1)
        with patch.object(
            setup, "grafana_request", return_value={"status": "success", "data": SECRET}
        ):
            with self.assertRaises(setup.SetupError) as error:
                setup.discover(Path("."), {})
            self.assertNotIn(SECRET, str(error.exception))

    def test_invalid_exporter_credential_structure_fails_before_inventory_access(self):
        args = MagicMock(metrics_only=False, inventory_file=None)
        config = {"fortigate_host": "example.invalid", "api_port": 443}
        with patch.object(setup, "load", return_value=[SECRET]):
            with self.assertRaises(setup.SetupError) as error:
                setup.interface_command(Path("."), config, args)
        self.assertNotIn(SECRET, str(error.exception))

    def test_upgrade_failed_atomic_write_cleans_new_temporary_file_only(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "site.json"
            target.write_bytes(b"original")
            with patch.object(upgrade.os, "replace", side_effect=PermissionError(SECRET)):
                with self.assertRaises(OSError):
                    upgrade.put(target, b"replacement")
            self.assertEqual(target.read_bytes(), b"original")
            pending = target.with_name(target.name + ".fortilens-new")
            self.assertFalse(pending.exists())
            pending.write_bytes(b"operator recovery file")
            with self.assertRaises(upgrade.UpgradeError):
                upgrade.put(target, b"replacement")
            self.assertEqual(pending.read_bytes(), b"operator recovery file")

    def test_upgrade_malformed_external_plan_fails_before_any_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / ".upgrades/usage-1"
            folder.mkdir(parents=True)
            plan = folder / "plan.json"
            cases = (
                SECRET,
                "[]",
                json.dumps({"files": [SECRET]}),
                json.dumps({"files": [{"path": "site.json", "after": "0" * 64}]}),
            )
            for case in cases:
                plan.write_text(case, encoding="utf-8")
                with self.subTest(case=case), self.assertRaises(upgrade.UpgradeError) as error:
                    upgrade.read_plan(root, "usage-1")
                self.assertNotIn(SECRET, str(error.exception))
            self.assertFalse((root / "site.json").exists())

    def test_installer_atomic_write_cleans_new_secret_temp_and_preserves_target(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "secret"
            target.write_text("original", encoding="utf-8")
            with patch.object(
                installer.os, "chown", side_effect=PermissionError(SECRET), create=True
            ):
                with self.assertRaises(OSError):
                    installer.put(target, "replacement")
            self.assertEqual(target.read_text(), "original")
            self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_installer_unavailable_commands_and_startup_failures_are_private(self):
        for failure in (
            OSError(SECRET),
            UnicodeError(SECRET),
            subprocess.TimeoutExpired([SECRET], 1),
        ):
            with patch.object(installer.subprocess, "run", side_effect=failure):
                with self.assertRaises(SystemExit) as error:
                    installer.run("synthetic-command")
                self.assertNotIn(SECRET, str(error.exception))
        with patch.object(installer, "_main", side_effect=OSError(SECRET)):
            with self.assertRaises(SystemExit) as error:
                installer.main()
            self.assertNotIn(SECRET, str(error.exception))
        with patch.object(installer, "_main", side_effect=SystemExit("trusted validation")):
            with self.assertRaises(SystemExit) as error:
                installer.main()
            self.assertEqual(str(error.exception), "trusted validation")
        with (
            patch.object(installer, "_main", side_effect=KeyboardInterrupt),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as error:
                installer.main()
            self.assertEqual(error.exception.code, 130)

    def test_installer_rejects_bad_metadata_but_accepts_a_fresh_resume_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            for value in (SECRET, "[]", json.dumps({"data": SECRET})):
                path.write_text(value, encoding="utf-8")
                with self.assertRaises(SystemExit) as error:
                    installer.saved_metadata(path, ("data", "origin", "source"))
                self.assertNotIn(SECRET, str(error.exception))
            value = {
                "data": "/var/lib/fortilens",
                "origin": "http://127.0.0.1:3030",
                "source": {"fixture": "a" * 64},
                "adopt": None,
                "project": None,
                "compose_file": "compose.yaml",
            }
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(installer.saved_metadata(path, tuple(value)), value)

    def test_installer_readiness_retries_nonobject_responses_and_closes_connections(self):
        connection = MagicMock()
        connection.getresponse.return_value.status = 200
        connection.getresponse.return_value.read.side_effect = [
            b"[]",
            b'{"independent_of_monitoring":true}',
        ]
        with (
            patch.object(installer.http.client, "HTTPConnection", return_value=connection),
            patch.object(installer.time, "sleep"),
        ):
            installer.wait_web("127.0.0.1", 3030, "http://127.0.0.1:3030", False)
        self.assertEqual(connection.close.call_count, 2)

    def test_release_expected_failures_and_installer_source_rejection_are_private(self):
        failures = (
            OSError(SECRET),
            ValueError(SECRET),
            subprocess.TimeoutExpired([SECRET], 1),
            SystemExit(SECRET),
        )
        for failure in failures:
            output = io.StringIO()
            with (
                patch.object(release, "build", side_effect=failure),
                contextlib.redirect_stderr(output),
            ):
                with self.assertRaises(SystemExit) as error:
                    release.main(["build", "--version", "v0.3.0"])
                self.assertEqual(error.exception.code, 1)
            self.assertNotIn(SECRET, output.getvalue())
            self.assertIn("Release validation failed", output.getvalue())

    def test_release_rejects_nonobject_archive_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / release.MANIFEST).write_text("[]", encoding="utf-8")
            with self.assertRaises(release.ReleaseError):
                release.source_state(root)

    def test_repository_read_failures_are_reported_without_contents_or_tracebacks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, check in (
                ("sample.py", checks.check_python),
                ("sample.md", checks.check_markdown),
            ):
                path = root / name
                path.write_bytes(b"\xff" + SECRET.encode())
                errors = check(root, [path])
                self.assertEqual(len(errors), 1)
                self.assertNotIn(SECRET, errors[0])
            output = io.StringIO()
            with (
                patch.object(checks, "source_files", side_effect=OSError(SECRET)),
                patch("sys.argv", ["check", "--root", str(root)]),
                contextlib.redirect_stderr(output),
            ):
                self.assertEqual(checks.main(), 1)
            self.assertNotIn(SECRET, output.getvalue())

    @unittest.skipUnless(
        importlib.util.find_spec("yaml"), "YAML checks require optional development dependencies"
    )
    def test_repository_invalid_yaml_and_action_types_do_not_echo_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / ".github/workflows/fixture.yml"
            path.parent.mkdir(parents=True)
            for text in (
                "password: " + SECRET + "\nbad: [\n",
                "jobs:\n  test:\n    steps:\n      - uses: [" + SECRET + "]\n",
            ):
                path.write_text(text, encoding="utf-8")
                errors = checks.check_yaml(root, [path])
                self.assertEqual(len(errors), 1)
                self.assertNotIn(SECRET, errors[0])


if __name__ == "__main__":
    unittest.main()
