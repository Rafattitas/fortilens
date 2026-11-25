"""Who uses each Internet line: per-line live figures and the SD-WAN rule a device matches.

All FortiGate payloads are synthetic (RFC 5737 addresses, locally administered MACs);
no test contacts a device.
"""

from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import accounting as a
import usage_service as u
import wan_health as w
from test_accounting import CONFIG, INVENTORY, record
from test_live import source

ADDRESSES = {
    "results": [
        {"name": "all", "type": "ipmask", "subnet": "0.0.0.0 0.0.0.0"},
        {"name": "eng-net", "type": "ipmask", "subnet": "192.0.2.0 255.255.255.128"},
        {"name": "lab-range", "type": "iprange", "start-ip": "192.0.2.200", "end-ip": "192.0.2.210"},
        {"name": "laptop", "type": "mac", "macaddr": [{"macaddr": "02:00:00:00:00:01"}]},
        {"name": "old-macs", "type": "mac", "start-mac": "02:00:00:00:01:00", "end-mac": "02:00:00:00:01:ff"},
        {"name": "web", "type": "fqdn", "fqdn": "portal.example"},
    ]
}
GROUPS = {
    "results": [
        {"name": "team", "member": [{"name": "laptop"}, {"name": "lab-range"}]},
        {
            "name": "eng-but-laptop",
            "member": [{"name": "eng-net"}],
            "exclude": "enable",
            "exclude-member": [{"name": "laptop"}],
        },
    ]
}


def rule(seq, name, src, members, **extra):
    return {
        "id": seq,
        "name": name,
        "mode": extra.pop("mode", "manual"),
        "src": [{"name": s} for s in src],
        "dst": [{"name": "all"}],
        "priority-members": [{"seq-num": m} for m in members],
        **extra,
    }


SDWAN = {
    "results": {
        "members": [{"seq-num": 1, "interface": "wan1"}, {"seq-num": 2, "interface": "wan2"}],
        "service": [
            rule(
                8,
                "o365",
                ["team"],
                [2, 1],
                **{
                    "internet-service": "enable",
                    "internet-service-name": [{"name": "Microsoft-Office365.Published"}],
                    "dst": [],
                },
            ),
            rule(9, "off", ["laptop"], [2], status="disable"),
            rule(6, "infra", ["laptop", "eng-but-laptop"], [1]),
            rule(3, "https-only", ["old-macs"], [2], protocol=6, **{"start-port": 443, "end-port": 443}),
            rule(5, "web-users", ["web"], [2]),
            rule(4, "everyone", ["all"], [2]),
        ],
    }
}


class SourceMatchTests(unittest.TestCase):
    def setUp(self):
        self.book = w.address_book(ADDRESSES, GROUPS)

    def match(self, name, ip, mac):
        return w.source_match(self.book, name, ip, mac)

    def test_subnets_ranges_macs_and_groups_are_judged(self):
        self.assertTrue(self.match("all", "198.51.100.1", ""))
        self.assertTrue(self.match("eng-net", "192.0.2.10", ""))
        self.assertFalse(self.match("eng-net", "192.0.2.130", ""))
        self.assertTrue(self.match("lab-range", "192.0.2.205", ""))
        self.assertTrue(self.match("laptop", "198.51.100.9", "02:00:00:00:00:01"))
        self.assertFalse(self.match("laptop", "198.51.100.9", "02:00:00:00:00:02"))
        self.assertTrue(self.match("old-macs", "", "02:00:00:00:01:7F"))
        self.assertTrue(self.match("team", "192.0.2.201", "02:00:00:00:00:09"))
        # A group with excluded members leaves them out.
        self.assertTrue(self.match("eng-but-laptop", "192.0.2.10", "02:00:00:00:00:09"))
        self.assertFalse(self.match("eng-but-laptop", "192.0.2.10", "02:00:00:00:00:01"))

    def test_what_cannot_be_judged_stays_unknown(self):
        self.assertIsNone(self.match("web", "192.0.2.10", "02:00:00:00:00:01"))
        self.assertIsNone(self.match("missing-object", "192.0.2.10", ""))
        # A MAC object cannot be judged for a device known only by its address.
        self.assertIsNone(self.match("laptop", "192.0.2.10", ""))
        self.assertIsNone(self.match("eng-net", "", "02:00:00:00:00:01"))


