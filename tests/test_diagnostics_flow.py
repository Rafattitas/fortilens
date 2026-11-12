"""Characterization of the management Diagnostics response (runtime.diagnostics).

Pins the full response the console receives, apart from the host "system"
block, for several installation states, with a fixed clock and a synthetic
Docker helper that returns canned service answers. It also records which
service requests were made, in order. tests/fixtures/diagnostics_flow.json
holds the expected results; regenerate it only for an intended change:

    FORTILENS_UPDATE_GOLDEN=1 python -m unittest tests.test_diagnostics_flow

All hosts, tokens and samples are synthetic.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
try:
    from management.common import Problem
    from management.configuration import Configuration
    from management.runtime import Operations
    from test_management import FakeDocker, fake_device
except ImportError:
    raise unittest.SkipTest("Management tests require management/requirements-test.txt") from None

GOLDEN = ROOT / "tests" / "fixtures" / "diagnostics_flow.json"
UPDATE = os.environ.get("FORTILENS_UPDATE_GOLDEN") == "1"
NOW = 1_794_000_000.0


class SampleDocker(FakeDocker):
    """Canned answers from each monitoring service; records every request."""

    def __init__(self, c, *, log_age=40, lag=12, metric_age=30):
        super().__init__(c)
        self.log_age, self.lag, self.metric_age = log_age, lag, metric_age
        self.requests = []

    def http(self, service, port, path, **kwargs):
        self.requests.append([service, port, path.split("?")[0]])
        if self.down:
            raise Problem("unavailable", "Synthetic monitoring service is offline.")
        if service == "grafana":
            return {"database": "ok"}
        if service == "fortilens-usage":
            return {"last_poll_ok": True, "processing_lag_seconds": self.lag}
        if service == "fortilens-syslog":
            return {"status": "ok", "received_datagrams": 3, "rejected_reasons": {}}
        if service == "loki" and "query_range" in path:
            values = [] if self.log_age is None else [[str(int((NOW - self.log_age) * 1e9)), "x"]]
            return {"data": {"result": [{"values": values}] if values else []}}
        sample = {"metric": {"job": "fortigate"}, "value": [NOW - self.metric_age, "1"]}
        return {"status": "success", "data": {"resultType": "vector", "result": [sample]}}


def installed(base, monitoring=None, **settings):
    c = Configuration(base / "stack", base / "control", project="diagnostics-fixture")
    view = c.view()
    values = deepcopy(view["settings"])
    values.update(fortigate_host="192.0.2.1", **settings)
    values["monitoring"].update(monitoring or {})
    saved = c.save(
        {
            "revision": view["revision"],
            "settings": values,
            "api_token": "synthetic-diagnostics-token",
            "replace_token": True,
        }
    )
    review = c.prepare(saved["revision"])
    c.install_files(review["plan_id"])
    c.complete(review["plan_id"])
    return c


NO_ACCOUNTING = {"accounting": False, "vlans": False, "websites": False}

# name -> (settings for installed(), docker options, installed?)
SCENARIOS = {
    "running": ({}, {}, True),
    "no-recent-logs": ({}, {"log_age": None}, True),
    "monitoring-down": ({}, {"down": True}, True),
    "native-syslog": (
        {"syslog_format": "fortigate", "syslog_protocol": "udp", "syslog_port": 5514},
        {},
        True,
    ),
    "accounting-off": ({"monitoring": NO_ACCOUNTING}, {}, True),
    "exporter-exclusions": ({"optional_exporter_exclusions": ["System/SensorInfo"]}, {}, True),
    "not-installed": ({}, {}, False),
}


@unittest.skipUnless(sys.platform.startswith("linux"), "Management runs on Linux")
class DiagnosticsCharacterization(unittest.TestCase):
    maxDiff = None

    def outcome(self, settings, options, is_installed):
        with tempfile.TemporaryDirectory(prefix="fortilens-diagnostics-") as directory:
            base = Path(directory).resolve()
            settings = dict(settings)
            if is_installed:
                c = installed(base, **settings)
            else:
                c = Configuration(base / "stack", base / "control", project="diagnostics-fixture")
            down = options.pop("down", False) if options else False
            docker = SampleDocker(c, **options)
            docker.down = down
            ops = Operations(c, docker, fake_device)
            try:
                with patch("time.time", return_value=NOW):
                    result = ops.diagnostics()
            finally:
                ops.executor.shutdown(wait=True)
            result.pop("system")
            text = json.dumps({"response": result, "requests": docker.requests}, sort_keys=True)
            return json.loads(text.replace(str(base), "<BASE>"))

    def test_diagnostics_match_the_recorded_results(self):
        actual = {
            name: self.outcome(deepcopy(s), dict(o), i) for name, (s, o, i) in SCENARIOS.items()
        }
        if UPDATE:
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            self.skipTest("Recorded " + str(GOLDEN.relative_to(ROOT)))
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(sorted(expected), sorted(actual))
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                self.assertEqual(expected[name], actual[name])


if __name__ == "__main__":
    unittest.main()
