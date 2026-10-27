"""Inventory provenance, VDOM isolation, explicit selection and secret boundaries."""
import argparse
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import interfaces
spec = importlib.util.spec_from_file_location("inventory_setup", ROOT / "portable/setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def response(rows=None, **fields):
    return {"status": "success", "vdom": "Office", "results": rows if rows is not None else [
        {"name": "port1", "vdom": "Office", "type": "physical", "role": "wan", "password": "NEVER_PERSIST"},
        {"name": "port2", "vdom": "Office", "type": "physical", "role": "lan"},
        {"name": "Employees", "vdom": "Office", "type": "vlan", "role": "lan", "interface": "port2", "vlanid": 20},
        {"name": "uplink.100", "vdom": "Office", "type": "vlan", "role": "wan", "interface": "port1", "vlanid": 100},
        {"name": "wan_named_but_unknown", "vdom": "Office", "type": "physical", "role": "undefined"},
    ], **fields}


def metric(vdom="Office", name="Employees", **labels):
    return {"status": "success", "data": {"resultType": "matrix", "result": [
        {"metric": {"name": name, "vdom": vdom, **labels}, "values": [[time.time() - 5, "0"]]}]}}


class InterfaceInventoryTests(unittest.TestCase):
    def config(self, **changes):
        return setup.validate_config({"fortigate_host": "fw.example.test", "vdom": "Office", **changes})

    def test_roles_and_vlan_are_separate_and_no_name_guessing(self):
        rows, partial = interfaces.from_cmdb(response())
        items = {x["name"]: x for x in interfaces.merge([], rows)}
        self.assertEqual(items["Employees"]["type"], "vlan")
        self.assertEqual(items["Employees"]["role"], "lan")
        self.assertEqual(items["uplink.100"]["role"], "wan")
        self.assertEqual(items["Employees"]["parent"], "port2")
        self.assertEqual(items["Employees"]["vlan_id"], 20)
        self.assertEqual(items["wan_named_but_unknown"]["review_status"], "Needs review")
        self.assertFalse(partial)
        self.assertNotIn("NEVER_PERSIST", json.dumps(items))

    def test_existing_exporter_parent_and_vlan_used_before_api(self):
        calls = []
        def request(*args, **kwargs):
            calls.append("exporter")
            return metric(parent="port2", vlanid="20")
        def api(start):
            calls.append("api")
            return response()
        r = interfaces.collect(self.config(), "FAKE_TOKEN", request=request, installation=Path("/test"), api_get=api)
        self.assertEqual(calls, ["exporter", "api"])
        employee = next(x for x in r["interfaces"] if x["name"] == "Employees")
        self.assertEqual(employee["sources"], ["exporter", "cmdb"])
        self.assertEqual(employee["vlan_id"], 20)
        self.assertEqual(employee["role"], "lan")

    def test_metrics_only_does_not_assume_physical_or_wan(self):
        with patch.object(interfaces, "cmdb_get") as api:
            r = interfaces.collect(self.config(), "", request=lambda *a, **k: metric(name="wan1", vlanid="0"),
                                   installation=Path("/test"), use_api=False)
        api.assert_not_called()
        item = r["interfaces"][0]
        self.assertIsNone(item["type"])
        self.assertIsNone(item["role"])
        self.assertEqual(item["review_status"], "Needs review")

    def test_missing_vdom_is_not_guessed_for_exporter(self):
        data = metric(vdom="", parent="port2", vlanid="20")
        rows = interfaces.merge(interfaces.from_metrics(data), [])
        self.assertIsNone(rows[0]["vdom"])
        with self.assertRaises(interfaces.InventoryError):
            interfaces.selected_names({"interfaces": rows}, ["Employees"], "Office")

    def test_stale_metrics_do_not_become_current_inventory(self):
        data = metric(parent="port2", vlanid="20")
        data["data"]["result"][0]["values"][0][0] = time.time() - 1000
        self.assertEqual(interfaces.from_metrics(data), [])

    def test_conflicts_and_limited_fields_require_review(self):
        m = interfaces.from_metrics(metric(parent="port9", vlanid="21"))
        a, _ = interfaces.from_cmdb(response([{ "name": "Employees", "type": "vlan", "role": "lan", "interface": "port2", "vlanid": 20, "q_limited_permission": True}]))
        item = interfaces.merge(m, a)[0]
        self.assertEqual(item["parent"], "port2")
        self.assertEqual(item["review_status"], "Needs review")
        self.assertIn("Sources disagree about vlan_id", item["issues"])
        self.assertIn("API permissions limit this interface's fields", item["issues"])

    def test_same_name_in_different_vdoms_does_not_merge(self):
        a, _ = interfaces.from_cmdb(response([
            {"name": "port1", "vdom": "Office", "type": "physical", "role": "wan"},
            {"name": "port1", "vdom": "Guest", "type": "physical", "role": "lan"}]))
        rows = interfaces.merge([], a)
        self.assertEqual(len(rows), 2)
        self.assertEqual(interfaces.selected_names({"interfaces": rows}, ["port1"], "Office"), ["port1"])
        with self.assertRaises(interfaces.InventoryError):
            interfaces.selected_names({"interfaces": rows}, ["port1"], "Missing")

    def test_api_pagination_and_permission_partial_are_preserved(self):
        pages = []
        def api(start):
            pages.append(start)
            if start == 0:
                return response([{ "name": "limited", "type": "physical", "q_limited_permission": True}], limit_reached=True, next_idx=1)
            return response([{ "name": "port1", "type": "physical", "role": "wan"}], limit_reached=False)
        r = interfaces.collect(self.config(), "FAKE_TOKEN", api_get=api)
        self.assertEqual(pages, [0, 1])
        self.assertEqual(len(r["interfaces"]), 2)
        self.assertIn("partial", r["coverage"])

    def test_broken_pagination_is_bounded_and_not_claimed_complete(self):
        count = []
        def api(start):
            count.append(start)
            return response(limit_reached=True, next_idx=0)
        r = interfaces.collect(self.config(), "FAKE_TOKEN", api_get=api)
        self.assertEqual(count, [0])
        self.assertIn("partial", r["coverage"])

    def test_offline_mode_never_calls_network_and_whitelists_fields(self):
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent) / "inventory.json"
            path.write_text(json.dumps(response()))
            with patch.object(interfaces, "cmdb_get") as api:
                r = interfaces.collect(self.config(), "FAKE_TOKEN", response_file=path,
                                       request=lambda *a, **k: self.fail("Unexpected network request"))
            api.assert_not_called()
            self.assertIn("Offline", r["coverage"])
            self.assertNotIn("NEVER_PERSIST", json.dumps(r))
            self.assertNotIn("FAKE_TOKEN", json.dumps(r))

    def test_api_transport_uses_get_header_whitelist_and_no_redirect(self):
        with patch.object(interfaces.urllib.request, "build_opener") as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value = json.dumps(response()).encode()
            interfaces.cmdb_get(self.config(), "FAKE_TOKEN")
        req = build.return_value.open.call_args.args[0]
        self.assertEqual(req.method, "GET")
        self.assertEqual(req.get_header("Authorization"), "Bearer FAKE_TOKEN")
        self.assertNotIn("FAKE_TOKEN", req.full_url)
        params = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
        self.assertEqual(params["vdom"], ["Office"])
        self.assertEqual(params["format"], ["name|vdom|type|role|interface|vlanid"])
        self.assertTrue(any(isinstance(x, interfaces.NoRedirect) for x in build.call_args.args))

    def test_api_permission_failure_is_sanitized_and_manual_setup_available(self):
        with patch.object(interfaces.urllib.request, "build_opener") as build:
            build.return_value.open.side_effect = urllib.error.HTTPError("https://test/", 403, "SECRET_REASON", {}, io.BytesIO(b"SECRET_BODY"))
            r = interfaces.collect(self.config(), "FAKE_TOKEN")
        self.assertEqual(r["interfaces"], [])
        self.assertIn("api_http_403", json.dumps(r))
        self.assertNotIn("SECRET", json.dumps(r))

    def test_init_saves_explicit_selection_before_dashboard_import(self):
        with tempfile.TemporaryDirectory() as parent:
            parent = Path(parent)
            config, token, data, output = parent / "site.json", parent / "token", parent / "raw.json", parent / "local"
            config.write_text(json.dumps(self.config()))
            token.write_text("FAKE_TOKEN")
            data.write_text(json.dumps(response()))
            r = subprocess.run([sys.executable, str(ROOT / "portable/setup.py"), "init", "--config", str(config),
                "--token-file", str(token), "--inventory-file", str(data), "--select-wan", "port1,uplink.100", "--confirm-wan", "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(setup.load(output / "site.json")["confirmed_wan_interfaces"], ["port1", "uplink.100"])
            stored = (output / "interface-inventory.json").read_text()
            self.assertNotIn("NEVER_PERSIST", stored)
            self.assertNotIn("FAKE_TOKEN", stored)
            wan = setup.load(output / "grafana/dashboard.json")["spec"]["variables"][3]["spec"]
            self.assertEqual([x["value"] for x in wan["options"]], ["$__all", "port1", "uplink.100"])
            self.assertIn('interface=~', wan["definition"])
            self.assertNotIn("\n", wan["definition"])
            self.assertNotIn("allValue", wan)
            self.assertEqual(len(setup.load(output / "grafana/dashboard.json")["spec"]["elements"]), 26)

    def test_existing_installation_selection_is_explicit_backed_up_and_local(self):
        with tempfile.TemporaryDirectory() as parent, contextlib.redirect_stdout(io.StringIO()):
            p = Path(parent) / "local"
            c = self.config()
            setup.render(c, p, "FAKE_TOKEN")
            original_dashboard = (p / "grafana/dashboard.json").read_bytes()
            raw = Path(parent) / "raw.json"; raw.write_text(json.dumps(response()))
            args = argparse.Namespace(metrics_only=False, inventory_file=raw, select_wan="port1", confirm_wan=False, json=True)
            with self.assertRaises(setup.SetupError):
                setup.interface_command(p, c, args)
            self.assertEqual(setup.load(p / "site.json")["confirmed_wan_interfaces"], [])
            args.confirm_wan = True
            r = setup.interface_command(p, c, args)
            self.assertFalse(r["selection"]["live_dashboard_changed"])
            self.assertEqual((p / "grafana/dashboard.json").read_bytes(), original_dashboard)
            self.assertTrue((p / "grafana/dashboard.interfaces-candidate.json").is_file())
            self.assertEqual(len(list((p / "backups").glob("interfaces-*/site.json"))), 1)

    def test_undefined_role_can_be_selected_only_by_explicit_human_choice(self):
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent) / "data.json";path.write_text(json.dumps(response()))
            report = interfaces.collect(self.config(), "", response_file=path)
            answers = iter(["wan_named_but_unknown", "no"])
            with contextlib.redirect_stdout(io.StringIO()):
                unchanged = interfaces.confirm_interactive(report, self.config(), lambda *a: next(answers))
            self.assertEqual(unchanged["confirmed_wan_interfaces"], [])
            answers = iter(["wan_named_but_unknown", "yes"])
            with contextlib.redirect_stdout(io.StringIO()):
                selected = interfaces.confirm_interactive(report, self.config(), lambda *a: next(answers))
            self.assertEqual(selected["confirmed_wan_interfaces"], ["wan_named_but_unknown"])
            self.assertEqual(next(x for x in report["interfaces"] if x["name"] == "wan_named_but_unknown")["role"], "undefined")


if __name__ == "__main__":
    unittest.main()
