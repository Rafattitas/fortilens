"""Application usage must reuse exact accounting bytes without changing identities.

All records are synthetic fixtures; no test contacts a device or installation.
"""

import base64
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting as a
import usage_service as u
from test_accounting import CONFIG, INVENTORY, record

DAY = ("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z")


def labelled(at="2026-10-20T12:00:00Z", app="YouTube", appcat="Video/Audio", apprisk="elevated", **fields):
    item = record(at, **fields)
    for key, value in (("app", app), ("appcat", appcat), ("apprisk", apprisk)):
        if value is not None:
            item["fields"][key] = value
    item["fields"]["dstcountry"] = "United States"
    return item


def mixed():
    return [
        labelled(sessionid="1", sentdelta="100", rcvddelta="1000"),
        labelled("2026-10-20T12:01:00Z", sessionid="1", logid="0000000013", sentdelta="50", rcvddelta="500", duration="120"),
        labelled(sessionid="2", app="ChatGPT", appcat="GenAI", apprisk="medium", mastersrcmac="02:00:00:00:00:02",
                 sentdelta="300", rcvddelta="30"),
        labelled(sessionid="3", app=None, appcat="unscanned", apprisk=None, mastersrcmac="02:00:00:00:00:03",
                 sentdelta="7", rcvddelta="9"),
        labelled(sessionid="4", subtype="local", logid="0001000014"),
    ]


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.store = a.Store(":memory:", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(self.store.close)

    def test_labels_never_change_event_or_delivery_identity(self):
        plain = record()
        rich = labelled()
        first, second = a.normalize(plain, "src"), a.normalize(rich, "src")
        self.assertEqual((first["uid"], first["delivery_uid"], first["session"]),
                         (second["uid"], second["delivery_uid"], second["session"]))
        self.assertNotIn("meta", first)
        self.assertEqual(second["meta"], {"app": "YouTube", "appcat": "Video/Audio", "apprisk": "elevated",
                                          "dstcountry": "United States"})
        line = 'type=traffic subtype=forward app="Microsoft Teams" appcat=Collaboration sessionid=9'
        self.assertEqual(a.parse_line(line)["app"], "Microsoft Teams")
        self.assertNotIn("meta", a.normalize(labelled(subtype="local", logid="0001000014"), "src"))

    def test_application_totals_equal_device_totals_for_whole_days_and_partial_windows(self):
        self.store.ingest(mixed())
        for start, end in (DAY, ("2026-10-20T11:59:00Z", "2026-10-20T12:00:30Z")):
            with self.subTest(start=start, end=end):
                report = self.store.report(start, end)
                apps = self.store.applications(start, end)
                self.assertEqual(apps["totals"], report["totals"])
        apps = self.store.applications(*DAY)
        names = [(x["app"], x["category"], x["total_bytes"], x["devices"]) for x in apps["applications"]]
        self.assertEqual(names, [("YouTube", "Video/Audio", 1650, 1), ("ChatGPT", "GenAI", 330, 1),
                                 (None, "unscanned", 16, 1)])
        self.assertEqual(apps["applications"][0]["risk"], "elevated")
        self.assertEqual(apps["unidentified_bytes"], 16)
        self.assertEqual(apps["applications_since"], "2026-10-20")
        self.assertEqual({c["category"]: c["total_bytes"] for c in apps["categories"]},
                         {"Video/Audio": 1650, "GenAI": 330, "unscanned": 16})
        self.assertEqual(apps["devices"], [])

    def test_application_and_category_selection_lists_devices(self):
        self.store.ingest(mixed())
        chosen = self.store.applications(*DAY, app="ChatGPT")
        self.assertEqual([d["mac"] for d in chosen["devices"]], ["02:00:00:00:00:02"])
        self.assertEqual(chosen["devices"][0]["total_bytes"], 330)
        genai = self.store.applications(*DAY, category="GenAI")
        self.assertEqual([d["total_bytes"] for d in genai["devices"]], [330])
        unknown = self.store.applications(*DAY, app="")
        self.assertEqual([d["mac"] for d in unknown["devices"]], ["02:00:00:00:00:03"])
        device = chosen["devices"][0]["device_id"]
        mine = self.store.applications(*DAY, device=device)
        self.assertEqual([x["app"] for x in mine["applications"]], ["ChatGPT"])

    def test_redelivery_after_upgrade_is_not_counted_twice(self):
        old = [record(sessionid="5", sentdelta="40", rcvddelta="60")]
        self.store.ingest(deepcopy(old))
        before = self.store.report(*DAY)["totals"]
        again = labelled(sessionid="5", sentdelta="40", rcvddelta="60")
        result = self.store.ingest([again])
        self.assertEqual(result["new_events"], 0)
        self.assertEqual(self.store.report(*DAY)["totals"], before)
        apps = self.store.applications(*DAY)
        # Recorded before labels were kept: reported apart, never as unidentified.
        self.assertEqual(apps["applications"], [])
        self.assertEqual(apps["untracked"]["total_bytes"], 100)
        self.assertIsNone(apps["tracking_started_ns"])

    def test_late_session_records_recompute_without_duplicate_application_rows(self):
        rows = mixed()
        self.store.ingest(rows[1:])
        self.store.ingest(rows[:1])
        self.store.ingest(deepcopy(rows))
        apps = self.store.applications(*DAY)
        self.assertEqual(apps["totals"], self.store.report(*DAY)["totals"])
        count = self.store.db.execute("SELECT COUNT(*) FROM app_daily").fetchone()[0]
        self.assertEqual(count, 3)

    def test_legacy_and_compact_storage_agree(self):
        legacy = a.Store(":memory:", CONFIG, INVENTORY)
        self.addCleanup(legacy.close)
        for store in (legacy, self.store):
            store.ingest(mixed())
        for window in (DAY, ("2026-10-20T11:59:00Z", "2026-10-20T12:00:30Z")):
            self.assertEqual(legacy.applications(*window), self.store.applications(*window))

    def test_pruning_keeps_whole_days_and_refuses_expired_partial_detail(self):
        self.store.ingest(mixed())
        self.store.prune("2026-10-29T00:00:00Z", ledger_days=3)
        self.assertEqual(self.store.applications(*DAY)["totals"]["upload_bytes"], 457)
        with self.assertRaisesRegex(ValueError, "expired"):
            self.store.applications("2026-10-20T11:00:00Z", "2026-10-20T13:00:00Z")
        self.store.prune("2027-12-01T00:00:00Z", ledger_days=3, summary_days=31)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM app_daily").fetchone()[0], 0)

    def test_older_file_without_summaries_reads_as_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "older.sqlite"
            writer = a.Store(path, CONFIG, INVENTORY, storage_format="compact")
            writer.ingest([record()])
            writer.db.execute("DROP TABLE app_daily")
            writer.db.commit()
            writer.close()
            with closing(a.Store.open_readonly(path, CONFIG, INVENTORY)) as reader:
                result = reader.applications(*DAY)
            self.assertEqual(result["status"], "UNAVAILABLE")
            self.assertIsNone(result["applications_since"])
            with closing(a.Store(path, CONFIG, INVENTORY)) as reopened:
                self.assertIsNotNone(reopened.db.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='app_daily'").fetchone())

    def test_http_route_requires_auth_and_accepts_unidentified_selection(self):
        self.store.ingest(mixed())
        engine = u.Engine(self.store)
        with tempfile.TemporaryDirectory() as root:
            Path(root, "reporting.html").write_text("<h1>FortiLens</h1>")
            server = ThreadingHTTPServer(("127.0.0.1", 0), u.handler(engine, "fixture-password-123456", root))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = "http://127.0.0.1:" + str(server.server_port) + "/api/applications"
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base)
                self.assertEqual(caught.exception.code, 401)
                auth = "Basic " + base64.b64encode(b"admin:fixture-password-123456").decode()
                query = "?period=custom&start=2026-10-20T00:00:00Z&end=2026-10-21T00:00:00Z&app="
                with urllib.request.urlopen(urllib.request.Request(base + query, headers={"Authorization": auth})) as response:
                    result = json.load(response)
                self.assertEqual(result["period"], "custom")
                self.assertEqual([d["mac"] for d in result["devices"]], ["02:00:00:00:00:03"])
                self.assertIn("processing", result)
                bad = urllib.request.Request(base + "?period=never", headers={"Authorization": auth})
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(bad)
                self.assertEqual(caught.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_portal_and_report_page_expose_the_route(self):
        import portal

        self.assertIn("/api/applications", portal.REPORT_ROUTES)
        page = (Path(__file__).resolve().parents[1] / "portable" / "reporting.html").read_text(encoding="utf-8")
        self.assertIn("api('/api/applications?'", page)
        self.assertIn('data-view="applications"', page)
        self.assertIn("['devices','vlans','reports','applications','websites','live'].includes(requestedSection)", page)

    def test_management_console_navigation_opens_the_applications_view(self):
        # The console hides the report page's own menu, so it needs its own entry.
        root = Path(__file__).resolve().parents[1] / "management"
        index = (root / "static" / "index.html").read_text(encoding="utf-8")
        script = (root / "static" / "app.js").read_text(encoding="utf-8")
        server = (root / "app.py").read_text(encoding="utf-8")
        self.assertIn('href="#applications" data-page="applications"', index)
        self.assertIn("applications:['Applications'", script)
        self.assertIn("'applications','live','quality'].includes(page)?f.accounting", script)
        # Without its own frame URL the console falls back to the Reports view.
        self.assertIn("applications:'/console/?section=applications#applications'", script)
        self.assertIn("'applications'", server.split("EMBEDDED_VIEWS=", 1)[1].split("\n", 1)[0])
        self.assertIn("'applications':'/console/?section=applications#applications'", server)
        self.assertIn("section in ('devices','vlans','reports','applications','websites','live')", server)


class ApplicationViewTests(unittest.TestCase):
    def setUp(self):
        self.store = a.Store(":memory:", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(self.store.close)

    def test_untracked_bytes_are_separate_and_complete_the_device_totals(self):
        self.store.ingest([record(sessionid="old", sentdelta="40", rcvddelta="60")])
        self.store.ingest(mixed())
        apps = self.store.applications(*DAY)
        report = self.store.report(*DAY)
        self.assertNotIn(None, [x["category"] for x in apps["applications"]])
        self.assertEqual(apps["untracked"]["upload_bytes"] + apps["totals"]["upload_bytes"], report["totals"]["upload_bytes"])
        self.assertEqual(apps["untracked"]["download_bytes"] + apps["totals"]["download_bytes"], report["totals"]["download_bytes"])
        self.assertEqual(apps["tracking_started_ns"], a.timestamp_ns("2026-10-20T12:00:00Z"))

    def test_protocol_only_labels_are_generic_and_summary_is_consistent(self):
        rows = mixed() + [labelled(sessionid="q", app="QUIC", appcat="Network.Service", apprisk="low",
                                   mastersrcmac="02:00:00:00:00:09", sentdelta="5000", rcvddelta="5000"),
                          labelled(sessionid="t", app="SSL_TLSv1.3", appcat="Network.Service", apprisk="medium",
                                   mastersrcmac="02:00:00:00:00:09", sentdelta="1", rcvddelta="1")]
        self.store.ingest(rows)
        apps = self.store.applications(*DAY)
        generic = {x["app"] for x in apps["applications"] if x["generic"]}
        self.assertEqual(generic, {"QUIC", "SSL_TLSv1.3"})
        summary = apps["summary"]
        self.assertEqual(summary["top_application"], "YouTube")
        self.assertEqual(summary["generic_bytes"], 10002)
        self.assertEqual(summary["unidentified_bytes"], 16)
        self.assertEqual(summary["identified_bytes"] + summary["generic_bytes"] + summary["unidentified_bytes"], summary["total_bytes"])
        self.assertEqual((summary["genai_bytes"], summary["genai_devices"]), (330, 1))
        self.assertEqual(summary["applications"], len(apps["applications"]))

    def test_selected_application_returns_daily_and_monthly_history(self):
        self.store.ingest(mixed())
        self.store.ingest([labelled("2026-10-21T08:00:00Z", sessionid="next-day", sentdelta="10", rcvddelta="20")])
        apps = self.store.applications("2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z", app="YouTube", history=True)
        self.assertEqual([(x["period"], x["upload_bytes"]+x["download_bytes"]) for x in apps["daily_history"]],
                         [("2026-10-20", 1650), ("2026-10-21", 30)])
        self.assertEqual([(x["period"], x["observed_days"]) for x in apps["monthly_history"]], [("2026-10", 2)])
        self.assertNotIn("daily_history", self.store.applications(*DAY))

    def test_selected_devices_are_one_row_per_endpoint_with_their_segments(self):
        self.store.ingest([labelled(sessionid="v1", srcintf="staff"), labelled(sessionid="v2", srcintf="guests")])
        chosen = self.store.applications(*DAY, app="YouTube")
        self.assertEqual(len(chosen["devices"]), 1)
        self.assertEqual(len(chosen["devices"][0]["vlans"]), 2)
        self.assertEqual(chosen["devices"][0]["total_bytes"], 600)
        self.assertTrue(a.GENERIC_APP.fullmatch("SSL_TLSv1.3.PQC"))
        self.assertFalse(a.GENERIC_APP.fullmatch("Microsoft.365.Portal"))

    def test_device_profile_labels_follow_the_latest_record(self):
        self.store.ingest([labelled(sessionid="p1", osname="Android", srchwvendor="Samsung", devtype="Mobile"),
                           labelled("2026-10-20T12:05:00Z", sessionid="p2", osname="Android 14", srchwvendor="Samsung")])
        self.store.ingest([labelled("2026-10-20T11:00:00Z", sessionid="p0", osname="Old OS", srchwvendor="Old")])
        device = self.store.report(*DAY)["devices"][0]
        self.assertEqual(device["profile"], {"os": "Android 14", "vendor": "Samsung"})
        chosen = self.store.applications(*DAY, app="YouTube")
        self.assertEqual(chosen["devices"][0]["profile"], device["profile"])
        plain = a.normalize(record(), "src")
        rich = a.normalize(labelled(osname="Android", srchwvendor="Samsung", devtype="Mobile"), "src")
        self.assertEqual(plain["uid"], rich["uid"])

    def test_today_so_far_uses_day_rows_without_decoding_every_record(self):
        rows = mixed() + [labelled("2026-10-20T15:00:00Z", sessionid="9", app="Zoom", appcat="Collaboration",
                                   mastersrcmac="02:00:00:00:00:09", sentdelta="20", rcvddelta="40")]
        self.store.ingest(rows)
        for start, end in (("2026-10-20T00:00:00Z", "2026-10-20T18:00:00Z"), ("2026-10-20T00:00:00Z", "2026-10-20T12:00:30Z"),
                           ("2026-10-20T12:00:30Z", "2026-10-21T00:00:00Z"), ("2026-10-20T11:59:00Z", "2026-10-20T15:30:00Z")):
            with self.subTest(start=start, end=end):
                self.assertEqual(self.store.applications(start, end)["totals"], self.store.report(start, end)["totals"])
        calls = []
        original = self.store.ledger.range_facts_labels
        self.store.ledger.range_facts_labels = lambda *args: calls.append(args) or original(*args)
        apps = self.store.applications("2026-10-20T00:00:00Z", "2026-10-20T18:00:00Z")
        self.assertEqual(calls, [])
        self.assertEqual({x["app"] for x in apps["applications"]}, {"YouTube", "ChatGPT", None, "Zoom"})
        # A window ending inside a row's events still reads the retained records.
        self.store.applications("2026-10-20T00:00:00Z", "2026-10-20T12:00:30Z")
        self.assertEqual(len(calls), 1)
        # The day application rows began: part of its records predate them, so the
        # retained facts stay the source and totals still equal device totals.
        with self.store.db:
            self.store.db.execute("DELETE FROM app_daily WHERE app='Zoom'")
        self.assertFalse(self.store._app_day_complete("2026-10-20"))
        window = ("2026-10-20T00:00:00Z", "2026-10-20T18:00:00Z")
        self.assertEqual(self.store.applications(*window)["totals"], self.store.report(*window)["totals"])
        self.assertEqual(len(calls), 2)


class InventoryMappingTests(unittest.TestCase):
    def test_unchanged_mapping_keeps_its_first_verification_time(self):
        first = a.carry_mapping_since({"interfaces": []}, {**INVENTORY, "collected_at": "2026-10-20T00:00:00Z"})
        self.assertEqual(first["mapping_verified_since"], "2026-10-20T00:00:00Z")
        later = a.carry_mapping_since(first, {**deepcopy(INVENTORY), "collected_at": "2026-10-20T05:00:00Z"})
        self.assertEqual(later["mapping_verified_since"], "2026-10-20T00:00:00Z")
        changed = deepcopy(INVENTORY)
        changed["interfaces"][0]["vlan_id"] = 999
        moved = a.carry_mapping_since(later, {**changed, "collected_at": "2026-10-20T06:00:00Z"})
        self.assertEqual(moved["mapping_verified_since"], "2026-10-20T06:00:00Z")
        # Without an earlier verification the saved inventory's own time is the bound.
        legacy = a.carry_mapping_since({**INVENTORY, "collected_at": "2026-10-19T00:00:00Z"},
                                       {**INVENTORY, "collected_at": "2026-10-20T00:00:00Z"})
        self.assertEqual(legacy["mapping_verified_since"], "2026-10-19T00:00:00Z")

    def test_records_after_an_unchanged_refresh_keep_their_vlan(self):
        store = a.Store(":memory:", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(store.close)
        store.inventory = a.carry_mapping_since({"interfaces": []}, {**INVENTORY, "collected_at": "2026-10-20T00:00:00Z"})
        store.inventory = a.carry_mapping_since(store.inventory, {**INVENTORY, "collected_at": "2026-10-20T13:00:00Z"})
        store.ingest([labelled(sessionid="r1")])
        vlan = store.report(*DAY)["rows"][0]["vlan"]
        self.assertEqual(vlan["status"], "MAPPED")
        stale = a.Store(":memory:", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(stale.close)
        stale.inventory = {**INVENTORY, "collected_at": "2026-10-20T13:00:00Z"}
        stale.ingest([labelled(sessionid="r1")])
        self.assertEqual(stale.report(*DAY)["rows"][0]["vlan"]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
