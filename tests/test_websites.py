"""Website usage: opt-in, joined from UTM hostnames to traffic sessions.

FortiGate traffic logs carry bytes without a hostname; Application Control and
Web Filter logs carry the hostname and the same session key. All records here
are synthetic.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accounting as a
import setup
from test_accounting import CONFIG, INVENTORY, record

ROOT = Path(__file__).resolve().parents[1]
DAY = ("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z")
LATER = "2026-10-25T12:00:00Z"


def utm(hostname, at="2026-10-20T11:59:59Z", subtype="app-ctrl", **fields):
    """An Application Control (or Web Filter) record for the default traffic session."""
    item = record(at, **fields)
    for key in ("sentdelta", "rcvddelta", "duration"):
        item["fields"].pop(key, None)
    item["fields"].update(type="utm", subtype=subtype, logid="1059028704", hostname=hostname, app="WhatsApp")
    return item


def site_total(store):
    return {row["site"]: (row["upload_bytes"], row["download_bytes"]) for row in store.websites(*DAY, now=LATER)["sites"]}


class SiteNameTests(unittest.TestCase):
    def test_registrable_site_only(self):
        cases = {"mail.google.com": "google.com", "WWW.Example.COM.": "example.com", "a.b.example.co.uk": "example.co.uk",
                 "portal.gov.eg": "portal.gov.eg", "www.vodafone.com.eg": "vodafone.com.eg", "example.com:443": "example.com",
                 "cdn-1.akamaized.net": "akamaized.net", "example.com": "example.com"}
        for host, site in cases.items():
            with self.subTest(host=host):
                self.assertEqual(a.site_name(host), site)
        for host in ("10.0.0.1", "[2001:db8::1]", "2001:db8::1", "intranet", "bad host.com", "a..com", "", "x.123"):
            with self.subTest(host=host):
                self.assertIsNone(a.site_name(host))


class WebsiteTests(unittest.TestCase):
    def store(self, enabled=True):
        store = a.Store(":memory:", {**CONFIG, "accounting_websites": enabled}, INVENTORY)
        self.addCleanup(store.close)
        if enabled:
            with store.db:
                # Count from before the fixtures; a real store counts from when it is switched on.
                store.set_state("websites_since_ns", 0)
        return store

    def test_hostname_links_to_the_traffic_session_without_changing_identities(self):
        traffic = a.normalize(record(), "fixture")
        event = a.normalize(utm("web.whatsapp.com"), "fixture")
        self.assertEqual(event["site"], ("whatsapp.com", traffic["session"]))
        self.assertNotIn("site", traffic)
        self.assertNotIn("hostname", event["fields"])
        plain = utm("web.whatsapp.com")
        plain["fields"].pop("hostname")
        self.assertEqual(a.normalize(plain, "fixture")["uid"], event["uid"])
        # Only Application Control and Web Filter records name websites.
        self.assertNotIn("site", a.normalize(utm("x.example.com", subtype="ips"), "fixture"))

    def test_site_rows_carry_exactly_the_session_counted_bytes(self):
        store = self.store()
        store.ingest([utm("web.whatsapp.com"), record(), record("2026-10-20T12:01:00Z", sentdelta="50", rcvddelta="70", duration="120")])
        daily = store.report(*DAY)["totals"]
        self.assertEqual(site_total(store), {"whatsapp.com": (daily["upload_bytes"], daily["download_bytes"])})
        self.assertEqual(site_total(store)["whatsapp.com"], (150, 270))

    def test_a_late_hostname_moves_counted_bytes_without_double_counting(self):
        store = self.store()
        store.ingest([record()])
        self.assertEqual(site_total(store), {})
        store.ingest([utm("web.whatsapp.com", at="2026-10-20T12:00:30Z")])
        self.assertEqual(site_total(store), {"whatsapp.com": (100, 200)})
        # More records of the session re-derive its facts; the site row follows exactly.
        store.ingest([record("2026-10-20T12:01:00Z", sentdelta="50", rcvddelta="70", duration="120")])
        self.assertEqual(site_total(store), {"whatsapp.com": (150, 270)})
        # The first site seen for a session is kept.
        store.ingest([utm("www.example.com", at="2026-10-20T12:02:00Z")])
        self.assertEqual(set(site_total(store)), {"whatsapp.com"})
        # Repeated deliveries change nothing.
        store.ingest([record(), utm("web.whatsapp.com", at="2026-10-20T12:00:30Z")])
        self.assertEqual(site_total(store), {"whatsapp.com": (150, 270)})

    def test_switched_off_records_nothing(self):
        store = self.store(enabled=False)
        store.ingest([utm("web.whatsapp.com"), record()])
        self.assertEqual(store.db.execute("SELECT count(*) FROM session_site").fetchone()[0], 0)
        result = store.websites(*DAY, now=LATER)
        self.assertEqual((result["enabled"], result["sites"]), (False, []))
        self.assertIsNone(store.state("websites_since_ns"))

    def test_usage_before_switching_on_is_not_counted(self):
        store = self.store()
        with store.db:
            store.set_state("websites_since_ns", a.timestamp_ns("2026-10-20T12:00:30Z"))
        store.ingest([utm("web.whatsapp.com"), record(), record("2026-10-20T12:01:00Z", sentdelta="50", rcvddelta="70", duration="120")])
        self.assertEqual(site_total(store), {"whatsapp.com": (50, 70)})
        # On the switch-on day, coverage compares only usage from that moment.
        self.assertEqual(store.websites(*DAY, now=LATER)["summary"]["coverage"], 1.0)

    def test_report_lists_devices_and_coverage(self):
        store = self.store()
        other = dict(sessionid="11", srcport="2000", mastersrcmac="02:00:00:00:00:02", srcname="laptop")
        store.ingest([utm("web.whatsapp.com"), record(), utm("www.youtube.com", **other), record(**other, sentdelta="1000", rcvddelta="9000"),
                      record(sessionid="12", srcport="3000", sentdelta="5", rcvddelta="5")])
        result = store.websites(*DAY, now=LATER, site="youtube.com")
        self.assertEqual([x["site"] for x in result["sites"]], ["youtube.com", "whatsapp.com"])
        self.assertEqual(result["summary"]["top_site"], "youtube.com")
        self.assertEqual([(d["hostname"], d["total_bytes"]) for d in result["devices"]], [("laptop", 10000)])
        self.assertEqual(result["devices"][0]["vlans"][0]["key"], "vlan:100:staff")
        self.assertAlmostEqual(result["summary"]["coverage"], 10300/10310)
        only = store.websites(*DAY, now=LATER, device=result["devices"][0]["device_id"])
        self.assertEqual([x["site"] for x in only["sites"]], ["youtube.com"])

    def test_periods_are_whole_days(self):
        store = self.store()
        with self.assertRaises(ValueError):
            store.websites("2026-10-20T06:00:00Z", "2026-10-21T00:00:00Z", now=LATER)
        with self.assertRaises(ValueError):
            store.websites("2026-10-20T00:00:00Z", "2026-10-20T15:00:00Z", now=LATER)
        # Today so far is the day in progress.
        today = store.websites("2026-10-20T00:00:00Z", "2026-10-20T15:00:00Z", now="2026-10-20T15:00:00Z")
        self.assertEqual((today["first_day"], today["last_day"]), ("2026-10-20", "2026-10-20"))
        week = store.websites("2026-10-14T00:00:00Z", "2026-10-21T00:00:00Z", now=LATER)
        self.assertEqual((week["first_day"], week["last_day"]), ("2026-10-14", "2026-10-20"))

    def test_rows_expire_after_ninety_days_and_links_with_the_ledger(self):
        store = self.store()
        store.ingest([utm("web.whatsapp.com"), record()])
        store.prune("2027-01-17T00:00:00Z", ledger_days=3, summary_days=400)
        self.assertEqual(store.db.execute("SELECT count(*) FROM session_site").fetchone()[0], 0)
        self.assertEqual(store.db.execute("SELECT count(*) FROM site_daily").fetchone()[0], 1)
        store.prune("2027-01-19T00:00:00Z", ledger_days=3, summary_days=400)
        self.assertEqual(store.db.execute("SELECT count(*) FROM site_daily").fetchone()[0], 0)
        self.assertEqual(store.db.execute("SELECT count(*) FROM daily").fetchone()[0], 1)


class WebsiteSettingsTests(unittest.TestCase):
    def test_switch_is_off_by_default_and_needs_accounting(self):
        self.assertFalse(setup.DEFAULTS["accounting_websites"])
        with self.assertRaises(setup.SetupError):
            setup.validate_config({"accounting_websites": True, "accounting_enabled": False})
        try:
            from management.models import Settings
        except ImportError:
            self.skipTest("management dependencies are not installed")
        settings = Settings.model_validate({"fortigate_host": "192.0.2.1", "vdom": "root", "sla": "", "wan_interfaces": [],
                                            "timezone": "UTC", "accounting_timezone": "UTC"})
        self.assertFalse(settings.monitoring.websites)

    def test_console_and_page_offer_websites(self):
        page = (ROOT / "portable/reporting.html").read_text(encoding="utf-8")
        for text in ('id="sites-section"', "/api/websites", "Website totals are kept per day"):
            self.assertIn(text, page)
        console = (ROOT / "management/static/index.html").read_text(encoding="utf-8")
        self.assertIn('data-setting="monitoring.websites"', console)
        self.assertIn('data-page="websites"', console)


if __name__ == "__main__":
    unittest.main()
