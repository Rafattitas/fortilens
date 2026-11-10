"""A FortiGate clock ahead of this server, a busy reader and a slow raw scan each end with their reason."""

import base64
import http.client
import json
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import accounting
import accounting_storage
import doctor
import portal
import usage_service as usage
from test_accounting import CONFIG, INVENTORY, record

NS = accounting.NS
FIRST, NEWEST = "2026-10-20T12:00:00Z", "2026-10-20T12:09:00Z"
PASSWORD = "synthetic-report-password"


def at(text):
    return accounting.timestamp_ns(text)


class ClockAheadReportTests(unittest.TestCase):
    """The FortiGate's newest records carry later times than this server's clock."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="fortilens-clock-")
        self.addCleanup(folder.cleanup)
        self.store = accounting.Store(Path(folder.name) / "usage.sqlite", CONFIG, deepcopy(INVENTORY))
        self.addCleanup(self.store.close)
        self.store.ingest([record(FIRST), record(NEWEST, sessionid="11")])
        with self.store.db:
            self.store.set_state("inventory", deepcopy(INVENTORY))
        self.engine = usage.Engine(self.store)
        self.addCleanup(self.engine.close)
        self.scans = []
        for ledger in (accounting_storage.LegacyLedger, accounting_storage.CompactLedger):
            original = ledger.range_facts

            def counted(instance, *args, _original=original, **kwargs):
                self.scans.append(args)
                return _original(instance, *args, **kwargs)

            patcher = patch.object(ledger, "range_facts", counted)
            patcher.start()
            self.addCleanup(patcher.stop)

    def server_clock(self, text):
        return patch.object(usage.time, "time_ns", return_value=at(text))

    def test_today_covers_records_ahead_of_this_server_from_daily_summaries(self):
        self.assertEqual(self.store.newest_record_ns(), at(NEWEST))
        with self.server_clock("2026-10-20T12:00:30Z"):
            result = self.engine.report("today")
        # Both records, read from the daily summary: no raw-record scan.
        self.assertEqual(result["totals"]["download_bytes"], 400)
        self.assertEqual(self.scans, [])
        self.assertEqual(result["source_clock_ahead_seconds"], 510)

    def test_an_explicit_end_is_kept_and_reads_raw_records(self):
        # The previous behaviour for every period ending now: the newest record
        # falls after the end, so the day is split and raw records are read.
        result = self.engine.report("today", at="2026-10-20T12:00:30Z")
        self.assertEqual(result["totals"]["download_bytes"], 200)
        self.assertTrue(self.scans)
        self.assertEqual(result["source_clock_ahead_seconds"], 0)

    def test_a_server_clock_ahead_of_the_fortigate_changes_nothing(self):
        with self.server_clock("2026-10-20T12:30:00Z"):
            result = self.engine.report("today")
        self.assertEqual(result["totals"]["download_bytes"], 400)
        self.assertEqual(self.scans, [])
        self.assertEqual(result["source_clock_ahead_seconds"], 0)

    def test_recent_five_minutes_end_at_the_newest_record(self):
        with self.server_clock("2026-10-20T12:00:30Z"):
            result = self.engine.report("recent")
        self.assertEqual(result["totals"]["download_bytes"], 200)

    def test_applications_and_websites_use_the_same_period(self):
        with self.server_clock("2026-10-20T12:00:30Z"):
            self.assertEqual(self.engine.applications("today")["end_utc"], "2026-10-20T12:09:01+00:00")
            self.engine.websites("today")
        self.assertEqual(self.scans, [])

    def test_a_busy_reader_answers_with_its_reason_instead_of_queueing(self):
        held, release = threading.Event(), threading.Event()

        def hold():
            with self.engine.read_lock:
                held.set()
                release.wait(10)

        thread = threading.Thread(target=hold)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(5))
        with patch.object(usage, "READ_WAIT_SECONDS", 0.05), self.assertRaises(usage.ServiceError) as busy:
            self.engine.report("today")
        self.assertEqual(str(busy.exception), "reports_busy")
        release.set()
        thread.join()
        self.assertEqual(self.engine.report("today", at=NEWEST)["totals"]["download_bytes"], 200)

    def test_a_raw_scan_past_its_budget_stops_with_its_reason(self):
        with patch.object(usage, "REPORT_SCAN_SECONDS", -1), self.assertRaises(usage.ServiceError) as slow:
            self.engine.report("custom", start="2026-10-20T12:05:00Z", end="2026-10-20T12:10:00Z")
        self.assertEqual(str(slow.exception), "report_scan_too_long")
        # The read lock is released; the next request is served.
        self.assertEqual(self.engine.report("custom", start="2026-10-20T12:05:00Z", end="2026-10-20T12:10:00Z")["totals"]["download_bytes"], 200)

    def test_accounting_deadline_only_applies_to_raw_scans(self):
        past = 0.0
        with self.assertRaises(accounting.ReportTooLarge):
            self.store.report("2026-10-20T12:05:00Z", "2026-10-20T12:10:00Z", deadline=past)
        # Whole days come from summaries and never hit the deadline.
        self.assertEqual(self.store.report("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z", deadline=past)["totals"]["download_bytes"], 400)


class ErrorMessageTests(unittest.TestCase):
    """Each reason reaches the reader: worker, portal relay and console gateway."""

    def server(self, handler):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def close():
            server.shutdown()
            server.server_close()
            thread.join()

        self.addCleanup(close)
        return server

    def request(self, server, path, headers):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", path, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_worker_names_busy_and_too_long(self):
        store = accounting.Store(":memory:", CONFIG, INVENTORY)
        self.addCleanup(store.close)
        engine = usage.Engine(store)
        with tempfile.TemporaryDirectory() as root:
            server = self.server(usage.handler(engine, PASSWORD, root))
            auth = {"Authorization": "Basic " + base64.b64encode(("admin:" + PASSWORD).encode()).decode()}
            for code, status in (("reports_busy", 503), ("report_scan_too_long", 400)):
                with self.subTest(code=code), patch.object(engine, "report", side_effect=usage.ServiceError(code)):
                    self.assertEqual(self.request(server, "/api/report", auth),
                                     (status, {"error": code, "message": usage.ERROR_MESSAGES[code]}))

    def test_portal_relays_the_busy_reason_and_nothing_else(self):
        self.assertEqual(portal.REPORT_FAILURE_MESSAGES["reports_busy"], usage.ERROR_MESSAGES["reports_busy"])
        gateway = portal.Portal(PASSWORD, ".")
        server = self.server(portal.handler(gateway))
        headers = {"Authorization": gateway.report_auth}
        busy = {"error": "reports_busy", "message": usage.ERROR_MESSAGES["reports_busy"]}
        for payload, expected in ((busy, busy), ({"error": "reports_busy", "message": "Injected text"}, {"error": "upstream_unavailable"})):
            with self.subTest(payload=payload):
                response = Mock(status=503)
                response.read.return_value = json.dumps(payload).encode()
                response.getheaders.return_value = []
                connection = Mock()
                connection.getresponse.return_value = response
                with patch.object(gateway, "connect", return_value=connection):
                    self.assertEqual(self.request(server, "/api/report", headers), (503, expected))


class ClockCheckTests(unittest.TestCase):
    NOW = 1800000000

    def request(self, value):
        calls = []

        def answer(p, c, route, method="GET", body=None, timeout=5):
            calls.append(body)
            assert route == "/api/ds/query" and "eventtime" in body["queries"][0]["expr"]
            frames = [] if value is None else [{"schema": {"fields": [{"type": "time"}, {"type": "number"}]},
                                                "data": {"values": [[self.NOW * 1000], [value]]}}]
            return {"results": {"A": {"status": 200, "frames": frames}}}

        return answer, calls

    def check(self, value, receipt=True):
        answer, _ = self.request(value)
        return doctor.clock_check(".", {"loki_receive_timestamps": receipt}, answer, "loki", self.NOW)

    def test_offsets_are_measured_in_any_eventtime_unit(self):
        for value, status in (((self.NOW + 5) * 10**9, "PASS"), ((self.NOW - 30) * 10**6, "PASS"),
                              ((self.NOW + 120) * 10**9, "WARN"), ((self.NOW + 525) * 10**9, "FAIL"),
                              ((self.NOW - 900) * 1000, "FAIL"), (self.NOW + 90, "WARN")):
            with self.subTest(value=value):
                self.assertEqual(self.check(value)["status"], status)

    def test_the_message_names_direction_size_and_the_fix(self):
        ahead = self.check((self.NOW + 525) * 10**9)
        self.assertEqual(ahead["message"], "FortiGate log times are 8 min 45 s ahead of this server's clock.")
        self.assertIn("chronyc makestep", ahead["action"])
        self.assertEqual(ahead["evidence"]["offset_seconds"], 525.0)
        behind = self.check((self.NOW - 90) * 10**9)
        self.assertEqual(behind["message"], "FortiGate log times are 90 seconds behind this server's clock.")

    def test_unmeasurable_clocks_are_not_reported_as_in_sync(self):
        self.assertEqual(self.check(None)["status"], "UNKNOWN")
        self.assertEqual(self.check(None, receipt=False)["status"], "INFO")


if __name__ == "__main__":
    unittest.main()