class DeviceRuleTests(unittest.TestCase):
    def setUp(self):
        self.book = w.address_book(ADDRESSES, GROUPS)
        self.rules = w.sdwan_rules(SDWAN, ["wan1", "wan2"])["rules"]

    def names(self, ip, mac):
        found = w.device_rules(self.rules, self.book, ip, mac)
        return [(r["name"], r["lines"], r["all_traffic"], r["certain"]) for r in found["rules"]], found[
            "settled"
        ]

    def test_partial_rules_come_first_and_the_first_full_rule_ends_the_list(self):
        by_name = {r["name"]: r for r in self.rules}
        self.assertFalse(by_name["o365"]["all_traffic"])
        self.assertFalse(by_name["https-only"]["all_traffic"])
        self.assertTrue(by_name["infra"]["all_traffic"])
        # The laptop's Office 365 traffic goes to wan2 first; everything else follows infra.
        self.assertEqual(
            self.names("192.0.2.10", "02:00:00:00:00:01"),
            (
                [("o365", ["wan2", "wan1"], False, True), ("infra", ["wan1"], True, True)],
                True,
            ),
        )
        # Disabled rules never match.
        self.assertNotIn("off", [n for n, *_ in self.names("192.0.2.10", "02:00:00:00:00:01")[0]])

    def test_unknown_sources_are_listed_as_maybe_and_the_search_goes_on(self):
        self.assertEqual(
            self.names("198.51.100.7", "02:00:00:00:00:09"),
            ([("web-users", ["wan2"], True, False), ("everyone", ["wan2"], True, True)], True),
        )
        self.assertEqual(
            self.names("198.51.100.7", "02:00:00:00:01:10")[0][0][0], "https-only"
        )

    def test_without_a_full_rule_the_rest_follows_the_implicit_rule(self):
        rules = [r for r in self.rules if r["name"] != "everyone"]
        found = w.device_rules(rules, self.book, "198.51.100.7", "02:00:00:00:00:09")
        self.assertFalse(found["settled"])
        self.assertEqual([r["name"] for r in found["rules"]], ["web-users"])

    def test_a_negated_source_matches_everyone_else(self):
        payload = {
            "results": {
                "members": [{"seq-num": 1, "interface": "wan1"}],
                "service": [rule(1, "not-laptop", ["laptop"], [1], **{"src-negate": "enable"})],
            }
        }
        rules = w.sdwan_rules(payload, ["wan1"])["rules"]
        self.assertTrue(rules[0]["source_negated"])
        self.assertEqual(w.device_rules(rules, self.book, "", "02:00:00:00:00:01")["rules"], [])
        other = w.device_rules(rules, self.book, "", "02:00:00:00:00:02")
        self.assertEqual([r["name"] for r in other["rules"]], ["not-laptop"])


