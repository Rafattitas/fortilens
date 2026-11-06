"""In-place upgrade of a web-managed installation, in a temporary layout.

A fake host records service operations; no systemd, Docker or real installation
is touched, and nothing runs as root.
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from management import install, upgrade  # noqa: E402


class FakeHost:
    def __init__(self, registry, fail=None):
        self.registry = registry
        self.fail = fail
        self.calls = []

    def _maybe_fail(self, name):
        if self.fail == name:
            self.fail = None
            raise upgrade.UpgradeError(
                "The fortilens-usage service did not become healthy (unhealthy)."
            )

    def run(self, *args, **kwargs):
        self.calls.append(("run",) + args)

    def services(self):
        return {"fortilens-usage", "fortilens-portal", "grafana"}

    def image_exists(self, image):
        return True

    def tag(self, source, target):
        self.calls.append(("tag", source, target))

    def compose(self, *args, **kwargs):
        self.calls.append(("compose",) + args)

    def wait_healthy(self, service):
        self.calls.append(("healthy", service))
        self._maybe_fail("healthy:" + service)

    def systemctl(self, *args):
        self.calls.append(("systemctl",) + args)

    def wait_web(self, access):
        self.calls.append(("web",))
        self._maybe_fail("web")

    def validate(self, prefix):
        self._maybe_fail("validate")

    def build_venv(self, prefix, requirements):
        target = Path(prefix) / "venv.new"
        target.mkdir()
        (target / "marker").write_text("new")
        return target


def owned_put(path, data, mode=0o600, uid=0, gid=0):
    return ORIGINAL_PUT(path, data, mode=mode, uid=os.getuid(), gid=os.getgid())


ORIGINAL_PUT = install.put


@unittest.skipUnless(hasattr(os, "getuid"), "POSIX ownership is required")
class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.prefix, self.control, self.stack = (
            self.tmp / "opt",
            self.tmp / "control",
            self.tmp / "stack",
        )
        names = install.source_files(ROOT)
        for name in names:
            target = self.prefix / "src" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, target)
        # The installed version differs in one management file and in service code.
        (self.prefix / "src/management/app.py").write_text("# previous version\n")
        self.old_meta = {
            "data": "/var/lib/fortilens",
            "origin": "http://127.0.0.1:3030",
            "source": install.source_files(self.prefix / "src"),
        }
        (self.prefix / "installation.json").write_text(json.dumps(self.old_meta))
        (self.prefix / "venv").mkdir()
        (self.prefix / "venv/marker").write_text("old")
        (self.stack / "usage").mkdir(parents=True)
        for relative, origin in upgrade.STACK_CODE.items():
            if relative.startswith("usage/"):
                (self.stack / relative).write_bytes((ROOT / origin).read_bytes())
        (self.stack / "usage/usage_service.py").write_text("# previous worker\n")
        self.control.mkdir()
        self.registry = {
            "installation": str(self.stack),
            "control": str(self.control),
            "project": "fortilens-test",
            "compose_file": "compose.yaml",
            "management_port": 3030,
        }
        registry_file = self.tmp / "registry.json"
        registry_file.write_text(json.dumps(self.registry))
        unit = self.tmp / "web.service"
        unit.write_text(
            "ExecStart=/opt/fortilens/venv/bin/python -m management.app --host 127.0.0.1 --port 3030\n"
        )
        for patch in (
            mock.patch.object(upgrade, "REGISTRY", str(registry_file)),
            mock.patch.object(upgrade, "WEB_UNIT", str(unit)),
            mock.patch.object(upgrade.install, "put", owned_put),
            mock.patch.object(upgrade.os, "geteuid", lambda: 0),
            mock.patch.object(upgrade, "MIN_FREE", 0),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def journal(self):
        return json.loads(upgrade.journal_path(self.control).read_text())

    def test_upgrade_replaces_source_and_service_code_then_records_completion(self):
        host = FakeHost(self.registry)
        upgrade.upgrade(ROOT, self.prefix, host=host, now=1_800_000_000)
        self.assertEqual(
            (self.prefix / "src/management/app.py").read_bytes(),
            (ROOT / "management/app.py").read_bytes(),
        )
        self.assertEqual(
            (self.stack / "usage/usage_service.py").read_bytes(),
            (ROOT / "portable/usage_service.py").read_bytes(),
        )
        self.assertEqual(
            json.loads((self.prefix / "installation.json").read_text())["source"],
            install.source_files(ROOT),
        )
        journal = self.journal()
        self.assertEqual(
            (journal["status"], journal["stack"]), ("COMPLETE", ["usage/usage_service.py"])
        )
        # Only the worker is rebuilt; the portal shares its image and is recreated after it.
        compose = [c for c in host.calls if c[0] == "compose"]
        self.assertEqual(
            compose,
            [
                (
                    "compose",
                    "up",
                    "-d",
                    "--no-deps",
                    "--build",
                    "--force-recreate",
                    "fortilens-usage",
                ),
                ("compose", "up", "-d", "--no-deps", "--force-recreate", "fortilens-portal"),
            ],
        )
        self.assertIn(
            (
                "tag",
                "fortilens-usage:fortilens-test",
                "fortilens-usage:fortilens-test-pre-" + journal["id"],
            ),
            host.calls,
        )
        stops = host.calls.index(("systemctl", "stop", "fortilens-web", "fortilens-helper"))
        self.assertLess(
            stops, host.calls.index(("systemctl", "start", "fortilens-helper", "fortilens-web"))
        )
        # Unchanged requirements keep the existing environment.
        self.assertEqual((self.prefix / "venv/marker").read_text(), "old")
        self.assertIsNone(upgrade.upgrade(ROOT, self.prefix, host=FakeHost(self.registry)))

    def test_failed_service_restores_previous_source_code_and_image(self):
        host = FakeHost(self.registry, fail="healthy:fortilens-usage")
        with self.assertRaises(SystemExit) as raised:
            upgrade.upgrade(ROOT, self.prefix, host=host, now=1_800_000_000)
        self.assertIn("previous version was restored", str(raised.exception))
        self.assertEqual(
            (self.prefix / "src/management/app.py").read_text(), "# previous version\n"
        )
        self.assertEqual((self.stack / "usage/usage_service.py").read_text(), "# previous worker\n")
        self.assertEqual(json.loads((self.prefix / "installation.json").read_text()), self.old_meta)
        journal = self.journal()
        self.assertEqual((journal["status"], journal["failed_step"]), ("FAILED", "services"))
        self.assertIn(
            (
                "tag",
                "fortilens-usage:fortilens-test-pre-" + journal["id"],
                "fortilens-usage:fortilens-test",
            ),
            host.calls,
        )
        self.assertEqual(host.calls[-1], ("web",))

    def test_changed_requirements_swap_the_environment_and_rollback_returns_it(self):
        (self.prefix / "src/management/requirements.txt").write_text("fastapi==0.0.1\n")
        self.old_meta["source"] = install.source_files(self.prefix / "src")
        (self.prefix / "installation.json").write_text(json.dumps(self.old_meta))
        upgrade.upgrade(ROOT, self.prefix, host=FakeHost(self.registry), now=1_800_000_000)
        self.assertEqual((self.prefix / "venv/marker").read_text(), "new")
        self.assertTrue(self.journal()["venv_swapped"])
        upgrade.rollback(self.prefix, host=FakeHost(self.registry))
        self.assertEqual((self.prefix / "venv/marker").read_text(), "old")
        self.assertEqual(
            (self.prefix / "src/management/requirements.txt").read_text(), "fastapi==0.0.1\n"
        )
        self.assertEqual((self.stack / "usage/usage_service.py").read_text(), "# previous worker\n")
        self.assertEqual(self.journal()["status"], "ROLLED_BACK")
        with self.assertRaises(SystemExit):
            upgrade.rollback(self.prefix, host=FakeHost(self.registry))

    def test_failure_before_the_environment_swap_keeps_the_current_environment(self):
        (self.prefix / "venv.previous").mkdir()
        (self.prefix / "venv.previous/marker").write_text("older upgrade")
        (self.prefix / "src/management/requirements.txt").write_text("fastapi==0.0.1\n")
        self.old_meta["source"] = install.source_files(self.prefix / "src")
        (self.prefix / "installation.json").write_text(json.dumps(self.old_meta))
        host = FakeHost(self.registry)
        host.build_venv = mock.Mock(
            side_effect=upgrade.UpgradeError("A host operation failed: pip.")
        )
        with self.assertRaises(SystemExit):
            upgrade.upgrade(ROOT, self.prefix, host=host, now=1_800_000_000)
        self.assertEqual((self.prefix / "venv/marker").read_text(), "old")
        self.assertEqual(self.journal()["status"], "FAILED")

    def test_interrupted_upgrade_or_running_deployment_blocks_a_new_upgrade(self):
        (self.control / "upgrades").mkdir()
        upgrade.journal_path(self.control).write_text(
            json.dumps({"id": "20270115T080000Z", "status": "APPLYING", "stack": []})
        )
        with self.assertRaisesRegex(SystemExit, "interrupted"):
            upgrade.upgrade(ROOT, self.prefix, host=FakeHost(self.registry))
        upgrade.journal_path(self.control).unlink()
        (self.control / "plans/abc").mkdir(parents=True)
        (self.control / "plans/abc/plan.json").write_text(json.dumps({"status": "APPLYING"}))
        with self.assertRaisesRegex(SystemExit, "deployment is running"):
            upgrade.upgrade(ROOT, self.prefix, host=FakeHost(self.registry))
        self.assertEqual(
            (self.prefix / "src/management/app.py").read_text(), "# previous version\n"
        )

    def test_services_that_are_not_deployed_are_never_added(self):
        changes = upgrade.stack_changes(ROOT, self.stack)
        self.assertEqual(changes, ["usage/usage_service.py"])
        self.assertFalse((self.stack / "syslog").exists())

    def test_installer_points_existing_installations_to_the_upgrade(self):
        text = (ROOT / "management/install.py").read_text()
        self.assertIn("--upgrade", text)
        self.assertIn("upgrade.py", install.source_files(ROOT).__repr__())


if __name__ == "__main__":
    unittest.main()
