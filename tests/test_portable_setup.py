"""Setup isolation, accounting preservation, query scoping and secret boundary."""
import contextlib
from copy import deepcopy
import importlib.util
import io
import json
import re
import sys
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
spec = importlib.util.spec_from_file_location("portable_setup", ROOT / "portable/setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class PortableSetupTests(unittest.TestCase):
    def test_loki_timeout_layers_leave_time_to_deliver_a_long_query(self):
        # A write deadline shorter than the execution budget can discard a
        # successful query response; the datasource must also outlive both.
        loki = setup.load(ROOT / "portable/templates/loki.json")
        sources = setup.load(ROOT / "portable/templates/datasources.json")
        def seconds(value):
            return int(value[:-1]) * {"s": 1, "m": 60}[value[-1]]
        execution = seconds(loki["limits_config"]["query_timeout"])
        response = seconds(loki["server"]["http_server_write_timeout"])
        client = next(x["jsonData"]["timeout"] for x in sources["datasources"] if x["type"] == "loki")
        self.assertGreater(execution, 0)
        self.assertLess(execution, response)
        self.assertLess(response, client)

    def config(self, **changes):
        return setup.validate_config({"fortigate_host": "fw.example.test", "api_port": 8443,
            "vdom": "Employees", "sla": "ISP_Health", "wan_interfaces": ["port3", "uplink.100"], **changes})

    def test_generated_installation_has_one_matching_target_and_private_token(self):
        with tempfile.TemporaryDirectory() as parent:
            output = Path(parent) / "site"
            token = "test-only-$token:with-characters"
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                setup.render(self.config(), output, token)
            self.assertNotIn(token, stdout.getvalue())
            auth = setup.load(output / "secrets/fortigate-key.yaml")
            prom = setup.load(output / "prometheus/prometheus.yml")
            target = prom["scrape_configs"][0]["static_configs"][0]["targets"]
            self.assertEqual(target, ["https://fw.example.test:8443"])
            self.assertEqual(list(auth), target)
            self.assertEqual(auth[target[0]]["token"], token)
            for p in output.rglob("*"):
                if p.is_file() and p.name != "fortigate-key.yaml":
                    self.assertNotIn(token, p.read_text(encoding="utf-8"))
            compose = setup.load(output / "compose.yaml")
            self.assertNotIn("-insecure", compose["services"]["fortigate-exporter"]["command"])
            self.assertEqual([name for name, s in compose["services"].items() if "ports" in s], ["grafana", "alloy"])

    def test_existing_directory_is_not_modified(self):
        with tempfile.TemporaryDirectory() as parent:
            marker = Path(parent) / "existing.txt"
            marker.write_text("preserve")
            with self.assertRaises(setup.SetupError):
                setup.render(self.config(), parent, "fake-token")
            self.assertEqual(marker.read_text(), "preserve")
            self.assertEqual(len(list(Path(parent).iterdir())), 1)

    def test_rejects_query_injection_and_bad_endpoints(self):
        for change in [{"fortigate_host": "https://host/path"}, {"fortigate_host": "host:8443"},
                       {"fortigate_host": "a..b"}, {"api_port": 65536}, {"api_port": True},
                       {"sla": 'x"} or vector(1)'}, {"wan_interfaces": ["port1|.*"]},
                       {"vdom": "root$1"}, {"syslog_protocol": "sctp"}, {"token": "secret"},
                       {"api_tls_verify": "false"}]:
            with self.subTest(change=change), self.assertRaises(setup.SetupError):
                self.config(**change)
        self.assertEqual(setup.target_url(self.config(fortigate_host="2001:db8::1")), "https://[2001:db8::1]:8443")

    def test_other_company_does_not_inherit_original_defaults(self):
        d = setup.make_dashboard(self.config())
        text = json.dumps(d)
        for old in ["legacy-site.example.test", "legacy-sla", "legacy-datasource-uid"]:
            self.assertNotIn(old, text)
        self.assertEqual(d["metadata"], {"name": "forti-monitor"})
        self.assertEqual(len(d["spec"]["elements"]), 31)
        self.assertEqual(len(d["spec"]["variables"]), 4)
        wan = d["spec"]["variables"][3]["spec"]
        self.assertNotIn("allValue", wan)
        self.assertNotIn("\n", wan["definition"])
        self.assertIn("uplink.100", wan["definition"])
        self.assertIn("unless on() count(", wan["definition"])
        self.assertTrue(wan["multi"])

    def test_unconfigured_sla_does_not_fabricate_defaults(self):
        c = self.config(sla="", wan_interfaces=[])
        d = setup.make_dashboard(c)
        for v in d["spec"]["variables"][2:]:
            self.assertNotIn("vector(0)", v["spec"]["definition"])

    def test_panel_queries_and_missing_value_behavior_are_preserved(self):
        source = setup.load(ROOT / "portable/templates/dashboard.json")
        portable = setup.make_dashboard(self.config())
        for name, element in source["spec"]["elements"].items():
            before = element["spec"]["data"]["spec"]["queries"]
            after = portable["spec"]["elements"][name]["spec"]["data"]["spec"]["queries"]
            self.assertEqual([q["spec"]["query"]["spec"] for q in before], [q["spec"]["query"]["spec"] for q in after])
            self.assertEqual(element["spec"]["data"], portable["spec"]["elements"][name]["spec"]["data"])
            if portable["spec"]["elements"][name]["spec"]["vizConfig"]["group"] == "stat":
                self.assertEqual(portable["spec"]["elements"][name]["spec"]["vizConfig"]["spec"]["fieldConfig"]["defaults"]["noValue"], "NO DATA")
        self.assertEqual(source["spec"]["elements"]["panel-18"]["spec"]["vizConfig"]["spec"]["fieldConfig"]["defaults"]["mappings"],
            portable["spec"]["elements"]["panel-18"]["spec"]["vizConfig"]["spec"]["fieldConfig"]["defaults"]["mappings"])

    def test_bundle_only_contains_reviewed_public_inputs(self):
        with tempfile.TemporaryDirectory() as parent, contextlib.redirect_stdout(io.StringIO()):
            p = Path(parent) / "bundle.zip"
            setup.bundle(p)
            with zipfile.ZipFile(p) as archive:
                self.assertEqual(len(archive.namelist()), 23)
                for name in archive.namelist():
                    self.assertTrue(name.startswith("forti-monitor/"))
                    self.assertNotIn("local/", name)
                    self.assertNotIn("reference/", name)
                    self.assertIsNone(re.search(r"[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]", archive.read(name).decode("utf-8")),
                                      "Non-English Arabic text in distribution file: " + name)
                self.assertIn("forti-monitor/LICENSE", archive.namelist())


if __name__ == "__main__":
    unittest.main()
