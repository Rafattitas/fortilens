"""People: usage by the person the FortiGate names on each traffic record.

A FortiGate that learns sign-ins (FSSO, for example Active Directory polling)
writes the signed-in account in the `user` field of traffic logs, or in
`unauthuser` when no policy asked for the sign-in. All names and records here
are synthetic.
"""

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accounting as a
import portal
from test_accounting import CONFIG, INVENTORY, record

ROOT = Path(__file__).resolve().parents[1]
DAY = ("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z")
LATER = "2026-10-25T12:00:00Z"
LAPTOP = dict(sessionid="11", srcport="2000", mastersrcmac="02:00:00:00:00:02", srcname="laptop")


def totals(store, **kw):
    return {
        row["person"]: (row["upload_bytes"], row["download_bytes"])
        for row in store.people(*DAY, now=LATER, **kw)["people"]
    }


class PersonNameTests(unittest.TestCase):
    def test_names_are_case_folded_and_bounded(self):
        self.assertEqual(a.person_name({"user": " J.Rivera "}), "j.rivera")
        self.assertEqual(a.person_name({"user": "EXAMPLE\\M.Chen"}), "example\\m.chen")
        self.assertEqual(a.person_name({"unauthuser": "S.NOVAK"}), "s.novak")
        # The account a policy asked for wins over a passively learned one.
        self.assertEqual(a.person_name({"user": "a.okafor", "unauthuser": "x"}), "a.okafor")
        for labels in ({}, {"user": ""}, {"user": "   "}, {"user": "x" * 257}, {"user": "a\x00b"}):
            with self.subTest(labels=labels):
                self.assertIsNone(a.person_name(labels))

    def test_a_name_is_a_label_and_never_changes_an_identity(self):
        named = a.normalize(record(user="j.rivera"), "fixture")
        plain = a.normalize(record(), "fixture")
        self.assertEqual(
            (named["uid"], named["delivery_uid"], named["session"]),
            (plain["uid"], plain["delivery_uid"], plain["session"]),
        )
        self.assertEqual(named["meta"], {"user": "j.rivera"})
        self.assertNotIn("user", named["fields"])
        parsed = a.parse_line('type="traffic" unauthuser="S.NOVAK" srcip=192.0.2.10')
        self.assertEqual(parsed["unauthuser"], "S.NOVAK")


