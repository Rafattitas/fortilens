"""Characterization of the host installer's main flow (management/install.py).

``_main()`` needs root, Ubuntu, systemd and Docker. These tests replace only
the host boundary: commands (``run``), privileged writes (``put``), accounts
(``pwd``/``grp``), the port probe, the registration check and the readiness
wait. They record everything ``_main`` asks of the host, in order: commands,
written files with mode, owner and content, printed output, or the refusal
message. ``tests/fixtures/install_flow.json`` pins today's behavior so the
function can be restructured with identical results. Regenerate it only for an
intended behavior change:

    FORTILENS_UPDATE_GOLDEN=1 python -m unittest tests.test_install_flow

Everything here is synthetic; nothing touches the real host.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from management import install  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "install_flow.json"
UPDATE = os.environ.get("FORTILENS_UPDATE_GOLDEN") == "1"
SOURCE = {"management/__init__.py": "0" * 64}
UBUNTU = 'NAME="Ubuntu"\nID=ubuntu\nVERSION_ID="24.04"\n'
ORIGINAL_STAT = Path.stat
ORIGINAL_READ_TEXT = Path.read_text
ORIGINAL_METADATA = install.saved_metadata


class Host:
    """A synthetic Ubuntu host that records what the installer asks of it."""

    def __init__(self, base, options):
        self.base = base
        self.options = options
        self.events = []
        self.files = {}
        self.tokens = itertools.count()
        self.account_created = options.get("account", True)
        self.registry = options.get("registry")

    def norm(self, value):
        text = str(value)
        stack = self.stack_hint
        if stack:
            project = "fortilens-" + hashlib.sha256(stack.encode()).hexdigest()[:10]
            text = text.replace(project, "<DEFAULT_PROJECT>")
        return (
            text.replace(str(self.base), "<BASE>")
            .replace(str(ROOT), "<SOURCE>")
            .replace(sys.executable, "<PYTHON>")
        )

    @property
    def stack_hint(self):
        return str(self.base / "data/control/stack")

    # Host boundary --------------------------------------------------------
    def run(self, *args, check=True):
        self.events.append(
            ["run", *[self.norm(a) for a in args]] + ([] if check else ["unchecked"])
        )
        if self.options.get("run_error") and args[:1] == ("docker",):
            raise OSError("synthetic host failure")
        code = 0
        if args[:4] == ("systemctl", "is-active", "--quiet", "fortilens-web") and len(args) == 4:
            code = 0 if self.options.get("web_active") else 3
        if args[:1] == ("useradd",):
            self.account_created = True
        return subprocess.CompletedProcess(list(args), code, "", "")

    def put(self, path, data, mode=0o600, uid=0, gid=0):
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        name = self.norm(path)
        self.events.append(["put", name, oct(mode), uid, gid])
        self.files[name] = self.norm(text)
        if str(path) == "/etc/fortilens/registry.json":
            self.registry = json.loads(text)

    def getpwnam(self, name):
        if not self.account_created:
            raise KeyError(name)
        return types.SimpleNamespace(
            pw_uid=990, pw_gid=990 if not self.options.get("docker_gid") else 999
        )

    def getgrnam(self, name):
        if name != "docker" or self.options.get("no_docker_group"):
            raise KeyError(name)
        members = ["fortilens"] if self.options.get("web_in_docker") else []
        return types.SimpleNamespace(gr_mem=members, gr_gid=999)

    def saved_metadata(self, path, required):
        if str(path) == "/etc/fortilens/registry.json":
            return dict(self.registry)
        return ORIGINAL_METADATA(path, required)

    def socket(self, family, kind):
        host = self

        class Probe:
            def bind(self, address):
                host.events.append(["probe", list(address)])
                if host.options.get("port_busy"):
                    raise OSError("synthetic: address in use")

            def close(self):
                pass

        return Probe()

    def validate(self, command, **kwargs):
        self.events.append(["validate-registration", self.norm(command[0])])
        return subprocess.CompletedProcess(command, 1 if self.options.get("invalid") else 0, "", "")

    def wait_web(self, bind, port, origin, tls):
        self.events.append(["wait-web", bind, port, origin, tls])

    def chown(self, path, uid, gid):
        self.events.append(["chown", self.norm(path), uid, gid])

    def stat(self, path, *args, **kwargs):
        # The runner is not root: report test folders as root-owned, as on a host.
        result = ORIGINAL_STAT(path, *args, **kwargs)
        if str(path).startswith(str(self.base)):
            values = list(tuple(result)[:10])
            values[4] = values[5] = 0
            return os.stat_result(values)
        return result

    def read_text(self, path, *args, **kwargs):
        if str(path) == "/etc/os-release":
            return self.options.get("os_release", UBUNTU)
        return ORIGINAL_READ_TEXT(path, *args, **kwargs)


# name -> (arguments with {P}/{D}/{A}/{C}/{K} placeholders, host options)
SCENARIOS = {
    "fresh-loopback": (["--prefix", "{P}", "--data", "{D}"], {}),
    "fresh-new-account-and-dependencies": (
        ["--prefix", "{P}", "--data", "{D}", "--install-dependencies"],
        {"account": False},
    ),
    "fresh-tls-lan": (
        [
            "--prefix",
            "{P}",
            "--data",
            "{D}",
            "--bind",
            "192.0.2.10",
            "--tls-cert",
            "{C}",
            "--tls-key",
            "{K}",
        ],
        {},
    ),
    "fresh-tls-named-origin": (
        [
            "--prefix",
            "{P}",
            "--data",
            "{D}",
            "--bind",
            "0.0.0.0",
            "--port",
            "8443",
            "--origin",
            "https://monitor.example.test:8443",
            "--tls-cert",
            "{C}",
            "--tls-key",
            "{K}",
            "--dashboard-uid",
            "custom-dashboard",
        ],
        {},
    ),
    "fresh-ipv6-loopback": (["--prefix", "{P}", "--data", "{D}", "--bind", "::1"], {}),
    "adopt-existing": (
        [
            "--prefix",
            "{P}",
            "--data",
            "{D}",
            "--adopt",
            "{A}",
            "--project",
            "site-monitor",
            "--compose-file",
            "compose.yaml",
        ],
        {"adopt": True},
    ),
    "web-already-active": (["--prefix", "{P}", "--data", "{D}"], {"web_active": True}),
    "resume-interrupted": (["--prefix", "{P}", "--data", "{D}"], {"partial": {}}),
    "rerun-installed": (["--prefix", "{P}", "--data", "{D}"], {"previous": True}),
    "upgrade-dispatch": (["--upgrade", "--prefix", "{P}"], {}),
    "rollback-dispatch": (["--rollback-upgrade", "--prefix", "{P}"], {}),
    "refuse-not-root": (["--prefix", "{P}", "--data", "{D}"], {"euid": 1000}),
    "refuse-not-linux": (["--prefix", "{P}", "--data", "{D}"], {"platform": "win32"}),
    "refuse-not-ubuntu": (["--prefix", "{P}", "--data", "{D}"], {"os_release": "ID=debian\n"}),
    "refuse-not-amd64": (["--prefix", "{P}", "--data", "{D}"], {"machine": "aarch64"}),
    "refuse-missing-docker": (["--prefix", "{P}", "--data", "{D}"], {"missing": "docker"}),
    "refuse-port-range": (["--prefix", "{P}", "--data", "{D}", "--port", "80"], {}),
    "refuse-bind-name": (["--prefix", "{P}", "--data", "{D}", "--bind", "localhost"], {}),
    "refuse-lan-without-tls": (["--prefix", "{P}", "--data", "{D}", "--bind", "0.0.0.0"], {}),
    "refuse-cert-without-key": (["--prefix", "{P}", "--data", "{D}", "--tls-cert", "{C}"], {}),
    "refuse-origin-path": (
        ["--prefix", "{P}", "--data", "{D}", "--origin", "http://127.0.0.1:3030/console"],
        {},
    ),
    "refuse-http-network-origin": (
        ["--prefix", "{P}", "--data", "{D}", "--origin", "http://monitor.example.test:3030"],
        {},
    ),
    "refuse-tls-http-origin": (
        [
            "--prefix",
            "{P}",
            "--data",
            "{D}",
            "--tls-cert",
            "{C}",
            "--tls-key",
            "{K}",
            "--origin",
            "http://127.0.0.1:3030",
        ],
        {},
    ),
    "refuse-bad-origin-port": (
        ["--prefix", "{P}", "--data", "{D}", "--origin", "http://127.0.0.1:99999"],
        {},
    ),
    "refuse-dashboard-uid": (
        ["--prefix", "{P}", "--data", "{D}", "--dashboard-uid", "bad uid"],
        {},
    ),
    "refuse-prefix-not-empty": (["--prefix", "{P}", "--data", "{D}"], {"prefix_file": True}),
    "refuse-previous-other-origin": (
        ["--prefix", "{P}", "--data", "{D}", "--port", "3031"],
        {"previous": True},
    ),
    "refuse-previous-other-source": (
        ["--prefix", "{P}", "--data", "{D}"],
        {"previous": True, "previous_source": {"management/__init__.py": "1" * 64}},
    ),
    "refuse-partial-other-options": (
        ["--prefix", "{P}", "--data", "{D}", "--port", "3031"],
        {"partial": {}},
    ),
    "refuse-partial-other-source": (
        ["--prefix", "{P}", "--data", "{D}"],
        {"partial": {"source": {"management/__init__.py": "2" * 64}}},
    ),
    "refuse-partial-other-adoption": (
        ["--prefix", "{P}", "--data", "{D}", "--project", "another-project"],
        {"partial": {}},
    ),
    "refuse-port-in-use": (["--prefix", "{P}", "--data", "{D}"], {"port_busy": True}),
    "refuse-adopt-without-project": (
        ["--prefix", "{P}", "--data", "{D}", "--adopt", "{A}"],
        {"adopt": True},
    ),
    "refuse-invalid-project": (
        ["--prefix", "{P}", "--data", "{D}", "--project", "Bad_Project!"],
        {},
    ),
    "refuse-compose-outside": (
        ["--prefix", "{P}", "--data", "{D}", "--compose-file", "../compose.yaml"],
        {},
    ),
    "refuse-adopt-missing-compose": (
        ["--prefix", "{P}", "--data", "{D}", "--adopt", "{A}", "--project", "site-monitor"],
        {},
    ),
    "refuse-web-user-in-docker": (["--prefix", "{P}", "--data", "{D}"], {"web_in_docker": True}),
    "refuse-web-group-is-docker": (["--prefix", "{P}", "--data", "{D}"], {"docker_gid": True}),
    "refuse-invalid-registration": (["--prefix", "{P}", "--data", "{D}"], {"invalid": True}),
    "refuse-registered-other-project": (
        ["--prefix", "{P}", "--data", "{D}", "--project", "another-project"],
        {"previous": True},
    ),
    "main-host-failure": (["--prefix", "{P}", "--data", "{D}"], {"run_error": True}),
}


@unittest.skipUnless(
    sys.platform.startswith("linux") and sys.version_info >= (3, 11),
    "The web installer runs on Linux with Python 3.11+, as it checks itself",
)
class InstallFlowCharacterization(unittest.TestCase):
    maxDiff = None

    def outcome(self, arguments, options):
        with tempfile.TemporaryDirectory(prefix="fortilens-install-flow-") as directory:
            base = Path(directory).resolve()
            return self.recorded(base, arguments, options)

    def recorded(self, base, arguments, options):
        host = Host(base, options)
        prefix, data, adopt = base / "opt", base / "data", base / "existing"
        cert, key = base / "cert.pem", base / "key.pem"
        cert.write_text("SYNTHETIC CERTIFICATE\n")
        key.write_text("SYNTHETIC KEY\n")
        if options.get("adopt"):
            adopt.mkdir()
            (adopt / "compose.yaml").write_text("services: {}\n")
        source = dict(options.get("previous_source", SOURCE))
        origin = "http://127.0.0.1:3030"
        if options.get("previous"):
            prefix.mkdir()
            (prefix / "installation.json").write_text(
                json.dumps({"data": str(data), "origin": origin, "source": source})
            )
            host.registry = {
                "installation": str(data / "control/stack"),
                "project": "fortilens-registered",
            }
        if "partial" in options:
            prefix.mkdir()
            record = {
                "data": str(data),
                "origin": origin,
                "source": dict(SOURCE),
                "adopt": None,
                "project": None,
                "compose_file": "compose.yaml",
                **options["partial"],
            }
            (prefix / "installing.json").write_text(json.dumps(record))
        if options.get("prefix_file"):
            prefix.mkdir()
            (prefix / "unrelated.txt").write_text("keep me")
        values = {"P": prefix, "D": data, "A": adopt, "C": cert, "K": key}
        argv = ["install.py"] + [a.format(**values) for a in arguments]
        upgrade = sys.modules.get("management.upgrade")
        if upgrade is None:
            from management import upgrade
        fake_ssl = types.SimpleNamespace(
            SSLContext=lambda protocol: types.SimpleNamespace(
                load_cert_chain=lambda c, k: host.events.append(["load-cert", host.norm(c)])
            ),
            PROTOCOL_TLS_SERVER=object(),
        )
        missing = options.get("missing")
        output = io.StringIO()
        with contextlib.ExitStack() as stack:
            enter = stack.enter_context
            enter(patch.object(sys, "argv", argv))
            enter(patch.object(install.sys, "platform", options.get("platform", "linux")))
            enter(patch.object(install.os, "geteuid", lambda: options.get("euid", 0), create=True))
            enter(patch.object(install.os, "chown", host.chown, create=True))
            enter(
                patch.object(install.platform, "machine", lambda: options.get("machine", "x86_64"))
            )
            enter(
                patch.object(
                    install.shutil,
                    "which",
                    lambda name: None if name == missing else "/usr/bin/" + name,
                )
            )
            enter(patch.object(install, "run", host.run))
            enter(patch.object(install, "put", host.put))
            enter(patch.object(install, "saved_metadata", host.saved_metadata))
            enter(patch.object(install, "source_files", lambda path: dict(SOURCE)))
            enter(patch.object(install, "wait_web", host.wait_web))
            enter(patch.object(install, "ssl", fake_ssl))
            enter(patch.object(install.socket, "socket", host.socket))
            enter(patch.object(install.subprocess, "run", host.validate))
            enter(
                patch.object(
                    install.secrets, "token_urlsafe", lambda n=None: "token-%d" % next(host.tokens)
                )
            )
            enter(patch.object(Path, "stat", lambda p, *a, **k: host.stat(p, *a, **k)))
            enter(patch.object(Path, "read_text", lambda p, *a, **k: host.read_text(p, *a, **k)))
            enter(patch.dict(sys.modules, {"pwd": types.SimpleNamespace(getpwnam=host.getpwnam)}))
            enter(patch.dict(sys.modules, {"grp": types.SimpleNamespace(getgrnam=host.getgrnam)}))
            enter(
                patch.object(
                    upgrade,
                    "upgrade",
                    lambda src, pfx: host.events.append(
                        ["upgrade", host.norm(src), host.norm(pfx)]
                    ),
                )
            )
            enter(
                patch.object(
                    upgrade,
                    "rollback",
                    lambda pfx: host.events.append(["rollback", host.norm(pfx)]),
                )
            )
            enter(contextlib.redirect_stdout(output))
            enter(contextlib.redirect_stderr(output))
            try:
                install.main()
                refusal = None
            except SystemExit as exit:
                refusal = str(exit.code)
        return {
            "events": host.events,
            "files": host.files,
            "output": host.norm(output.getvalue()).splitlines(),
            "refusal": refusal,
        }

    def test_installer_flow_matches_the_recorded_results(self):
        actual = {name: self.outcome(*scenario) for name, scenario in SCENARIOS.items()}
        if UPDATE:
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            self.skipTest("Recorded " + str(GOLDEN.relative_to(ROOT)))
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(sorted(expected), sorted(actual))
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                self.assertEqual(expected[name], actual[name])

    def test_refusal_scenarios_refuse_and_the_others_finish(self):
        expected = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
        for name, result in expected.items():
            with self.subTest(scenario=name):
                refused = result["refusal"] is not None
                self.assertEqual(refused, name.startswith(("refuse-", "main-")), result["refusal"])


if __name__ == "__main__":
    unittest.main()
