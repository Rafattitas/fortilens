"""Accounting days that follow the FortiGate clock, and viewer-local display time."""

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import accounting as a

sys.path.insert(0, str(ROOT / "tests"))
from test_accounting import CONFIG, INVENTORY, record  # noqa: E402

FOLLOW = {**CONFIG, "accounting_timezone": "fortigate"}
NS = 1_000_000_000


def at(text):
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def days(store):
    return [row[0] for row in store.db.execute("SELECT DISTINCT day FROM daily ORDER BY day")]


class OffsetTests(unittest.TestCase):
    def test_fortigate_tz_field(self):
        self.assertEqual(a.parse_utc_offset("+0200"), 7200)
        self.assertEqual(a.parse_utc_offset("-0330"), -12600)
        self.assertEqual(a.parse_utc_offset("+05:45"), 20700)
        for bad in ("", "0200", "+2", "+1500", "+0260", None):
            self.assertIsNone(a.parse_utc_offset(bad), bad)
        self.assertEqual(a.offset_label(7200), "UTC+02:00")
        self.assertEqual(a.offset_label(-12600), "UTC-03:30")

    def test_offset_never_changes_event_identity(self):
        plain, stamped = record(), record(tz="+0200")
        self.assertEqual(
            a.normalize(plain, "fixture")["uid"], a.normalize(stamped, "fixture")["uid"]
        )
        self.assertEqual(a.normalize(stamped, "fixture")["utc_offset"], 7200)
        self.assertNotIn("utc_offset", a.normalize(plain, "fixture"))