class PeopleTests(unittest.TestCase):
    def store(self):
        store = a.Store(":memory:", CONFIG, INVENTORY)
        self.addCleanup(store.close)
        return store

    def test_person_rows_carry_exactly_the_counted_bytes_of_named_records(self):
        store = self.store()
        store.ingest(
            [
                record(user="J.Rivera"),
                record(
                    "2026-10-20T12:01:00Z",
                    sentdelta="50",
                    rcvddelta="70",
                    duration="120",
                    user="j.rivera",
                ),
            ]
        )
        daily = store.report(*DAY)["totals"]
        self.assertEqual(
            totals(store), {"j.rivera": (daily["upload_bytes"], daily["download_bytes"])}
        )
        self.assertEqual(totals(store)["j.rivera"], (150, 270))
        # Repeated deliveries change nothing.
        store.ingest([record(user="j.rivera")])
        self.assertEqual(totals(store), {"j.rivera": (150, 270)})

    def test_records_that_name_nobody_add_nothing(self):
        store = self.store()
        store.ingest([record()])
        result = store.people(*DAY, now=LATER)
        self.assertEqual(result["people"], [])
        self.assertIsNone(result["since_ns"])
        self.assertIsNone(result["summary"]["coverage"])
        self.assertIsNone(result["summary"]["total_bytes"])
        # A session named only on its later record counts only that record's bytes.
        store.ingest(
            [record("2026-10-20T12:01:00Z", sentdelta="50", rcvddelta="70", user="m.chen")]
        )
        self.assertEqual(totals(store), {"m.chen": (50, 70)})
        self.assertEqual(store.report(*DAY)["totals"]["upload_bytes"], 150)

    def test_one_device_used_by_two_people_is_split_by_record(self):
        store = self.store()
        store.ingest(
            [
                record(**LAPTOP, user="a.okafor", sentdelta="10", rcvddelta="90"),
                record(**{**LAPTOP, "sessionid": "12", "srcport": "2001"}, user="s.novak"),
            ]
        )
        self.assertEqual(totals(store), {"a.okafor": (10, 90), "s.novak": (100, 200)})
        result = store.people(*DAY, now=LATER, person="a.okafor")
        self.assertEqual(result["summary"]["devices"], 1)
        self.assertEqual([x["devices"] for x in result["people"]], [1, 1])
        self.assertEqual(result["people"][0]["device_names"], ["laptop"])
        self.assertEqual(
            [(d["hostname"], d["mac"], d["total_bytes"]) for d in result["devices"]],
            [("laptop", "02:00:00:00:00:02", 100)],
        )
        self.assertEqual(result["devices"][0]["vlans"][0]["key"], "vlan:100:staff")

    def test_report_ranks_people_and_measures_coverage(self):
        store = self.store()
        store.ingest(
            [
                record(user="j.rivera"),
                record(**LAPTOP, user="m.chen", sentdelta="1000", rcvddelta="9000"),
                record(sessionid="13", srcport="3000", sentdelta="5", rcvddelta="5"),
            ]
        )
        result = store.people(*DAY, now=LATER)
        self.assertEqual([x["person"] for x in result["people"]], ["m.chen", "j.rivera"])
        self.assertEqual(result["summary"]["top_person"], "m.chen")
        self.assertEqual(result["summary"]["people"], 2)
        self.assertEqual(result["summary"]["total_bytes"], 10300)
        self.assertEqual(result["summary"]["internet_bytes"], 10310)
        self.assertAlmostEqual(result["summary"]["coverage"], 10300 / 10310)
        self.assertEqual(result["since_ns"], a.timestamp_ns("2026-10-20T12:00:00Z"))
        self.assertGreater(result["people"][0]["last_event_ns"], 0)
        device = store.people(*DAY, now=LATER, person="m.chen")["devices"][0]["device_id"]
        only = store.people(*DAY, now=LATER, device=device)
        self.assertEqual([x["person"] for x in only["people"]], ["m.chen"])

    def test_a_device_keeps_the_last_person_a_record_named(self):
        store = self.store()
        store.ingest([record(user="j.rivera", osname="Windows")])
        device = store.report(*DAY)["devices"][0]
        self.assertEqual(device["profile"]["person"], "j.rivera")
        self.assertEqual(device["profile"]["os"], "Windows")
        # Newer detection labels without a name keep the person.
        store.ingest([record("2026-10-20T12:01:00Z", sentdelta="1", rcvddelta="1", osname="Linux")])
        profile = store.profiles([device["device_id"]])[device["device_id"]]
        self.assertEqual((profile["person"], profile["os"]), ("j.rivera", "Linux"))
        # A newer name replaces it; an older one does not.
        store.ingest(
            [
                record("2026-10-20T12:02:00Z", sentdelta="1", rcvddelta="1", user="m.chen"),
                record(
                    "2026-10-20T11:00:00Z",
                    **{**LAPTOP, "mastersrcmac": "02:00:00:00:00:01"},
                    user="old.name",
                ),
            ]
        )
        profile = store.profiles([device["device_id"]])[device["device_id"]]
        self.assertEqual((profile["person"], profile["os"]), ("m.chen", "Linux"))
        self.assertEqual(profile["person_ns"], a.timestamp_ns("2026-10-20T12:02:00Z"))

    def test_periods_are_whole_days(self):
        store = self.store()
        with self.assertRaises(ValueError):
            store.people("2026-10-20T06:00:00Z", "2026-10-21T00:00:00Z", now=LATER)
        today = store.people(
            "2026-10-20T00:00:00Z", "2026-10-20T15:00:00Z", now="2026-10-20T15:00:00Z"
        )
        self.assertEqual((today["first_day"], today["last_day"]), ("2026-10-20", "2026-10-20"))

    def test_rows_expire_with_the_usage_summaries(self):
        store = self.store()
        store.ingest([record(user="j.rivera")])
        count = "SELECT count(*) FROM person_daily"
        store.prune("2027-01-19T00:00:00Z", ledger_days=3, summary_days=400)
        self.assertEqual(store.db.execute(count).fetchone()[0], 1)
        store.prune("2027-11-25T00:00:00Z", ledger_days=3, summary_days=400)
        self.assertEqual(store.db.execute(count).fetchone()[0], 0)
        self.assertEqual(store.db.execute("SELECT count(*) FROM daily").fetchone()[0], 0)

    def test_a_reader_of_an_older_file_reports_nobody(self):
        store = self.store()
        with store.db:
            store.db.execute("DROP TABLE person_daily")
        result = store.people(*DAY, now=LATER)
        self.assertEqual((result["people"], result["summary"]["people"]), ([], 0))


class PeoplePageTests(unittest.TestCase):
    def test_console_page_and_gateway_offer_people(self):
        page = (ROOT / "portable/reporting.html").read_text(encoding="utf-8")
        for text in ('id="people-section"', "/api/people", "Person totals are kept per day"):
            self.assertIn(text, page)
        console = (ROOT / "management/static/index.html").read_text(encoding="utf-8")
        self.assertIn('data-page="people"', console)
        self.assertIn("/api/people", portal.REPORT_ROUTES)
        app = (ROOT / "management/app.py").read_text(encoding="utf-8")
        compact = re.sub(r"\s+", "", app)
        self.assertIn('"people"', re.search(r"EMBEDDED_VIEWS=\{([^}]*)\}", compact).group(1))
        self.assertIn('"people":"/console/?section=people#people"', compact)


if __name__ == "__main__":
    unittest.main()
