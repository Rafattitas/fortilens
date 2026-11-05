"""Release payload completeness, reproducibility and compatibility checks."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import re
import secrets
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fortilens_release", ROOT / "tools/release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleaseTests(unittest.TestCase):
    def test_management_static_dependencies_are_in_installer_release_and_docker_context(self):
        stylesheet = (ROOT / "management/static/app.css").read_text(encoding="utf-8")
        fonts = re.findall(r'url\(["\']?(fonts/[^"\'()\s]+)', stylesheet)
        self.assertTrue(fonts, "The vendored management fonts must be discoverable")
        required = {"management/static/" + name for name in fonts}
        required.update({"management/static/fonts/OFL.txt", "management/static/grafana-clock.js"})
        spec = importlib.util.spec_from_file_location(
            "fortilens_asset_installer", ROOT / "management/install.py"
        )
        installer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(installer)
        installed = installer.source_files(ROOT)
        packaged = release.full_sources(ROOT)
        context_rules = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())
        for name in sorted(required):
            with self.subTest(resource=name):
                self.assertIn(name, installed)
                self.assertIn(name, packaged)
                self.assertEqual(packaged[name], (ROOT / name).read_bytes())
                self.assertIn("!" + name, context_rules)

    def test_versions_are_semantic_and_cannot_inject_paths(self):
        for value in ("v0.3.0", "v1.2.3-rc.1", "v1.0.0+build.2"):
            self.assertEqual(release.version_value(value), value)
        for value in (
            "../v1.2.3",
            "v01.2.3",
            "1.2.3",
            "v1.2",
            "v1.2.3-01",
            "v1.2.3/extra",
            "v1.2.3\n",
        ):
            with self.subTest(value=value), self.assertRaises(release.ReleaseError):
                release.version_value(value)

    def test_full_and_portable_payloads_are_exact_and_reproducible(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            first = release.build(ROOT, folder / "first", "v0.3.0-test.1")
            second = release.build(ROOT, folder / "second", "v0.3.0-test.1")
            counts = release.verify(ROOT, folder / "first", "v0.3.0-test.1")
            self.assertEqual(counts["portable_files"], 27)
            for before, after in zip(first, second):
                self.assertEqual(before.read_bytes(), after.read_bytes())
            full = release.full_members(first[0])
            self.assertIn("management/static/app.js", full)
            self.assertIn("management/static/grafana-clock.js", full)
            self.assertIn("tests/test_grafana_clock.py", full)
            self.assertIn("management/requirements.txt", full)
            self.assertIn("portable/templates/Dockerfile.usage", full)
            self.assertIn("portable/syslog_receiver.py", full)
            self.assertIn("portable/templates/Dockerfile.syslog", full)
            self.assertIn("tests/test_syslog_receiver.py", full)
            self.assertIn("install.sh", full)
            self.assertIn("tools/check_repository.py", full)
            adapter_files = (
                "docs/SYSLOG_NATIVE_UDP.md",
                "examples/rsyslog/fortilens-syslog.conf",
                "examples/rsyslog/fortilens-syslog.service",
            )
            for name in adapter_files:
                self.assertIn(name, full)
            manifest = json.loads(full[release.MANIFEST])
            for row in manifest["files"]:
                self.assertEqual(row["sha256"], hashlib.sha256(full[row["path"]]).hexdigest())
                self.assertEqual(row["size"], len(full[row["path"]]))
            with zipfile.ZipFile(first[1]) as archive:
                self.assertEqual(
                    {n: archive.read(n) for n in archive.namelist()}, release.portable_sources(ROOT)
                )
                self.assertIn("forti-monitor/syslog_receiver.py", archive.namelist())
                self.assertIn("forti-monitor/templates/Dockerfile.syslog", archive.namelist())
                for name in adapter_files:
                    self.assertFalse(
                        any(n.endswith("/" + Path(name).name) for n in archive.namelist()), name
                    )

    def test_extracted_full_artifact_has_installer_inputs_and_existing_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            full, _ = release.build(ROOT, folder / "dist", "v0.3.0-test.1")
            # Windows TEMP may be a junction; link checks compare resolved paths.
            extracted = (folder / "extracted").resolve()
            for name, data in release.full_members(full).items():
                path = extracted / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            self.assertEqual(release.full_sources(extracted), release.full_sources(ROOT))
            check_spec = importlib.util.spec_from_file_location(
                "fortilens_packaged_checks", extracted / "tools/check_repository.py"
            )
            checks = importlib.util.module_from_spec(check_spec)
            check_spec.loader.exec_module(checks)
            self.assertEqual(
                checks.check_markdown(
                    extracted, [path for path in extracted.rglob("*") if path.is_file()]
                ),
                [],
            )
            for arguments in (
                ["portable/setup.py", "--help"],
                ["portable/setup.py", "init", "--help"],
                ["portable/setup.py", "prepare-upgrade", "--help"],
                ["portable/syslog_receiver.py", "--help"],
                ["-m", "management.install", "--help"],
            ):
                result = subprocess.run(
                    [sys.executable, *arguments],
                    cwd=extracted,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)
            # Run a real accounting regression module from packaged source, with
            # synthetic temporary databases; this never starts Docker/services.
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    "test_accounting.py",
                ],
                cwd=extracted,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("OK", result.stderr)

    def test_both_extracted_distributions_can_prepare_native_receiver_without_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            full, portable = release.build(ROOT, folder / "dist", "v0.3.0-test.1")
            extracted = folder / "full"
            for name, data in release.full_members(full).items():
                path = extracted / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            installer_spec = importlib.util.spec_from_file_location(
                "fortilens_packaged_installer", extracted / "management/install.py"
            )
            installer = importlib.util.module_from_spec(installer_spec)
            installer_spec.loader.exec_module(installer)
            runtime = installer.source_files(extracted)
            self.assertIn("portable/syslog_receiver.py", runtime)
            self.assertIn("portable/templates/Dockerfile.syslog", runtime)
            self.assertNotIn("tests/test_syslog_receiver.py", runtime)

            portable_root = folder / "portable"
            with zipfile.ZipFile(portable) as archive:
                for name in archive.namelist():
                    path = portable_root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(archive.read(name))
            token = "synthetic-artifact-receiver-token"
            token_file = folder / "token"
            token_file.write_text(token, encoding="utf-8")
            config_file = folder / "native.json"
            config_file.write_text(
                json.dumps(
                    {
                        "fortigate_host": "192.0.2.1",
                        "syslog_format": "fortigate",
                        "syslog_protocol": "udp",
                        "syslog_port": 5514,
                        "accounting_enabled": True,
                        "accounting_timezone": "UTC",
                    }
                ),
                encoding="utf-8",
            )
            for label, source in (
                ("full", extracted / "portable"),
                ("portable", portable_root / release.PORTABLE_PREFIX),
            ):
                with self.subTest(distribution=label):
                    output = folder / (label + "-installation")
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(source / "setup.py"),
                            "init",
                            "--config",
                            str(config_file),
                            "--token-file",
                            str(token_file),
                            "--output",
                            str(output),
                        ],
                        cwd=source,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=30,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn(token, result.stdout + result.stderr)
                    self.assertEqual(
                        (output / "syslog/syslog_receiver.py").read_bytes(),
                        (source / "syslog_receiver.py").read_bytes(),
                    )
                    self.assertEqual(
                        (output / "syslog/Dockerfile").read_bytes(),
                        (source / "templates/Dockerfile.syslog").read_bytes(),
                    )
                    compose = json.loads((output / "compose.yaml").read_text(encoding="utf-8"))
                    receiver = compose["services"]["fortilens-syslog"]
                    self.assertEqual(receiver["ports"], ["0.0.0.0:5514:1514/udp"])
                    self.assertNotIn("secrets", receiver)
                    self.assertNotIn("ports", compose["services"]["alloy"])

    def test_real_full_extraction_can_rebuild_existing_portable_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            full, _ = release.build(ROOT, folder / "dist", "v0.3.0-test.1")
            # Validate members, then preserve actual archive metadata on
            # extraction. Copying only member bytes hid pre-1980 ZIP failures.
            release.full_members(full)
            with tarfile.open(full) as archive:
                options = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
                archive.extractall(folder / "extracted", **options)
            extracted = folder / "extracted" / release.FULL_PREFIX
            self.assertEqual(release.portable_sources(extracted), release.portable_sources(ROOT))

    def test_unlisted_private_files_never_enter_either_archive(self):
        # Build from an isolated source copy so hygiene fixtures cannot enter a
        # contributor's real installation or produce tracked private material.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            for name, data in release.full_sources(ROOT).items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            # The test itself ships in the full source archive. A fresh marker
            # distinguishes private fixtures from this public test source.
            secret = "synthetic-exclusion-" + secrets.token_hex(16)
            for name in (
                "portable/local/site.json",
                "portable/secrets/token",
                "management/auth.sqlite",
                "docs/private-notes.md",
                "customer/export.csv",
            ):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(secret)
            archives = release.build(root, Path(directory) / "dist", "v0.3.0-test.1")
            for data in release.full_members(archives[0]).values():
                self.assertNotIn(secret.encode(), data)
            with zipfile.ZipFile(archives[1]) as archive:
                for name in archive.namelist():
                    self.assertNotIn(secret.encode(), archive.read(name))

    def test_missing_required_runtime_file_fails_before_creating_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "management").mkdir()
            (root / "management/install.py").write_bytes(
                (ROOT / "management/install.py").read_bytes()
            )
            with self.assertRaises(SystemExit):
                release.full_sources(root)

    def test_existing_outputs_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            release.build(ROOT, folder, "v0.3.0-test.1")
            before = {path.name: path.read_bytes() for path in folder.iterdir()}
            with self.assertRaises(release.ReleaseError):
                release.build(ROOT, folder, "v0.3.0-test.1")
            self.assertEqual(before, {path.name: path.read_bytes() for path in folder.iterdir()})

    def test_checksum_or_payload_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            full, _ = release.build(ROOT, folder, "v0.3.0-test.1")
            original = full.read_bytes()
            full.write_bytes(original + b"tampered")
            with self.assertRaises(release.ReleaseError):
                release.verify(ROOT, folder, "v0.3.0-test.1")
            full.write_bytes(original)
            files = release.full_members(full)
            files["management/app.py"] += b"\n# tampered\n"
            changed = folder / "changed.tar.gz"
            release.write_full(changed, files)
            full.write_bytes(changed.read_bytes())
            names = release.artifact_names("v0.3.0-test.1")
            checksums = "".join(
                release.digest((folder / name).read_bytes()) + "  " + name + "\n" for name in names
            )
            (folder / "SHA256SUMS.txt").write_text(checksums, encoding="ascii")
            with self.assertRaises(release.ReleaseError):
                release.verify(ROOT, folder, "v0.3.0-test.1")

    def test_full_archive_rejects_paths_links_and_unexpected_modes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unsafe.tar.gz"
            for name, kind, mode in (
                ("../escape", tarfile.REGTYPE, 0o644),
                ("fortilens/link", tarfile.SYMTYPE, 0o644),
                ("fortilens/file", tarfile.REGTYPE, 0o777),
            ):
                with self.subTest(name=name):
                    with tarfile.open(path, "w:gz") as archive:
                        entry = tarfile.TarInfo(name)
                        entry.type, entry.mode, entry.size = kind, mode, 0
                        archive.addfile(entry, io.BytesIO())
                    with self.assertRaises(release.ReleaseError):
                        release.full_members(path)

    def test_source_rejects_traversal_and_symlink_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("../outside", "/absolute", "directory\\file"):
                with self.subTest(name=name), self.assertRaises(release.ReleaseError):
                    release.source_file(root, name)
            with (
                patch.object(Path, "is_symlink", return_value=True),
                self.assertRaises(release.ReleaseError),
            ):
                release.source_file(root, "file")


if __name__ == "__main__":
    unittest.main()
