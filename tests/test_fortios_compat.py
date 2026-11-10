"""FortiOS version coverage and Live usage on firmware that answers differently.

The reply shapes follow what FortiOS 7.2.14 returned on a real device: every
REST reply names the firmware, an unsupported sort is silently ignored, and an
unknown route is HTTP 404. All payloads here are synthetic.
"""

import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting as a
import doctor
import usage_service as u
from test_accounting import CONFIG, INVENTORY
from test_applications import labelled
from test_live import application, source

ROOT = Path(__file__).resolve().parents[1]
FIRMWARE = {"status": "success", "serial": "FGTSYNTHETIC0001", "version": "v7.4.5", "build": 2702}


def not_found(route):
    return urllib.error.HTTPError(route, 404, "Not Found", {}, None)


class FirmwareTests(unittest.TestCase):
    def setUp(self):
        self.store = a.Store(":memory:", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(self.store.close)
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        Path(self.root.name, "token").write_text("synthetic-token\n")
        self.calls = []
        self.counters = 0

    def engine(self, fortiview=None, interfaces=True, applications=True):
        """An engine whose FortiGate answers like `fortiview` rows, or 404 when None."""

        def fetch(base, route, params=None, **kwargs):
            self.calls.append(route)
            if route == u.INTERFACE_ROUTE:
                if not interfaces:
                    raise not_found(route)
                self.counters += 1000
                return {
                    **FIRMWARE,
                    "results": {
                        "wan1": {"rx_bytes": self.counters, "tx_bytes": self.counters // 4}
                    },
                }
            if route == "/api/v2/monitor/system/status":
                return {**FIRMWARE, "results": {"model_name": "FortiGate"}}
            if fortiview is None or (params["report_by"] == "application" and not applications):
                raise not_found(route)
            rows = (
                fortiview
                if params["report_by"] == "source"
                else [application(41540, "wan1", 800, 200)]
            )
            return {**FIRMWARE, "results": {"details": rows}}

        engine = u.Engine(
            self.store,
            token_file=str(Path(self.root.name, "token")),
            config_root=self.root.name,
            fetch=fetch,
        )
        engine.config = {
            **engine.config,
            "fortigate_host": "192.0.2.1",
            "api_port": 443,
            "vdom": "root",
            "wan_interfaces": ["wan1"],
            "confirmed_wan_interfaces": ["wan1"],
            "api_tls_verify": True,
        }
        ticks = iter(float(x) for x in range(0, 10_000, 10))
        engine.interface_rates = lambda _clock, real=engine.interface_rates: real(
            lambda: next(ticks), pause=0
        )
        return engine

    def test_support_levels_are_explicit(self):
        self.assertEqual(u.fortios_support("v7.2.14")["level"], "TESTED")
        self.assertEqual(u.fortios_support("7.2.9")["level"], "SAME_BRANCH")
        self.assertEqual(u.fortios_support("v7.4.5")["level"], "UNTESTED")
        self.assertEqual(u.fortios_support(None)["level"], "UNKNOWN")
        self.assertEqual(u.fortios_support("7.4")["level"], "UNKNOWN")

    def test_rows_are_checked_for_fields_and_order(self):
        busy, quiet = (
            source("192.0.2.1", "", "staff", "wan1", 900, 100),
            source("192.0.2.2", "", "staff", "wan1", 10, 0),
        )
        self.assertEqual(u.check_live_rows([busy, quiet]), ([], True))
        self.assertEqual(u.check_live_rows([quiet, busy]), ([], False))
        renamed = [{k: v for k, v in busy.items() if k != "rx_bandwidth"}]
        self.assertEqual(u.check_live_rows(renamed), (["rx_bandwidth"], True))
        self.assertEqual(u.check_live_rows([]), ([], True))

    def test_ignored_sort_is_detected_and_rows_sorted_here(self):
        rows = [
            source("192.0.2.2", "", "staff", "wan1", 10, 0),
            source("192.0.2.1", "", "staff", "wan1", 900, 100),
        ]
        live = self.engine(rows).live(now=100.0)
        self.assertEqual([d["ip"] for d in live["devices"]], ["192.0.2.1", "192.0.2.2"])
        compat = live["compatibility"]
        self.assertEqual((compat["fortiview"], compat["sorted_by_device"]), ("OK", False))
        # The firmware is read from the FortiView reply itself.
        self.assertEqual(
            (compat["fortios"]["version"], compat["fortios"]["build"], compat["fortios"]["level"]),
            ("7.4.5", 2702, "UNTESTED"),
        )

    def test_missing_fortiview_keeps_link_totals(self):
        engine = self.engine(None)
        live = engine.live(now=100.0)
        self.assertEqual(live["devices"], [])
        self.assertEqual(live["totals_source"], "INTERFACE_COUNTERS")
        self.assertEqual(live["totals"]["download_bps"], 800)
        compat = live["compatibility"]
        self.assertEqual(
            (compat["fortiview"], compat["fortiview_error"], compat["interface_counters"]),
            ("UNAVAILABLE", "http_404", "OK"),
        )

    def test_missing_parts_are_reported_not_fatal(self):
        rows = [source("192.0.2.1", "", "staff", "wan1", 900, 100)]
        live = self.engine(rows, interfaces=False, applications=False).live(now=100.0)
        self.assertEqual(live["totals_source"], "LISTED_SESSIONS")
        self.assertEqual(live["totals"]["download_bps"], 900)
        self.assertEqual(
            (live["compatibility"]["applications"], live["compatibility"]["interface_counters"]),
            ("UNAVAILABLE", "UNAVAILABLE"),
        )
        renamed = [{k: v for k, v in rows[0].items() if k not in ("rx_bandwidth", "tx_bandwidth")}]
        live = self.engine(renamed).live(now=100.0)
        self.assertEqual(
            (live["compatibility"]["fortiview"], live["compatibility"]["missing_fields"]),
            ("FIELDS_MISSING", ["rx_bandwidth", "tx_bandwidth"]),
        )

    def test_nothing_readable_is_still_an_error(self):
        engine = self.engine(None, interfaces=False)
        with self.assertRaises(u.ServiceError):
            engine.live(now=100.0)
        self.assertEqual(engine.live_compatibility["fortiview"], "UNAVAILABLE")

    def test_diagnostics_read_the_firmware_and_back_off_on_failure(self):
        engine = self.engine([source("192.0.2.1", "", "staff", "wan1", 900, 100)])
        details = engine.fortigate_diagnostics(now=1000.0)
        self.assertEqual(
            (details["version"], details["level"], details["live"]["fortiview"]),
            ("7.4.5", "UNTESTED", "OK"),
        )
        self.assertEqual(self.calls.count("/api/v2/monitor/system/status"), 1)
        before = len(self.calls)
        engine.fortigate_diagnostics(now=1200.0)
        self.assertEqual(len(self.calls), before, "fresh results are reused")
        # A FortiGate that cannot be read is retried after five minutes, not on every request.
        broken = self.engine(None, interfaces=False)
        broken.fetch = lambda *args, **kwargs: (_ for _ in ()).throw(not_found("x"))
        self.assertEqual(broken.fortigate_diagnostics(now=1000.0)["error"], "http_404")
        attempts = broken._device_attempt
        broken.fortigate_diagnostics(now=1100.0)
        self.assertIs(broken._device_attempt, attempts)
        broken.fortigate_diagnostics(now=1400.0)
        self.assertIsNot(broken._device_attempt, attempts)

    def test_doctor_reports_version_live_parts_and_application_ids(self):
        tested = {
            "api_configured": True,
            "version": "7.2.14",
            "build": 1778,
            **u.fortios_support("7.2.14"),
            "live": {
                "fortiview": "OK",
                "missing_fields": [],
                "sorted_by_device": True,
                "applications": "OK",
                "interface_counters": "OK",
            },
        }
        checks = {c["id"]: c for c in doctor.fortios_checks(tested, app_ids_seen=12)}
        self.assertEqual(
            (checks["fortios_version"]["status"], checks["live_usage_api"]["status"]),
            ("PASS", "PASS"),
        )
        self.assertIn("Build 1778", checks["fortios_version"]["message"])
        self.assertNotIn("application_ids", checks)
        other = {
            **tested,
            "version": "7.4.5",
            **u.fortios_support("7.4.5"),
            "live": {
                "fortiview": "UNAVAILABLE",
                "fortiview_error": "http_404",
                "missing_fields": [],
                "sorted_by_device": None,
                "applications": "UNAVAILABLE",
                "interface_counters": "OK",
            },
        }
        checks = {c["id"]: c for c in doctor.fortios_checks(other, app_ids_seen=0)}
        self.assertEqual(checks["fortios_version"]["status"], "WARN")
        self.assertIn("http_404", checks["live_usage_api"]["message"])
        self.assertNotIn("application statistics", checks["live_usage_api"]["message"])
        self.assertEqual(checks["application_ids"]["status"], "WARN")
        unknown = doctor.fortios_checks(
            {"api_configured": True, "error": "timeout", **u.fortios_support(None), "live": None}
        )
        self.assertEqual(
            [(c["id"], c["status"]) for c in unknown], [("fortios_version", "UNKNOWN")]
        )
        self.assertEqual(doctor.fortios_checks({"api_configured": False}), [])

    def test_store_counts_learned_application_ids(self):
        self.assertEqual(self.store.diagnostics()["app_ids_seen"], 0)
        self.store.ingest([labelled(sessionid="c1", app="ChatGPT", appcat="GenAI", appid="41540")])
        self.assertEqual(self.store.diagnostics()["app_ids_seen"], 1)

    def test_live_page_explains_missing_parts(self):
        page = (ROOT / "portable/reporting.html").read_text(encoding="utf-8")
        self.assertIn('id="live-compat"', page)
        for text in (
            "did not return realtime device statistics",
            "ignored the bandwidth sort",
            "Application statistics are unavailable",
        ):
            self.assertIn(text, page)


if __name__ == "__main__":
    unittest.main()