class LiveLineTests(unittest.TestCase):
    def setUp(self):
        self.store = a.Store(":memory:", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(self.store.close)

    def engine(self, root, broken=()):
        reads = []

        def fake_fetch(base, route, params=None, **kwargs):
            reads.append(route)
            if route in broken or route == u.INTERFACE_ROUTE:
                raise u.ServiceError("http_403")
            if route == u.LIVE_ROUTE:
                rows = (
                    [
                        source("192.0.2.10", "02:00:00:00:00:01", "staff", "wan1", 800, 4000, 3),
                        source("192.0.2.10", "02:00:00:00:00:01", "staff", "wan2", 100, 50, 2),
                        source("192.0.2.11", "02:00:00:00:00:02", "staff", "wan2", 900, 100, 4),
                    ]
                    if params["report_by"] == "source"
                    else []
                )
                return {"status": "success", "results": {"details": rows}}
            payload = {
                u.SDWAN_ROUTE: SDWAN,
                u.ADDRESS_ROUTE: ADDRESSES,
                u.ADDRGRP_ROUTE: GROUPS,
            }[route]
            return {"status": "success", **payload}

        token = Path(root, "token")
        token.write_text("synthetic-token\n")
        engine = u.Engine(self.store, token_file=str(token), config_root=root, fetch=fake_fetch)
        engine.config = {
            **engine.config,
            "fortigate_host": "192.0.2.1",
            "api_port": 443,
            "vdom": "root",
            "wan_interfaces": ["wan1", "wan2"],
            "confirmed_wan_interfaces": ["wan1", "wan2"],
            "wan_speeds": {"wan1": {"down_mbps": 35.0, "up_mbps": 5.0}},
        }
        return engine, reads

    def test_live_view_splits_devices_by_line_with_speeds_and_rules(self):
        with tempfile.TemporaryDirectory() as root:
            engine, reads = self.engine(root)
            r = engine.live_view(now=100.0)
            self.assertEqual(r["sdwan"], {"status": "OBSERVED"})
            speeds = {l["interface"]: l["speed"] for l in r["links"]}
            self.assertEqual(speeds, {"wan1": {"down_mbps": 35.0, "up_mbps": 5.0}, "wan2": None})
            laptop = next(d for d in r["devices"] if d["mac"] == "02:00:00:00:00:01")
            self.assertEqual(
                laptop["lines"],
                {
                    "wan1": {"download_bps": 800, "upload_bps": 4000, "sessions": 3},
                    "wan2": {"download_bps": 100, "upload_bps": 50, "sessions": 2},
                },
            )
            self.assertEqual(
                [x["name"] for x in laptop["sdwan"]["rules"]], ["o365", "infra"]
            )
            other = next(d for d in r["devices"] if d["mac"] == "02:00:00:00:00:02")
            self.assertEqual([x["name"] for x in other["sdwan"]["rules"]], ["infra"])
            # Addresses are read once and kept with the SD-WAN rules.
            engine.live_view(now=110.0)
            self.assertEqual(reads.count(u.ADDRESS_ROUTE), 1)
            # The alert path keeps the plain live figures.
            self.assertNotIn("sdwan", engine.live(now=110.0))

    def test_unreadable_addresses_leave_rules_unknown_not_guessed(self):
        with tempfile.TemporaryDirectory() as root:
            engine, _ = self.engine(root, broken=(u.ADDRGRP_ROUTE,))
            r = engine.live_view(now=100.0)
            self.assertEqual(r["sdwan"], {"status": "UNAVAILABLE", "reason": "http_403"})
            self.assertTrue(all(d["sdwan"] is None for d in r["devices"]))
            self.assertEqual(r["devices"][0]["lines"]["wan1"]["sessions"], 3)

    def test_line_devices_carry_rules_and_the_line_speed(self):
        self.store.ingest(
            [
                record("2026-10-20T12:00:00Z", sessionid="1", sentdelta="100", rcvddelta="1000"),
                record("2026-10-20T12:01:00Z", sessionid="2", sentdelta="10", rcvddelta="90"),
            ]
        )
        with tempfile.TemporaryDirectory() as root:
            engine, _ = self.engine(root)
            engine.config["confirmed_wan_interfaces"] = ["uplink-a", "wan1"]
            engine.config["wan_speeds"] = {"uplink-a": {"down_mbps": 10.0, "up_mbps": 2.0}}
            start = a.timestamp_ns("2026-10-20T11:59:00Z") // a.NS
            r = engine.wan_incident("uplink-a", start, start + 3600)
            self.assertEqual(r["speed"], {"down_mbps": 10.0, "up_mbps": 2.0})
            self.assertEqual(r["sdwan"], {"status": "OBSERVED"})
            device = r["devices"][0]
            self.assertEqual(device["ip"], "192.0.2.10")
            self.assertIn("rules", device["sdwan"])
            # The cached detail is not changed by the rules added to one answer.
            again = engine.wan_incident("uplink-a", start, start + 3600)
            self.assertEqual(again["devices"], r["devices"])


if __name__ == "__main__":
    unittest.main()
