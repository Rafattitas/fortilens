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
        {
            "name": "lab-range",
            "type": "iprange",
            "start-ip": "192.0.2.200",
            "end-ip": "192.0.2.210",
        },
        {"name": "laptop", "type": "mac", "macaddr": [{"macaddr": "02:00:00:00:00:01"}]},
        {
            "name": "old-macs",
            "type": "mac",
            "start-mac": "02:00:00:00:01:00",
            "end-mac": "02:00:00:00:01:ff",
        },
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
            rule(
                3,
                "https-only",
                ["old-macs"],
                [2],
                protocol=6,
                **{"start-port": 443, "end-port": 443},
            ),
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
        return [
            (r["name"], r["lines"], r["all_traffic"], r["certain"]) for r in found["rules"]
        ], found["settled"]

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
        self.assertEqual(self.names("198.51.100.7", "02:00:00:00:01:10")[0][0][0], "https-only")

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
            self.assertEqual([x["name"] for x in laptop["sdwan"]["rules"]], ["o365", "infra"])
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
            # The reason is the sanitized error code, never the upstream text.
            self.assertEqual(r["sdwan"]["status"], "UNAVAILABLE")
            self.assertTrue(r["sdwan"]["reason"])
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


class LineVolumeTests(unittest.TestCase):
    """What each line carried, from synthetic Prometheus interface counters."""

    # 2026-11-25 10:30 UTC; the store's accounting zone is UTC.
    NOW = 1795602600.0

    def setUp(self):
        self.store = a.Store(":memory:", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(self.store.close)
        self.queries = []

    def engine(self, silent=()):
        """wan1 sends 1,000 and receives 4,000 bytes a second; wan2 half of that.

        Lines in ``silent`` have no samples. Samples stop one hour before NOW for wan2.
        """
        import re

        def fake_fetch(base, route, params=None, **kwargs):
            self.queries.append(params)
            seconds = int(re.search(r"\[(\d+)s\]", params["query"]).group(1))
            end = float(params["time"])
            result = []
            for name, scale in (("wan1", 1.0), ("wan2", 0.5)):
                if name in silent:
                    continue
                covered = seconds
                if name == "wan2":
                    covered = max(0, min(seconds, seconds - (end - (self.NOW - 3600))))
                if covered <= 0:
                    continue
                for label, value in (
                    ("download", 4000 * scale * covered),
                    ("upload", 1000 * scale * covered),
                    ("samples", covered / 60),
                ):
                    result.append(
                        {"metric": {"name": name, "dir": label}, "value": [end, str(value)]}
                    )
            return {"status": "success", "data": {"resultType": "vector", "result": result}}

        engine = u.Engine(self.store, fetch=fake_fetch)
        engine.config = {
            **engine.config,
            "fortigate_host": "192.0.2.1",
            "confirmed_wan_interfaces": ["wan1", "wan2"],
            "wan_speeds": {"wan1": {"down_mbps": 35.0, "up_mbps": 5.0}},
            "prometheus_scrape_seconds": 60,
        }
        return engine

    def test_a_day_is_split_into_hours_and_lines_without_samples_stay_unknown(self):
        r = self.engine(silent=("wan2",)).line_volume("yesterday", now=self.NOW)
        self.assertEqual((r["start"], r["end"]), ("2026-11-24T00:00:00+00:00", "2026-11-25T00:00:00+00:00"))
        self.assertEqual((r["bucket"], len(r["buckets"])), ("hour", 24))
        self.assertEqual(r["buckets"][13]["label"], "13:00")
        wan1, wan2 = r["lines"]
        self.assertEqual((wan1["download_bytes"], wan1["upload_bytes"]), (4000 * 86400, 1000 * 86400))
        self.assertEqual((wan1["coverage_pct"], wan1["speed"]["up_mbps"]), (100.0, 5.0))
        # No samples: unknown, never zero.
        self.assertEqual((wan2["download_bytes"], wan2["coverage_pct"]), (None, 0.0))
        self.assertEqual(r["buckets"][0]["lines"]["wan2"]["upload_bytes"], None)
        # Every query is scoped to the confirmed lines of this FortiGate.
        self.assertIn('name=~"wan1|wan2"', self.queries[0]["query"])
        self.assertIn('instance="192.0.2.1"', self.queries[0]["query"])

    def test_longer_periods_use_local_days_and_report_partial_coverage(self):
        r = self.engine().line_volume("week", now=self.NOW)
        self.assertEqual(r["bucket"], "day")
        self.assertEqual(
            [b["label"] for b in r["buckets"]],
            ["2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23", "2026-11-24", "2026-11-25"],
        )
        # Today's bucket ends now, not at midnight.
        self.assertEqual(r["buckets"][-1]["end"], "2026-11-25T10:30:00+00:00")
        wan2 = r["lines"][1]
        self.assertLess(wan2["coverage_pct"], 100)
        self.assertEqual(r["buckets"][-1]["lines"]["wan2"]["download_bytes"], round(2000 * 9.5 * 3600))
        month = self.engine().line_volume("last_month", now=self.NOW)
        self.assertEqual((month["start"][:10], month["end"][:10], len(month["buckets"])), ("2026-10-01", "2026-11-01", 31))

    def test_windows_are_checked_and_results_kept_for_a_minute(self):
        engine = self.engine()
        for args in (
            ("next_year",),
            ("custom", "2026-11-01T00:00:00", "2026-11-02T00:00:00+00:00"),
            ("custom", "2026-01-01T00:00:00+00:00", "2026-11-01T00:00:00+00:00"),
            ("custom", "2026-11-02T00:00:00+00:00", "2026-11-01T00:00:00+00:00"),
        ):
            with self.assertRaises(u.ServiceError):
                engine.line_volume(*args, now=self.NOW)
        engine.line_volume("today", now=self.NOW)
        asked = len(self.queries)
        engine.line_volume("today", now=self.NOW + 20)
        self.assertEqual(len(self.queries), asked)
        # A report needs only the totals: one query.
        self.queries.clear()
        totals = engine.line_volume(
            "custom", "2026-11-24T00:00:00+00:00", "2026-11-25T00:00:00+00:00", now=self.NOW, buckets=False
        )
        self.assertEqual((len(self.queries), totals["buckets"]), (1, []))

    def test_reports_list_each_line_and_say_what_is_unknown(self):
        import notifications as n
        from datetime import datetime, timezone

        data = {
            "start": datetime(2026, 11, 24, tzinfo=timezone.utc),
            "end": datetime(2026, 11, 25, tzinfo=timezone.utc),
            "site": "Synthetic site",
            "line_volume": [
                {"interface": "wan1", "download_bytes": 22_500_000_000, "upload_bytes": 18_800_000_000, "coverage_pct": 100.0},
                {"interface": "wan2", "download_bytes": 5_000_000_000, "upload_bytes": 1_000_000_000, "coverage_pct": 60.0},
                {"interface": "lte", "download_bytes": None, "upload_bytes": None, "coverage_pct": 0.0},
            ],
            "usage": None,
            "digest": [],
        }
        text = n.render_report("daily", data, timezone.utc)["text"]
        self.assertIn("Usage per line", text)
        self.assertIn("wan1 · 22.5 GB · 18.8 GB", text)
        self.assertIn("wan2 (60% recorded) · 5.0 GB · 1.0 GB", text)
        self.assertIn("lte · unknown · unknown", text)
        self.assertIn("show less than they carried", text)


if __name__ == "__main__":
    unittest.main()