class TimelineTests(unittest.TestCase):
    def test_fixed_offset_day_boundaries(self):
        zone = a.ZoneTimeline.load([[None, 7200]])
        self.assertEqual(
            datetime.fromtimestamp(at("2026-11-08T21:59:59Z"), zone).date(), date(2026, 11, 8)
        )
        self.assertEqual(
            datetime.fromtimestamp(at("2026-11-08T22:00:00Z"), zone).date(), date(2026, 11, 9)
        )
        first, last = a.period_bounds("today", "2026-11-09T10:00:00Z", zone)
        self.assertEqual(first, at("2026-11-08T22:00:00Z") * NS)

    def test_eastward_change_shortens_only_the_last_old_day(self):
        zone = a.ZoneTimeline.load([[None, "UTC"], ["2026-11-09", 7200]])
        # 8 November stays a UTC day but ends where 9 November starts in UTC+2.
        self.assertEqual(
            datetime.fromtimestamp(at("2026-11-08T21:30:00Z"), zone).date(), date(2026, 11, 8)
        )
        self.assertEqual(
            datetime.fromtimestamp(at("2026-11-08T22:30:00Z"), zone).date(), date(2026, 11, 9)
        )
        start8 = a.period_bounds("today", "2026-11-08T12:00:00Z", zone)[0]
        start9 = a.period_bounds("today", "2026-11-09T12:00:00Z", zone)[0]
        self.assertEqual(start8, at("2026-11-08T00:00:00Z") * NS)
        self.assertEqual(start9, at("2026-11-08T22:00:00Z") * NS)
        self.assertEqual((start9 - start8) // NS, 22 * 3600)

    def test_westward_change_lengthens_the_last_old_day(self):
        zone = a.ZoneTimeline.load([[None, 10800], ["2026-11-09", 7200]])
        start8 = a.period_bounds("today", "2026-11-08T12:00:00Z", zone)[0]
        start9 = a.period_bounds("today", "2026-11-09T12:00:00Z", zone)[0]
        self.assertEqual((start9 - start8) // NS, 25 * 3600)
        # The extra hour, 21:00-22:00 UTC, still belongs to 8 November.
        extra = datetime.fromtimestamp(at("2026-11-08T21:30:00Z"), zone)
        self.assertEqual(extra.date(), date(2026, 11, 8))
        self.assertEqual(a.period_bounds("today", "2026-11-08T21:30:00Z", zone)[0], start8)

    def test_week_spans_a_change(self):
        zone = a.ZoneTimeline.load([[None, "UTC"], ["2026-11-09", 7200]])
        first, last = a.period_bounds("week", "2026-11-10T12:00:00Z", zone)
        self.assertEqual(first, at("2026-11-04T00:00:00Z") * NS)


class FollowFortiGateTests(unittest.TestCase):
    def test_new_store_takes_the_first_reported_offset(self):
        store = a.Store(":memory:", FOLLOW, INVENTORY)
        self.addCleanup(store.close)
        store.ingest([record("2026-11-08T23:30:00Z", tz="+0200")])
        self.assertEqual(store.state("zone_timeline"), [[None, 7200]])
        self.assertEqual(days(store), ["2026-11-09"])
        report = store.report("2026-11-08T22:00:00Z", "2026-11-09T22:00:00Z")
        self.assertEqual(report["timezone"], "Etc/GMT-2")
        self.assertEqual(report["timezone_label"], "FortiGate time (UTC+02:00)")

    def test_a_changed_offset_applies_from_the_next_local_day(self):
        store = a.Store(":memory:", FOLLOW, INVENTORY)
        self.addCleanup(store.close)
        store.ingest([record("2026-11-08T10:00:00Z", tz="+0200")])
        # The FortiGate moves to UTC+03:00 in the afternoon of 8 November.
        store.ingest([record("2026-11-08T14:00:00Z", sessionid="11", tz="+0300")])
        self.assertEqual(store.state("zone_timeline"), [[None, 7200], ["2026-11-09", 10800]])
        # 22:30 at UTC+02:00 is still 8 November on the old offset; 9 November
        # starts at midnight UTC+03:00, 21:00 UTC, so 8 November has 23 hours.
        store.ingest([record("2026-11-08T20:30:00Z", sessionid="12", tz="+0300")])
        self.assertEqual(days(store), ["2026-11-08"])
        store.ingest([record("2026-11-08T21:30:00Z", sessionid="13", tz="+0300")])
        self.assertEqual(days(store), ["2026-11-08", "2026-11-09"])
        rows = dict(store.db.execute("SELECT day, COUNT(*) FROM daily GROUP BY day").fetchall())
        self.assertEqual(rows, {"2026-11-08": 1, "2026-11-09": 1})

    def test_reverting_before_the_change_cancels_it(self):
        store = a.Store(":memory:", FOLLOW, INVENTORY)
        self.addCleanup(store.close)
        store.ingest([record("2026-11-08T10:00:00Z", tz="+0200")])
        store.ingest([record("2026-11-08T11:00:00Z", sessionid="11", tz="+0300")])
        store.ingest([record("2026-11-08T12:00:00Z", sessionid="12", tz="+0200")])
        self.assertEqual(store.state("zone_timeline"), [[None, 7200]])

    def test_late_records_do_not_move_the_timeline_back(self):
        store = a.Store(":memory:", FOLLOW, INVENTORY)
        self.addCleanup(store.close)
        store.ingest([record("2026-11-08T12:00:00Z", tz="+0300")])
        store.ingest([record("2026-11-08T09:00:00Z", sessionid="11", tz="+0200")])
        self.assertEqual(store.state("zone_timeline"), [[None, 10800]])

    def test_existing_utc_store_switches_forward_only(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.sqlite"
            store = a.Store(path, CONFIG, INVENTORY)
            store.ingest([record("2026-11-08T23:30:00Z", sessionid="1")])
            store.close()
            self.assertEqual(days_of(path, CONFIG), ["2026-11-08"])
            # Stores close inside the folder's lifetime; Windows cannot delete open files.
            follow = a.Store(path, FOLLOW, INVENTORY)
            try:
                self.assertEqual(follow.state("zone_timeline"), [[None, "UTC"]])
                self.assertEqual(follow.state("contract")["accounting_timezone"], "fortigate")
                follow.ingest([record("2026-11-09T08:00:00Z", sessionid="2", tz="+0200")])
                self.assertEqual(
                    follow.state("zone_timeline"), [[None, "UTC"], ["2026-11-10", 7200]]
                )
                # 9 November is still a UTC day; 10 November starts at 22:00 UTC.
                follow.ingest([record("2026-11-09T22:30:00Z", sessionid="3", tz="+0200")])
                self.assertEqual(days(follow), ["2026-11-08", "2026-11-09", "2026-11-10"])
            finally:
                follow.close()
            reader = a.Store.open_readonly(path, FOLLOW, INVENTORY)
            try:
                self.assertEqual(
                    datetime.fromtimestamp(at("2026-11-09T22:30:00Z"), reader.zone()).date(),
                    date(2026, 11, 10),
                )
            finally:
                reader.close()

    def test_other_contract_changes_and_going_back_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.sqlite"
            a.Store(path, FOLLOW, INVENTORY).close()
            with self.assertRaises(ValueError):
                a.Store(path, CONFIG, INVENTORY)
            with self.assertRaises(ValueError):
                a.Store(path, {**FOLLOW, "vdom": "other"}, INVENTORY)
            # Switching zones never permits changing a bound serial.
            utc = Path(folder) / "utc.sqlite"
            a.Store(utc, {**CONFIG, "expected_devid": "FG-FIXTURE"}, INVENTORY).close()
            with self.assertRaises(ValueError):
                a.Store(utc, {**FOLLOW, "expected_devid": "FG-OTHER"}, INVENTORY)

    def test_fixed_zones_keep_their_behaviour(self):
        store = a.Store(":memory:", CONFIG, INVENTORY)
        self.addCleanup(store.close)
        store.ingest([record("2026-11-08T23:30:00Z", tz="+0200")])
        self.assertIsNone(store.state("zone_timeline"))
        self.assertEqual(days(store), ["2026-11-08"])
        report = store.report("2026-11-08T00:00:00Z", "2026-11-09T00:00:00Z")
        self.assertEqual((report["timezone"], report["timezone_label"]), ("UTC", "UTC"))


def days_of(path, config):
    store = a.Store.open_readonly(path, config, INVENTORY)
    try:
        return days(store)
    finally:
        store.close()


class SettingsTests(unittest.TestCase):
    def setUp(self):
        try:
            sys.path.insert(0, str(ROOT))
            from management.models import Settings
        except ImportError as error:  # management dependencies are optional here
            self.skipTest(str(error))
        self.Settings = Settings

    def test_defaults_follow_viewer_and_fortigate(self):
        fields = self.Settings.model_fields
        self.assertEqual(fields["timezone"].default, "browser")
        self.assertEqual(fields["accounting_timezone"].default, "fortigate")

    def test_console_formats_browser_as_the_viewer_zone(self):
        script = (ROOT / "management/static/app.js").read_text(encoding="utf-8")
        self.assertIn(
            "function displayZone(){const zone=state.view?.settings.timezone;return zone&&zone!=='browser'?zone:undefined}",
            script,
        )
        self.assertNotIn("settings.timezone||'UTC'", script)


if __name__ == "__main__":
    unittest.main()
