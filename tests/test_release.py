"""Release payload completeness, reproducibility and compatibility checks."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
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
            self.assertEqual(counts["portable_files"], 23)
            for before, after in zip(first, second):
                self.assertEqual(before.read_bytes(), after.read_bytes())
            full = release.full_members(first[0])
            self.assertIn("management/static/app.js", full)
            self.assertIn("management/requirements.txt", full)
            self.assertIn("portable/templates/Dockerfile.usage", full)
            self.assertIn("install.sh", full)
            self.assertIn("tools/check_repository.py", full)
            manifest = json.loads(full[release.MANIFEST])
            for row in manifest["files"]:
                self.assertEqual(row["sha256"], hashlib.sha256(full[row["path"]]).hexdigest())
                self.assertEqual(row["size"], len(full[row["path"]]))
            with zipfile.ZipFile(first[1]) as archive:
                self.assertEqual(
                    {n: archive.read(n) for n in archive.namelist()}, release.portable_sources(ROOT)
                )

    def test_extracted_full_artifact_has_installer_inputs_and_existing_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            full, _ = release.build(ROOT, folder / "dist", "v0.3.0-test.1")
            extracted = folder / "extracted"
            for name, data in release.full_members(full).items():
                path = extracted / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            self.assertEqual(release.full_sources(extracted), release.full_sources(ROOT))
            for arguments in (
                ["portable/setup.py", "--help"],
                ["portable/setup.py", "init", "--help"],
                ["portable/setup.py", "prepare-upgrade", "--help"],
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
