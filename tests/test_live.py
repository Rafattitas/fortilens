"""Live usage from FortiGate realtime FortiView statistics.

All FortiView payloads are synthetic; no test contacts a device.
"""

import base64
import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest import mock
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting as a
import usage_service as u
from test_accounting import CONFIG, INVENTORY
from test_applications import labelled

ROOT = Path(__file__).resolve().parents[1]


def source(ip, mac, srcintf, dstintf, rx, tx, sessions=3, apps=()):
    return {
        "srcaddr": ip,
        "srcmac": mac,
        "srcintf": srcintf,
        "dstintf": dstintf,
        "rx_bandwidth": rx,
        "tx_bandwidth": tx,
        "sessions": sessions,
        "apps": [{"id": i, "count": c} for i, c in apps],
    }


def application(appid, dstintf, rx, tx, sessions=2, name=None):
    entry = {"id": appid, "count": sessions}
    if name:
        entry["name"] = name
    return {
        "apps": [entry],
        "dstintf": dstintf,
        "srcintf": "staff",
        "rx_bandwidth": rx,
        "tx_bandwidth": tx,
        "sessions": sessions,
    }


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.store = a.Store(":memory:", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(self.store.close)

    def test_application_ids_from_records_name_live_rows(self):
        self.store.ingest(
            [
                labelled(
                    sessionid="c1", app="ChatGPT", appcat="GenAI", apprisk="medium", appid="41540"
                ),
                labelled(
                    sessionid="c2", app="Video", appcat="Video/Audio", apprisk="elevated", appid="0"
                ),
            ]
        )
        self.assertEqual(
            self.store.app_catalog(["41540", "0", "x"]),
            {"41540": {"app": "ChatGPT", "category": "GenAI", "risk": "medium"}},
        )
        # The ID is descriptive only: identities and totals do not change.
        plain = a.normalize(labelled(app="ChatGPT"), "src")
        rich = a.normalize(labelled(app="ChatGPT", appid="41540"), "src")
        self.assertEqual((plain["uid"], plain["delivery_uid"]), (rich["uid"], rich["delivery_uid"]))

    def test_live_counts_only_wan_traffic_and_names_known_devices(self):
        self.store.ingest([labelled(sessionid="k1", appid="41540", app="ChatGPT", appcat="GenAI")])
        by_mac, by_ip = self.store.recent_identities()
        known = next(iter(by_mac.values()))
        rows = [
            source(
                known["ip"],
                known["mac"].upper(),
                "staff",
                "wan1",
                8_000_000,
                1_000_000,
                apps=[(41540, 5), (7, 1)],
            ),
            source(known["ip"], known["mac"], "staff", "wan2", 2_000_000, 500_000),
            source("192.0.2.77", "", "staff", "wan1", 400, 100),
            source("192.0.2.80", "02:00:00:00:00:80", "staff", "port3", 9_999_999, 9_999_999),
        ]
        apps = [
            application(41540, "wan1", 7_000_000, 900_000),
            application(0, "wan1", 300, 50, name="UDP/9930"),
            application(99, "port3", 5, 5),
        ]
        live = u.build_live(
            rows,
            apps,
            ["wan1", "wan2"],
            INVENTORY,
            "root",
            by_mac,
            by_ip,
            self.store.app_catalog(["41540"]),
            1_800_000_000,
        )
        self.assertEqual(
            live["totals"],
            {"download_bps": 10_000_400, "upload_bps": 1_500_100, "sessions": 9, "devices": 2},
        )
        first = live["devices"][0]
        self.assertEqual(
            (first["device_id"], first["download_bps"], first["top_apps"][0]),
            (known["device_id"], 10_000_000, "ChatGPT"),
        )
        self.assertEqual(first["vlan"]["status"], "MAPPED")
        self.assertIsNone(live["devices"][1]["device_id"])
        self.assertEqual(
            [(x["interface"], x["download_bps"]) for x in live["links"]],
            [("wan1", 8_000_400), ("wan2", 2_000_000)],
        )
        self.assertEqual([x["app"] for x in live["applications"]], ["ChatGPT", "UDP/9930"])
        self.assertEqual(live["applications"][0]["category"], "GenAI")
        # Without interface counters the totals are only the listed sessions, and say so.
        self.assertEqual((live["totals_source"], live["truncated"]), ("LISTED_SESSIONS", False))
        with mock.patch.object(u, "LIVE_ROWS", 4):
            self.assertTrue(
                u.build_live(rows, apps, ["wan1"], INVENTORY, "root", by_mac, by_ip, {}, 0)[
                    "truncated"
                ]
            )

    def test_malformed_fortiview_payloads_are_rejected(self):
        for payload in ({"results": "x"}, {"results": {"details": [1]}}, []):
            with self.subTest(payload=payload), self.assertRaises(u.ServiceError):
                u.fv_rows(payload)
        self.assertEqual(u.fv_rows({"results": {"details": []}}), [])
        self.assertEqual(u.fv_rows({"results": [{"srcaddr": "x"}]}), [{"srcaddr": "x"}])

    def test_live_route_is_allowlisted_cached_and_authenticated(self):
        self.store.ingest([labelled(sessionid="r1", appid="41540", app="ChatGPT", appcat="GenAI")])
        calls = []

        counters = {"rx": 0, "tx": 0}

        def fake_fetch(base, route, params=None, **kwargs):
            if route == u.INTERFACE_ROUTE:
                # 1,000 bytes down and 250 up per reading: 8,000 and 2,000 bits per second at one second.
                counters["rx"] += 1000
                counters["tx"] += 250
                calls.append((route, None, params["vdom"], None))
                return {
                    "status": "success",
                    "results": {
                        "wan1": {"rx_bytes": counters["rx"], "tx_bytes": counters["tx"]},
                        "port3": {"rx_bytes": 9, "tx_bytes": 9},
                    },
                }
            calls.append(
                (route, params["report_by"], params["vdom"], params["realtime"], params["sort_by"])
            )
            if params["report_by"] == "source":
                return {
                    "status": "success",
                    "results": {
                        "details": [
                            source("192.0.2.10", "02:00:00:00:00:01", "staff", "wan1", 800, 200)
                        ]
                    },
                }
            return {
                "status": "success",
                "results": {"details": [application(41540, "wan1", 800, 200)]},
            }

        with tempfile.TemporaryDirectory() as root:
            token = Path(root, "token")
            token.write_text("synthetic-token\n")
            engine = u.Engine(self.store, token_file=str(token), config_root=root, fetch=fake_fetch)
            engine.config = {
                **engine.config,
                "fortigate_host": "192.0.2.1",
                "api_port": 443,
                "vdom": "root",
                "wan_interfaces": ["wan1"],
                "confirmed_wan_interfaces": ["wan1"],
                "api_tls_verify": True,
            }
            clock = iter([0.0, 1.0] + [float(x) for x in range(11, 400, 10)])
            engine.interface_rates = lambda _clock, real=engine.interface_rates: real(
                lambda: next(clock), pause=0
            )
            first = engine.live(now=100.0)
            again = engine.live(now=103.0)
            self.assertEqual(first, again)
            fortiview = [c for c in calls if c[0] == u.LIVE_ROUTE]
            self.assertEqual(len(fortiview), 2)
            # Sorted by bandwidth so a bounded page holds the busiest sources.
            self.assertEqual({c[4] for c in fortiview}, {"bandwidth"})
            self.assertEqual(first["totals_source"], "INTERFACE_COUNTERS")
            self.assertEqual(
                (first["totals"]["download_bps"], first["totals"]["upload_bps"]), (8000, 2000)
            )
            self.assertEqual(
                first["links"],
                [
                    {
                        "interface": "wan1",
                        "download_bps": 8000,
                        "upload_bps": 2000,
                        "measured_seconds": 1.0,
                    }
                ],
            )
            self.assertEqual(first["listed"], {"download_bps": 800, "upload_bps": 200})
            self.assertFalse(first["truncated"])
            engine.live(now=106.0)
            self.assertEqual(len([c for c in calls if c[0] == u.LIVE_ROUTE]), 4)
            self.assertEqual(first["applications"][0]["app"], "ChatGPT")
            Path(root, "reporting.html").write_text("<h1>FortiLens</h1>")
            server = ThreadingHTTPServer(
                ("127.0.0.1", 0), u.handler(engine, "fixture-password-123456", root)
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = "http://127.0.0.1:" + str(server.server_port) + "/api/live"
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(url)
                self.assertEqual(caught.exception.code, 401)
                auth = "Basic " + base64.b64encode(b"admin:fixture-password-123456").decode()
                with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"Authorization": auth})
                ) as response:
                    result = json.load(response)
                self.assertEqual(result["source"], "FORTIVIEW_REALTIME")
                self.assertEqual(result["totals_source"], "INTERFACE_COUNTERS")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
        with self.assertRaises(u.ServiceError):
            engine.read_device("/api/v2/monitor/firewall/session", {})

    def test_console_and_page_expose_live_usage(self):
        import portal

        self.assertIn("/api/live", portal.REPORT_ROUTES)
        page = (ROOT / "portable/reporting.html").read_text(encoding="utf-8")
        self.assertIn('data-view="live"', page)
        self.assertIn("api('/api/live')", page)
        self.assertIn("if(route==='/api/live')", page)
        index = (ROOT / "management/static/index.html").read_text(encoding="utf-8")
        script = (ROOT / "management/static/app.js").read_text(encoding="utf-8")
        server = (ROOT / "management/app.py").read_text(encoding="utf-8")
        self.assertIn('href="#live" data-page="live"', index)
        self.assertIn("live:'/console/?section=live#live'", script)
        # Compare the server source without whitespace or quote style, so code
        # formatting cannot break these checks.
        compact = re.sub(r"\s+", "", server).replace('"', "'")
        self.assertIn("'live':'/console/?section=live#live'", compact)
        self.assertIn("'live'", re.search(r"EMBEDDED_VIEWS=\{([^}]*)\}", compact).group(1))


if __name__ == "__main__":
    unittest.main()
