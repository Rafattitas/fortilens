"""Characterization of Prepare for registered installations.

Pins the exact candidate files that ``Configuration.prepare`` (and its
``_patch`` step) produces today, or the code of the refusal, for a matrix of
settings changes on fresh, managed and adopted installations. It exists so that
the code can be restructured with byte-identical results.

The expected results live in ``tests/fixtures/configuration_patch.json`` as one
SHA-256 per candidate file, in the order the plan lists them. Regenerate them
only for an intended behavior change, and review the reason in the same change:

    FORTILENS_UPDATE_GOLDEN=1 python -m unittest tests.test_configuration_patch

All hosts, tokens and certificates here are synthetic.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
try:
    import yaml

    from management.common import Problem, setup
    from management.configuration import Configuration
    from test_configuration_ca import CA
except ImportError:
    raise unittest.SkipTest("Management tests require management/requirements-test.txt") from None

GOLDEN = ROOT / "tests" / "fixtures" / "configuration_patch.json"
UPDATE = os.environ.get("FORTILENS_UPDATE_GOLDEN") == "1"
TOKEN = "synthetic-patch-token"


@dataclass
class Step:
    """One saved revision. Every step but the last is prepared and applied."""

    settings: dict = field(default_factory=dict)
    token: str | None = None
    ca: str | None = None
    acknowledge_insecure_tls: bool = False
    # Edits an installed file before this step's save, as an operator might.
    mutate: Callable[[Configuration], None] | None = None


def merge(base, updates):
    result = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = value
    return result


def edit_compose(change):
    def mutate(c):
        path = c.root / c.compose_name
        compose = yaml.safe_load(path.read_text(encoding="utf-8"))
        change(compose)
        path.write_text(yaml.safe_dump(compose, sort_keys=False), encoding="utf-8")

    return mutate


def edit_bound(service, container_path, change):
    def mutate(c):
        path = c.bound_file(c.compose(), service, container_path)
        path.write_text(change(path.read_text(encoding="utf-8")), encoding="utf-8")

    return mutate


def edit_prometheus(change):
    def text(value):
        data = yaml.safe_load(value)
        change(data)
        return yaml.safe_dump(data, sort_keys=False)

    return edit_bound("prometheus", "/etc/prometheus/prometheus.yml", text)


def set_root_url(compose):
    compose["services"]["grafana"].setdefault("environment", {})
    compose["services"]["grafana"]["environment"]["GF_SERVER_ROOT_URL"] = (
        "https://monitor.example.test:3001/grafana/"
    )


def drop_receiver_port(compose):
    compose["services"]["alloy"]["ports"] = []


def mapping_ports(service):
    def change(compose):
        compose["services"][service]["ports"] = [{"target": 1514, "published": 1514}]

    return change


def extra_handoff(compose):
    compose["services"]["alloy"]["ports"].append("127.0.0.1:6514:1514/tcp")


def custom_receiver(compose):
    compose["services"]["fortilens-syslog"]["environment"] = {"CUSTOM": "1"}


def custom_scrape(data):
    for job in data["scrape_configs"]:
        if job.get("job_name") == "fortilens-syslog":
            job["scrape_interval"] = "5s"


def without_protocol(text):
    return "\n".join(line for line in text.splitlines() if "protocol" not in line) + "\n"


def without_receiver_limits(text):
    kept = ("use_incoming_timestamp", "max_message_length")
    lines = [line for line in text.splitlines() if not any(k in line for k in kept)]
    return "\n".join(lines) + "\n"


def add_ports(service, *bindings):
    def change(compose):
        compose["services"][service].setdefault("ports", []).extend(bindings)

    return change


def set_ports(service, *bindings):
    def change(compose):
        compose["services"][service]["ports"] = list(bindings)

    return change


EXPORTER_AUTH = ("fortigate-exporter", "/config/fortigate-key.yaml")


def known_target(text):
    auth = yaml.safe_load(text)
    target = setup.target_url({**setup.DEFAULTS, "fortigate_host": "192.0.2.5"})
    auth[target] = dict(next(iter(auth.values())))
    return yaml.safe_dump(auth)


def unreadable_record(text):
    auth = yaml.safe_load(text)
    return yaml.safe_dump({key: "not-a-record" for key in auth})


NATIVE = {"syslog_format": "fortigate", "syslog_protocol": "udp", "syslog_port": 5514}
HOST = {**NATIVE, "syslog_network": "host"}
LEGACY = {
    "syslog_format": "rfc5424",
    "syslog_protocol": "tcp",
    "syslog_port": 1514,
    "syslog_network": "bridge",
}
NO_ACCOUNTING = {"monitoring": {"accounting": False, "vlans": False, "websites": False}}

# name -> (installation kind, steps). "fresh" has nothing installed yet,
# "managed" starts from an applied web-managed installation, "adopted" from a
# CLI-rendered one with accounting and the portal, "adopted-native" from the
# same with the CLI native receiver, and "adopted-minimal" (optionally "-ca")
# from one without accounting or the portal. Names starting with "refuse-" must
# end in a refusal.
SCENARIOS = {
    "fresh": ("fresh", [Step()]),
    "fresh-native": ("fresh", [Step(NATIVE)]),
    "fresh-exclusions": ("fresh", [Step({"optional_exporter_exclusions": ["System/SensorInfo"]})]),
    "managed-display-name": ("managed", [Step({"site_name": "Renamed site"})]),
    "managed-retention-interval": (
        "managed",
        [
            Step(
                {
                    "monitoring_interval": 30,
                    "retention": {"loki_days": 90, "prometheus_days": 120},
                }
            )
        ],
    ),
    "managed-interval-only-global": ("managed", [Step({"monitoring_interval": 120})]),
    "managed-ports": ("managed", [Step({"grafana_port": 3101, "usage_port": 3102})]),
    "managed-root-url-port": (
        "managed",
        [Step({"grafana_port": 3101}, mutate=edit_compose(set_root_url))],
    ),
    "managed-alloy-extra-port": (
        "managed",
        [
            Step(
                {"syslog_port": 5514},
                mutate=edit_compose(add_ports("alloy", "0.0.0.0:9999:9999/tcp")),
            )
        ],
    ),
    "managed-short-grafana-binding": (
        "managed",
        [Step({"grafana_port": 3101}, mutate=edit_compose(set_ports("grafana", "3001:3000")))],
    ),
    "managed-syslog-udp-port": (
        "managed",
        [Step({"syslog_protocol": "udp", "syslog_port": 5514})],
    ),
    "managed-native-bridge": ("managed", [Step(NATIVE)]),
    "managed-native-host": ("managed", [Step(HOST)]),
    "managed-native-bridge-to-host": ("managed", [Step(NATIVE), Step(HOST)]),
    "managed-native-host-to-bridge": (
        "managed",
        [Step(HOST), Step({**NATIVE, "syslog_port": 7514})],
    ),
    "managed-native-host-port": ("managed", [Step(HOST), Step({**HOST, "syslog_port": 6514})]),
    "managed-native-host-to-legacy": ("managed", [Step(HOST), Step(LEGACY)]),
    "managed-native-bridge-to-legacy": ("managed", [Step(NATIVE), Step(LEGACY)]),
    "managed-native-reenable-host": (
        "managed",
        [Step(HOST), Step(LEGACY), Step(HOST)],
    ),
    "managed-endpoint": (
        "managed",
        [Step({"fortigate_host": "192.0.2.2", "api_port": 8443})],
    ),
    "managed-endpoint-and-token": (
        "managed",
        [Step({"fortigate_host": "192.0.2.3"}, token="synthetic-second-token")],
    ),
    "managed-token-only": ("managed", [Step(token="synthetic-rotated-token")]),
    "managed-insecure-tls": (
        "managed",
        [Step({"api_tls_verify": False}, acknowledge_insecure_tls=True)],
    ),
    "managed-exclusions": (
        "managed",
        [Step({"optional_exporter_exclusions": ["System/SensorInfo"]})],
    ),
    "managed-exclusions-cleared": (
        "managed",
        [
            Step({"optional_exporter_exclusions": ["System/SensorInfo"]}),
            Step({"optional_exporter_exclusions": []}),
        ],
    ),
    "managed-ca-added": ("managed", [Step(ca=CA)]),
    "managed-ca-removed": ("managed", [Step(ca=CA), Step(ca="")]),
    "managed-cpus": ("managed", [Step({"accounting_cpus": "2"})]),
    "managed-websites": ("managed", [Step({"monitoring": {"websites": True}})]),
    "managed-accounting-off": ("managed", [Step(NO_ACCOUNTING)]),
    "managed-accounting-back-on": (
        "managed",
        [Step(NO_ACCOUNTING), Step({"monitoring": {"accounting": True, "vlans": True}})],
    ),
    "refuse-unregistered-port": (
        "managed",
        [Step({"syslog_port": 5514}, mutate=edit_compose(drop_receiver_port))],
    ),
    "refuse-alloy-mapping-ports": (
        "managed",
        [Step({"syslog_port": 5514}, mutate=edit_compose(mapping_ports("alloy")))],
    ),
    "refuse-grafana-mapping-ports": (
        "managed",
        [Step({"site_name": "Renamed"}, mutate=edit_compose(mapping_ports("grafana")))],
    ),
    "refuse-extra-host-handoff": (
        "managed",
        [Step(HOST), Step({**NATIVE}, mutate=edit_compose(extra_handoff))],
    ),
    "refuse-custom-receiver": (
        "managed",
        [Step(NATIVE), Step({**NATIVE, "syslog_port": 6514}, mutate=edit_compose(custom_receiver))],
    ),
    "refuse-custom-scrape-job": (
        "managed",
        [
            Step(NATIVE),
            Step({**NATIVE, "syslog_port": 6514}, mutate=edit_prometheus(custom_scrape)),
        ],
    ),
    "refuse-custom-alloy-syntax": (
        "managed",
        [
            Step(
                {"syslog_protocol": "udp"},
                mutate=edit_bound("alloy", "/etc/alloy/config.alloy", without_protocol),
            )
        ],
    ),
    "refuse-insecure-tls-with-ca": (
        "managed",
        [Step(ca=CA), Step({"api_tls_verify": False}, acknowledge_insecure_tls=True)],
    ),
    "refuse-native-receiver-extra-port": (
        "managed",
        [
            Step(NATIVE),
            Step(
                {**NATIVE, "syslog_port": 6514},
                mutate=edit_compose(add_ports("fortilens-syslog", "0.0.0.0:9999:9999/tcp")),
            ),
        ],
    ),
    "refuse-host-receiver-published-port": (
        "managed",
        [
            Step(HOST),
            Step(
                {**HOST, "syslog_port": 6514},
                mutate=edit_compose(add_ports("fortilens-syslog", "0.0.0.0:6514:1514/udp")),
            ),
        ],
    ),
    "refuse-native-alloy-mapping-port": (
        "managed",
        [
            Step(NATIVE),
            Step(
                {**NATIVE, "syslog_port": 6514},
                mutate=edit_compose(add_ports("alloy", {"target": 9100})),
            ),
        ],
    ),
    "refuse-native-without-receiver-limits": (
        "managed",
        [
            Step(
                NATIVE,
                mutate=edit_bound("alloy", "/etc/alloy/config.alloy", without_receiver_limits),
            )
        ],
    ),
    "refuse-leave-host-with-extra-handoff": (
        "managed",
        [Step(HOST), Step(LEGACY, mutate=edit_compose(extra_handoff))],
    ),
    "refuse-leave-native-with-alloy-receiver": (
        "managed",
        [
            Step(NATIVE),
            Step(LEGACY, mutate=edit_compose(add_ports("alloy", "0.0.0.0:1514:1514/tcp"))),
        ],
    ),
    "refuse-endpoint-to-known-target": (
        "managed",
        [Step({"fortigate_host": "192.0.2.5"}, mutate=edit_bound(*EXPORTER_AUTH, known_target))],
    ),
    "refuse-unreadable-credential-record": (
        "managed",
        [
            Step(
                {"fortigate_host": "192.0.2.6"},
                mutate=edit_bound(*EXPORTER_AUTH, unreadable_record),
            )
        ],
    ),
    "adopted-display-name": ("adopted", [Step({"site_name": "Adopted rename"})]),
    "adopted-retention-ports": (
        "adopted",
        [Step({"grafana_port": 3201, "usage_port": 3202, "retention": {"loki_days": 120}})],
    ),
    "adopted-native-bridge": ("adopted", [Step(NATIVE)]),
    "adopted-native-host": ("adopted", [Step(HOST)]),
    "adopted-endpoint": ("adopted", [Step({"fortigate_host": "192.0.2.9"})]),
    "adopted-cli-native": ("adopted-native", [Step({**NATIVE, "syslog_port": 6514})]),
    "adopted-minimal-ca-accounting-on": (
        "adopted-minimal-ca",
        [Step({"monitoring": {"accounting": True, "vlans": True}})],
    ),
    "adopted-minimal-display-name": ("adopted-minimal", [Step({"site_name": "Minimal"})]),
    "adopted-minimal-accounting-on": (
        "adopted-minimal",
        [Step({"monitoring": {"accounting": True, "vlans": True}})],
    ),
}


class PrepareCharacterization(unittest.TestCase):
    maxDiff = None

    def installation(self, kind):
        if kind in ("fresh", "managed"):
            c = Configuration(self.base / "stack", self.base / "control", project="patch-fixture")
            if kind == "managed":
                self.apply(c, self.prepare(c, Step(token=TOKEN)))
            return c
        enabled = kind in ("adopted", "adopted-native")
        config = {
            "fortigate_host": "192.0.2.1",
            "accounting_enabled": enabled,
            "portal_enabled": enabled,
        }
        if kind == "adopted-native":
            config.update(NATIVE)
        if kind == "adopted-minimal-ca":
            # The CLI reads the CA from a host path; the copy it renders is what counts.
            source = self.base / "operator-ca.pem"
            source.write_text(CA, encoding="utf-8")
            config["api_ca_file"] = str(source)
        setup.render(setup.validate_config(config), self.base / "adopted", TOKEN)
        return Configuration(self.base / "adopted", self.base / "control", project="patch-adopted")

    def prepare(self, c, step):
        if step.mutate:
            step.mutate(c)
        view = c.view()
        body = {
            "revision": view["revision"],
            "settings": merge(view["settings"], {"fortigate_host": "192.0.2.1", **step.settings}),
        }
        token = step.token or (None if view["secret_status"]["api_token_present"] else TOKEN)
        if token:
            body.update(api_token=token, replace_token=True)
        if step.ca is not None:
            body.update(custom_ca=step.ca, replace_ca=True)
        if step.acknowledge_insecure_tls:
            body["acknowledge_insecure_tls"] = True
        saved = c.save(body)
        return c.prepare(saved["revision"])

    @staticmethod
    def apply(c, review):
        c.install_files(review["plan_id"])
        c.complete(review["plan_id"])

    def outcome(self, kind, steps):
        counter = itertools.count()
        with (
            tempfile.TemporaryDirectory() as directory,
            # Generated passwords become a fixed sequence, so candidates are stable.
            patch.object(
                setup.secrets,
                "token_urlsafe",
                side_effect=lambda n=None: "synthetic-random-%03d" % next(counter),
            ),
        ):
            self.base = Path(directory)
            return self.recorded(kind, steps)

    def recorded(self, kind, steps):
        c = self.installation(kind)
        for step in steps[:-1]:
            self.apply(c, self.prepare(c, step))
        try:
            review = self.prepare(c, steps[-1])
        except Problem as problem:
            return {"problem": problem.code}
        folder, plan = c.plan(review["plan_id"])
        # The plan keeps the order in which files are written, so it is pinned too.
        # A file copied unchanged from the rendered template is recorded as such,
        # so edits to the template sources do not count as a Prepare change.
        files = []
        for record in plan["files"]:
            data = (folder / "candidate" / record["path"]).read_bytes()
            generated = folder / "generated" / record["path"]
            if generated.is_file() and generated.read_bytes() == data:
                files.append([record["path"], "copied from the rendered template"])
                continue
            data = data.replace(str(self.base).encode(), b"<BASE>")
            data = data.replace(review["plan_id"].encode(), b"<PLAN>")
            files.append([record["path"], hashlib.sha256(data).hexdigest()])
        return {"files": files}

    def test_prepare_candidates_match_the_recorded_results(self):
        actual = {}
        for name, (kind, steps) in SCENARIOS.items():
            with self.subTest(scenario=name):
                actual[name] = self.outcome(kind, steps)
        if UPDATE:
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            self.skipTest("Recorded " + str(GOLDEN.relative_to(ROOT)))
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(sorted(expected), sorted(actual), "Scenario list and recording differ")
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                self.assertEqual(expected[name], actual[name])

    def test_every_scenario_reaches_its_intended_result(self):
        """Guards the matrix itself: refusals refuse, the rest produce files."""
        expected = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
        for name, result in expected.items():
            with self.subTest(scenario=name):
                self.assertEqual(name.startswith("refuse-"), "problem" in result, result)


if __name__ == "__main__":
    unittest.main()
