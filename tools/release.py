#!/usr/bin/env python3
"""Build and verify allowlisted full and portable releases without publishing."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import io
import json
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
FULL_PREFIX = "fortilens"
PORTABLE_PREFIX = "forti-monitor"
MANIFEST = "RELEASE-MANIFEST.json"
ZIP_TIME = (1980, 1, 1, 0, 0, 0)
VERSION = re.compile(
    r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)

# An explicit source allowlist, never a recursive workspace/archive operation.
# Runtime files come from the installer's own reviewed list, checked below.
PUBLIC_FILES = (
    ".dockerignore",
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    ".github/dependabot.yml",
    ".github/pull_request_template.md",
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/documentation.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/ISSUE_TEMPLATE/fortios_compatibility.yml",
    ".github/workflows/audit.yml",
    ".github/workflows/ci.yml",
    ".github/workflows/demo.yml",
    ".github/workflows/release.yml",
    "AGENTS.md",
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "ROADMAP.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "install.sh",
    "pyproject.toml",
    "requirements-dev.txt",
    "portable/.gitignore",
    ".agents/skills/fortilens-maintainer/SKILL.md",
    ".agents/skills/fortilens-maintainer/agents/openai.yaml",
    "docs/assets/architecture.svg",
    "docs/assets/hero.svg",
    "docs/assets/dashboard-preview.svg",
    "examples/usage-audit.sample.json",
    "examples/rsyslog/fortilens-syslog.conf",
    "examples/rsyslog/fortilens-syslog.service",
    "docs/README.md",
    "docs/ACCOUNTING_ENGINE.md",
    "docs/OPTIONAL_ALERTS.md",
    "docs/TROUBLESHOOTING.md",
    "docs/UPGRADE.md",
    "docs/VERIFICATION.md",
    "docs/architecture/ACCOUNTING.md",
    "docs/architecture/ACCOUNTING_ENGINE.md",
    "docs/architecture/COMPACT_ACCOUNTING.md",
    "docs/architecture/ARCHITECTURE.md",
    "docs/history/CLEANUP_VALIDATION.md",
    "docs/development/ERROR_HANDLING.md",
    "docs/history/FRONTEND_DESIGN_REVIEW.md",
    "docs/getting-started/INSTALLATION.md",
    "docs/development/MAINTAINABILITY.md",
    "docs/architecture/MANAGEMENT_ARCHITECTURE.md",
    "docs/development/MANAGEMENT_VERIFICATION.md",
    "docs/operations/CAPACITY.md",
    "docs/operations/COMPATIBILITY.md",
    "docs/operations/OPTIONAL_ALERTS.md",
    "docs/history/PR_PROFESSIONALIZATION.md",
    "docs/development/PROJECT_CONTEXT.md",
    "docs/development/QA.md",
    "docs/development/RELEASING.md",
    "docs/development/REPAIR_VERIFICATION.md",
    "docs/history/RELIABILITY_RELEASE_PLAN.md",
    "docs/history/REPOSITORY_AUDIT.md",
    "docs/development/REPOSITORY_GOVERNANCE.md",
    "docs/operations/SYSLOG_NATIVE_UDP.md",
    "docs/operations/TROUBLESHOOTING.md",
    "docs/development/UI_DESIGN.md",
    "docs/getting-started/UNIFIED_CONSOLE.md",
    "docs/operations/UPGRADE.md",
    "docs/architecture/USAGE_QUALITY.md",
    "docs/development/VERIFICATION.md",
    "docs/getting-started/WEB_SETUP.md",
    "docs/design/screenshots/README.md",
    "docs/design/screenshots/manifest.json",
    "docs/design/screenshots/01-overview-dark.png",
    "docs/design/screenshots/02-overview-light.png",
    "docs/design/screenshots/03-overview-degraded.png",
    "docs/design/screenshots/04-usage-docked.png",
    "docs/design/screenshots/05-reports.png",
    "docs/design/screenshots/06-evidence.png",
    "docs/design/screenshots/07-internet.png",
    "docs/design/screenshots/08-system.png",
    "docs/design/screenshots/09-diagnostics.png",
    "docs/design/screenshots/10-settings.png",
    "docs/design/screenshots/11-first-run.png",
    "docs/design/screenshots/12-sign-in.png",
    "docs/design/screenshots/14-overview-narrow.png",
    "docs/design/screenshots/15-usage-narrow.png",
    "docs/design/screenshots/16-applications.png",
    "docs/design/screenshots/17-live.png",
    "tools/release.py",
    "tools/check_repository.py",
    "tools/capture_screenshots.cjs",
    "tools/build_demo.cjs",
    "tools/check_demo.cjs",
    "demo/fixtures.js",
    "demo/demo.js",
    "docs/development/DEMO.md",
    "tests/support/check_frontend.py",
    "tests/integration/browser_deployment.cjs",
    "tests/integration/fixture_api.py",
    "tests/integration/integration_live_fortigate.py",
    "tests/integration/integration_management.py",
    "tests/integration/integration_native_host.py",
    "tests/integration/integration_stack.py",
    "tests/support/preview_reports.py",
    "tests/test_accounting.py",
    "tests/test_compact_accounting.py",
    "tests/support/benchmark_accounting.py",
    "tests/support/benchmark_scale.py",
    "tests/support/compare_engines.py",
    "tests/test_accounting_migrate.py",
    "tests/test_alerts.py",
    "tests/test_cli_errors.py",
    "tests/test_configuration_ca.py",
    "tests/test_dashboard_style.py",
    "tests/test_doctor.py",
    "tests/test_frontend_errors.py",
    "tests/test_grafana_clock.py",
    "tests/test_interfaces.py",
    "tests/test_management.py",
    "tests/test_management_errors.py",
    "tests/test_native_host.py",
    "tests/test_portable_setup.py",
    "tests/test_portal.py",
    "tests/test_probe_diagnostics.py",
    "tests/test_repair_runtime.py",
    "tests/test_report_concurrency.py",
    "tests/test_release.py",
    "tests/test_repository_checks.py",
    "tests/test_runtime_errors.py",
    "tests/test_service_errors.py",
    "tests/test_storage_projection.py",
    "tests/test_storage_runtime.py",
    "tests/test_syslog_receiver.py",
    "tests/test_upgrade.py",
    "tests/test_usage_quality.py",
    "tests/test_usage_service.py",
    "tests/test_wan_health.py",
)


class ReleaseError(ValueError):
    """An incomplete, unsafe or inconsistent release input."""


def version_value(value: str) -> str:
    if not VERSION.fullmatch(value):
        raise ReleaseError("Use a Semantic Versioning tag such as v0.3.0 or v0.3.0-rc.1")
    return value


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_file(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise ReleaseError("Release inputs must stay inside the source root")
    path = root.joinpath(*relative.parts)
    if any(part.is_symlink() for part in (path, *path.parents) if part.is_relative_to(root)):
        raise ReleaseError("Release inputs must not contain symlinks")
    if not path.is_file():
        raise ReleaseError("A reviewed release input is missing: " + name)
    return path


def full_sources(root: Path) -> dict[str, bytes]:
    """Combine required installer sources with reviewed public/development files."""
    root = root.resolve()
    installer = source_file(root, "management/install.py")
    spec = importlib.util.spec_from_file_location("fortilens_release_installer", installer)
    if spec is None or spec.loader is None:
        raise ReleaseError("Cannot inspect the installer source allowlist")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    runtime = module.source_files(root)
    names = sorted(set(runtime) | set(PUBLIC_FILES))
    return {name: source_file(root, name).read_bytes() for name in names}


def portable_sources(root: Path) -> dict[str, bytes]:
    """Use the unchanged legacy CLI to select the portable payload."""
    with tempfile.TemporaryDirectory(prefix="fortilens-portable-release-") as folder:
        bundle = Path(folder) / "portable.zip"
        result = subprocess.run(
            [
                sys.executable,
                str(source_file(root, "portable/setup.py")),
                "bundle",
                "--destination",
                str(bundle),
            ],
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )
        if result.returncode:
            raise ReleaseError("The existing portable bundle command failed")
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or any(
                not name.startswith(PORTABLE_PREFIX + "/")
                or ".." in PurePosixPath(name).parts
                or "\\" in name
                or name.endswith("/")
                for name in names
            ):
                raise ReleaseError("The portable bundle returned an unsafe file list")
            return {name: archive.read(name) for name in sorted(names)}


def source_state(root: Path) -> dict[str, str | None]:
    """Record provenance without claiming uncommitted bytes are in a commit."""
    if (root / ".git").exists() and shutil.which("git"):
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        status = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if revision.returncode == status.returncode == 0:
            return {
                "revision": revision.stdout.strip(),
                "worktree": "modified" if status.stdout else "clean",
            }
    archived = root / MANIFEST
    if archived.is_file() and not archived.is_symlink():
        document = json.loads(archived.read_text(encoding="utf-8"))
        source = document.get("source") if isinstance(document, dict) else None
        if isinstance(source, dict) and set(source) == {"revision", "worktree"}:
            if source["worktree"] in {"clean", "modified", "unversioned"} and (
                source["revision"] is None
                or re.fullmatch(r"[0-9a-f]{40,64}", str(source["revision"]))
            ):
                return source
        raise ReleaseError("The extracted source provenance is invalid")
    return {"revision": None, "worktree": "unversioned"}


def manifest(version: str, files: dict[str, bytes], source: dict[str, str | None]) -> bytes:
    records = [
        {
            "path": name,
            "sha256": digest(data),
            "size": len(data),
            "mode": "0755" if name == "install.sh" else "0644",
        }
        for name, data in sorted(files.items())
    ]
    return (
        json.dumps(
            {"schema_version": 1, "version": version, "source": source, "files": records},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def write_full(path: Path, files: dict[str, bytes]) -> None:
    # Stable order, owner, modes and timestamps make source artifacts reproducible.
    with (
        path.open("xb") as output,
        gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed,
    ):
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(FULL_PREFIX + "/" + name)
                info.size = len(data)
                info.mode = 0o755 if name == "install.sh" else 0o644
                # Extracted sources must remain usable by the existing ZIP
                # builder (DOS dates start in 1980). Leave a day for negative
                # local UTC offsets; gzip's independent timestamp stays zero.
                info.mtime = 315619200  # 1980-01-02 UTC
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(data))


def write_portable(path: Path, files: dict[str, bytes]) -> None:
    # Only ZIP metadata is normalized; legacy paths and file bytes stay intact.
    with zipfile.ZipFile(path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=ZIP_TIME)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)


def artifact_names(version: str) -> tuple[str, str]:
    return "fortilens-" + version + "-full.tar.gz", "fortilens-" + version + ".zip"


def build(root: Path, directory: Path, version: str) -> tuple[Path, Path]:
    version = version_value(version)
    root, directory = root.resolve(), directory.resolve()
    files = full_sources(root)
    portable = portable_sources(root)
    files[MANIFEST] = manifest(version, files, source_state(root))
    names = artifact_names(version)
    if any((directory / name).exists() for name in (*names, "SHA256SUMS.txt")):
        raise ReleaseError("Release output already exists; choose a clean output directory")
    directory.mkdir(parents=True, exist_ok=True)
    # Stage complete artifacts before making them visible in the output directory.
    with tempfile.TemporaryDirectory(prefix=".fortilens-release-", dir=directory) as folder:
        stage = Path(folder)
        write_full(stage / names[0], files)
        write_portable(stage / names[1], portable)
        checksums = "".join(
            digest((stage / name).read_bytes()) + "  " + name + "\n" for name in names
        )
        (stage / "SHA256SUMS.txt").write_text(checksums, encoding="ascii", newline="\n")
        for name in (*names, "SHA256SUMS.txt"):
            (stage / name).replace(directory / name)
    return directory / names[0], directory / names[1]


def full_members(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, "r:gz") as archive:
        files = {}
        for item in archive.getmembers():
            parts = PurePosixPath(item.name).parts
            if (
                not item.isfile()
                or len(parts) < 2
                or parts[0] != FULL_PREFIX
                or ".." in parts
                or "\\" in item.name
            ):
                raise ReleaseError("The full archive contains an unsafe member")
            name = "/".join(parts[1:])
            if name in files or item.mode != (0o755 if name == "install.sh" else 0o644):
                raise ReleaseError("The full archive has duplicate files or unexpected modes")
            stream = archive.extractfile(item)
            if stream is None:
                raise ReleaseError("A full archive member cannot be read")
            files[name] = stream.read()
        return files


def verify(root: Path, directory: Path, version: str) -> dict[str, int]:
    version = version_value(version)
    names = artifact_names(version)
    expected_checksums = "".join(
        digest((directory / name).read_bytes()) + "  " + name + "\n" for name in names
    )
    if (directory / "SHA256SUMS.txt").read_text(encoding="ascii") != expected_checksums:
        raise ReleaseError("Release checksums do not match the two expected artifacts")
    expected_full = full_sources(root)
    expected_full[MANIFEST] = manifest(version, expected_full, source_state(root))
    actual_full = full_members(directory / names[0])
    if actual_full != expected_full:
        raise ReleaseError("The full archive differs from its reviewed source inputs")
    expected_portable = portable_sources(root)
    with zipfile.ZipFile(directory / names[1]) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ReleaseError("The portable archive contains duplicate members")
        actual_portable = {name: archive.read(name) for name in archive.namelist()}
    if actual_portable != expected_portable:
        raise ReleaseError("The portable archive differs from the existing bundle payload")
    return {"full_files": len(actual_full), "portable_files": len(actual_portable)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build_parser = commands.add_parser(
        "build", help="Build local artifacts only; never creates a tag or release"
    )
    build_parser.add_argument("--version", required=True)
    build_parser.add_argument("--output", type=Path, default=Path("dist"))
    verify_parser = commands.add_parser(
        "verify", help="Compare checksums, paths and bytes against the source checkout"
    )
    verify_parser.add_argument("--version", required=True)
    verify_parser.add_argument("--directory", type=Path, default=Path("dist"))
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            for path in build(ROOT, args.output, args.version):
                print("Built: " + str(path))
        else:
            counts = verify(ROOT, args.directory, args.version)
            print("Verified full and portable artifacts: " + json.dumps(counts, sort_keys=True))
    except ReleaseError as exc:
        parser.exit(1, "Release validation failed: " + str(exc) + "\n")
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, subprocess.SubprocessError):
        parser.exit(
            1,
            "Release validation failed: Required source, archive or host operation is unavailable or invalid. Review file permissions, storage and supported inputs; private details are suppressed.\n",
        )
    except SystemExit as exc:
        if exc.code in (None, 0):
            raise
        parser.exit(
            1,
            "Release validation failed: The reviewed installer source is incomplete or unsafe; no release was published.\n",
        )
    except KeyboardInterrupt:
        parser.exit(
            130,
            "Release validation cancelled. Review the candidate output directory before retrying.\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
