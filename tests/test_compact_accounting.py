"""Compact storage must preserve the existing accounting contract exactly.

All inputs, files and concurrent writers in this module are disposable fixtures.
No test connects to a device, Docker engine or an existing installation.
"""

import json
import random
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting as accounting
import test_accounting as fixtures
import usage_service as usage

CONFIG = fixtures.CONFIG
INVENTORY = fixtures.INVENTORY
record = fixtures.record
START = "2026-10-20T00:00:00Z"
END = "2026-10-21T00:00:00Z"
AT = "2026-10-20T13:00:00Z"
CHECKPOINT = accounting.timestamp_ns("2026-10-20T12:01:00Z")


class CompactAccountingBehaviorTests(fixtures.AccountingTests):
    """Run existing behavioral cases against a fresh compact ledger as well."""

    def setUp(self):
        self.store = accounting.Store(
            ":memory:", CONFIG, deepcopy(INVENTORY), storage_format="compact"
        )
        self.addCleanup(self.store.close)

    def test_future_metadata_cannot_certify_historical_direction_or_vlan(self):
        self.store.inventory = {**INVENTORY, "collected_at": "2026-10-21T00:00:00Z"}
        self.store.ingest([record(srcintfrole="", dstintfrole="")])
        result = self.report()
        self.assertIsNone(result["totals"]["upload_bytes"])
        self.assertEqual(result["source_record_classifications"], {"UNRESOLVED_DIRECTION": 1})

    def test_equal_timestamp_stats_precede_closing_cumulative_record(self):
        self.store.ingest(
            [
                record(sentbyte="100", rcvdbyte="200"),
                record(
                    logid="0000000013", sentdelta="", rcvddelta="", sentbyte="150", rcvdbyte="300"
                ),
            ]
        )
        self.assertEqual(self.report()["totals"]["upload_bytes"], 150)
        self.assertEqual(self.report()["totals"]["download_bytes"], 300)
        self.store.prune("2026-10-29T12:00:00Z")
        self.assertIsNone(self.store.diagnostics()["first_retained_event_ns"])

    def test_internal_and_inbound_traffic_are_excluded_without_vanishing_evidence(self):
        self.store.ingest(
            [
                record(dstintf="guests", dstintfrole="lan"),
                record(sessionid="11", srcintfrole="wan"),
            ]
        )
        result = self.report()
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(sum(result["source_record_classifications"].values()), 2)

    def test_empty_is_unknown_not_zero_and_full_days_use_materialized_summaries(self):
        self.assertIsNone(self.report()["totals"]["upload_bytes"])
        self.store.ingest([record()])
        # Expiring detail must retain the existing full-day materialized result.
        self.store.prune("2026-10-29T00:00:00Z")
        self.assertEqual(self.report()["totals"]["upload_bytes"], 100)


class CompactDifferentialTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fortilens-compact-test-")
        self.addCleanup(self.temporary.cleanup)
        directory = Path(self.temporary.name)
        self.legacy_path = directory / "legacy.sqlite"
        self.compact_path = directory / "compact #fixture.sqlite"
        self.legacy = accounting.Store(self.legacy_path, CONFIG, deepcopy(INVENTORY))
        self.compact = accounting.Store(
            self.compact_path, CONFIG, deepcopy(INVENTORY), storage_format="compact"
        )
        self.addCleanup(lambda: self.legacy.close())
        self.addCleanup(lambda: self.compact.close())

    def ingest(self, records, checkpoint=None):
        before = self.legacy.ingest(deepcopy(records), checkpoint=checkpoint)
        after = self.compact.ingest(deepcopy(records), checkpoint=checkpoint)
        self.assertEqual(after, before)

    def test_pruning_multiple_session_pages_preserves_all_open_anchors(self):
        rows = [record(sessionid=str(i), subtype="local", logid="0000000014") for i in range(1200)]
        rows += [record(sessionid="open-" + str(i)) for i in range(20)]
        self.ingest(rows, checkpoint=CHECKPOINT)
        for store in (self.legacy, self.compact):
            store.prune("2026-10-29T12:00:00Z")
        self.compare()
        self.assertEqual(
            self.compact.db.execute("SELECT COUNT(*) FROM compact_events").fetchone()[0], 20
        )

    def compare(self, start=START, end=END):
        for filters in (
            {},
            {"search": "fixture"},
            {"vlan": "vlan:100:staff"},
            {"search": "no endpoint"},
        ):
            with self.subTest(filters=filters, start=start, end=end):
                old = self.legacy.report(start, end, **filters)
                new = self.compact.report(start, end, **filters)
                self.assertEqual(new, old)
                for device in old["devices"]:
                    identity = device["device_id"]
                    self.assertEqual(self.compact.history(identity), self.legacy.history(identity))
                    self.assertEqual(
                        self.compact.history(identity, months=True),
                        self.legacy.history(identity, months=True),
                    )
        self.assertEqual(self.compact.diagnostics(), self.legacy.diagnostics())
        self.assertEqual(self.compact.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_new_files_default_to_legacy_and_existing_compact_files_autodetect(self):
        self.assertEqual(self.legacy.ledger.format, "legacy")
        self.assertEqual(self.compact.ledger.format, "compact")
        self.ingest([record()], CHECKPOINT)
        self.compact.close()
        self.compact = accounting.Store(self.compact_path, CONFIG, deepcopy(INVENTORY))
        self.assertEqual(self.compact.ledger.format, "compact")
        self.compare()

    def test_incompatible_column_order_or_extension_fails_before_writer_initialization(self):
        self.ingest([record()], CHECKPOINT)
        with self.compact.db:
            self.compact.db.execute("ALTER TABLE compact_events ADD COLUMN unsupported TEXT")
        self.compact.close()
        with self.assertRaisesRegex(ValueError, "layout"):
            accounting.Store(self.compact_path, CONFIG, INVENTORY)
        with self.assertRaisesRegex(ValueError, "layout"):
            accounting.Store.open_readonly(self.compact_path, CONFIG, INVENTORY)

    def test_invalid_field_vector_rolls_back_new_ingestion_and_checkpoint(self):
        self.ingest([record()], CHECKPOINT)
        with self.compact.db:
            self.compact.db.execute(
                "UPDATE compact_events SET details=?", (json.dumps(["fixture", [None], {}]),)
            )
        with self.assertRaisesRegex(ValueError, "field vector"):
            self.compact.ingest([record("2026-10-20T12:01:00Z")], checkpoint=CHECKPOINT + 1)
        self.assertEqual(self.compact.state("checkpoint_ns"), CHECKPOINT)
        self.assertEqual(
            self.compact.db.execute("SELECT COUNT(*) FROM compact_events").fetchone()[0], 1
        )

    def test_new_session_shortcut_stops_after_fact_creation_in_the_same_write_unit(self):
        event = accounting.normalize(record(), CONFIG["source_id"])
        ledger = self.compact.ledger
        with self.compact.db, ledger.write_unit():
            ledger.insert_event(event)
            events = ledger.session_events(event["session"])
            self.assertEqual(ledger.session_facts(event["session"]), [])
            fact = accounting.derive_session(events, INVENTORY, CONFIG)[0]
            ledger.insert_fact(fact, event["session"])
            ledger.session_events(event["session"])
            self.assertEqual(ledger.session_facts(event["session"]), [fact])

    def test_field_dictionary_preserves_present_null_missing_and_future_values(self):
        event = accounting.normalize(record(), CONFIG["source_id"])
        event["fields"]["sentdelta"] = None
        event["fields"].pop("rcvddelta")
        event["fields"]["proto"] = ""
        event["fields"]["future-field"] = {"large": 2**128, "values": [None, "", "0"]}
        event["future-property"] = {"kept": True}
        ledger = self.compact.ledger
        with self.compact.db, ledger.write_unit():
            ledger.insert_event(event)
            observed = ledger.session_events(event["session"])[0]
            self.assertEqual(observed, event)
            self.assertIn("sentdelta", observed["fields"])
            self.assertNotIn("rcvddelta", observed["fields"])

    def test_future_fact_properties_and_nested_caller_mutation_are_lossless(self):
        event = accounting.normalize(record(), CONFIG["source_id"])
        ledger = self.compact.ledger
        with self.compact.db, ledger.write_unit():
            ledger.insert_event(event)
            fact = accounting.derive_session(
                ledger.session_events(event["session"]), INVENTORY, CONFIG
            )[0]
            fact["future-property"] = {"large": 2**128, "values": [None, "", "0"]}
            ledger.insert_fact(fact, event["session"])
            observed = ledger.event_fact(fact["uid"])
            self.assertEqual(observed, fact)
            observed["future-property"]["values"].append("caller-added")
            self.assertEqual(ledger.event_fact(fact["uid"]), fact)

    def test_truncated_new_event_selection_cannot_hide_an_existing_fact(self):
        self.ingest([record()], CHECKPOINT)
        event = accounting.normalize(record("2026-10-20T11:59:00Z"), CONFIG["source_id"])
        ledger = self.compact.ledger
        with self.compact.db, ledger.write_unit():
            ledger.insert_event(event)
            self.assertEqual(len(ledger.session_events(event["session"], limit=1)), 1)
            self.assertEqual(len(ledger.session_facts(event["session"])), 1)

    def test_caller_fact_mutation_is_encoded_instead_of_reusing_stale_cached_values(self):
        self.ingest([record()], CHECKPOINT)
        event = accounting.normalize(record(), CONFIG["source_id"])
        ledger = self.compact.ledger
        with self.compact.db, ledger.write_unit():
            fact = ledger.session_facts(event["session"])[0]
            fact["upload_bytes"] += 1
            fact["vlan"]["status"] = "SYNTHETIC_CHANGED"
            fact["flags"].append("synthetic-added-flag")
            ledger.delete_session_facts(event["session"])
            ledger.insert_fact(fact, event["session"])
            self.assertEqual(ledger.event_fact(fact["uid"]), fact)

    def test_wrong_explicit_format_is_rejected_without_mutating_the_file(self):
        self.ingest([record()], CHECKPOINT)
        before = self.compact.report(START, END)
        with self.assertRaises(ValueError):
            accounting.Store(self.compact_path, CONFIG, INVENTORY, storage_format="legacy")
        self.assertEqual(self.compact.report(START, END), before)

    def test_compact_source_contract_changes_are_rejected_without_resetting_data(self):
        self.ingest([record()], CHECKPOINT)
        expected = self.compact.report(START, END)
        for change in (
            {"source_id": "different-source"},
            {"vdom": "different-vdom"},
            {"expected_devid": "FG-DIFFERENT"},
        ):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    accounting.Store(self.compact_path, {**CONFIG, **change}, INVENTORY)
                self.assertEqual(self.compact.report(START, END), expected)

    def test_unknown_compact_format_version_is_rejected(self):
        path = Path(self.temporary.name) / "unknown-format.sqlite"
        with closing(sqlite3.connect(path)) as db:
            db.execute("PRAGMA user_version=1179418623")
        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            accounting.Store(path, CONFIG, INVENTORY)
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0], 0)

    def test_legacy_writer_schema_creation_fails_closed_on_a_compact_database(self):
        # This is the old unversioned initialization sequence, not the new factory.
        ddl = """
        CREATE TABLE IF NOT EXISTS events(uid TEXT PRIMARY KEY,session TEXT NOT NULL,stamp INTEGER NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_session ON events(session,stamp);
        CREATE TABLE IF NOT EXISTS deliveries(uid TEXT PRIMARY KEY,event_uid TEXT NOT NULL,stamp INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS facts(uid TEXT PRIMARY KEY,session TEXT NOT NULL,stamp INTEGER NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS facts_session ON facts(session);
        CREATE INDEX IF NOT EXISTS facts_stamp ON facts(stamp);
        """
        with closing(sqlite3.connect(self.compact_path)) as old_connection:
            with self.assertRaises(sqlite3.DatabaseError):
                old_connection.executescript(ddl)
        self.compare()

    def test_unpaired_unicode_and_embedded_null_roundtrip_without_changing_identity(self):
        self.ingest([record(srcname="fixture-\ud800\x00", sessionid="unicode")], CHECKPOINT)
        self.compare()
        event = self.compact.ledger.session_events(
            accounting.normalize(
                record(srcname="fixture-\ud800\x00", sessionid="unicode"), CONFIG["source_id"]
            )["session"]
        )[0]
        self.assertEqual(event["fields"]["srcname"], "fixture-\ud800\x00")

    def test_unsigned_counters_arbitrary_aggregate_and_raw_lexemes_are_exact(self):
        maximum = str(2**64 - 1)
        cases = [
            record(sessionid="u64-a", sentdelta=maximum, rcvddelta=maximum),
            record(sessionid="u64-b", sentdelta=maximum, rcvddelta=maximum),
            record(sessionid="leading", sentdelta="00000100", rcvddelta="00000000"),
            record(sessionid="invalid", sentdelta="not-a-number", sentbyte="9000"),
            record(sessionid="empty", sentdelta="", rcvddelta=""),
            record(sessionid="overflow", sentdelta=str(2**64), rcvddelta="0"),
        ]
        absent = record(sessionid="absent")
        del absent["fields"]["sentdelta"]
        cases.append(absent)
        self.ingest(cases, CHECKPOINT)
        self.compare()
        total = self.compact.report(START, END)["totals"]["upload_bytes"]
        self.assertGreater(total, 2**64)
        self.assertIs(type(total), int)
        # Replays preserve original lexical IDs rather than rehashing typed values.
        self.ingest(cases, CHECKPOINT + 1)
        self.compare()

    def test_exact_nanosecond_boundaries_and_source_classification_counts(self):
        first = accounting.timestamp_ns("2026-10-20T12:00:00Z")
        records = []
        for offset, subtype in ((0, "forward"), (1, "local"), (2, "forward")):
            item = record(sessionid="nano-" + str(offset), subtype=subtype)
            item.pop("timestamp")
            item["timestamp_ns"] = first + offset
            item["fields"]["eventtime"] = str(first + offset)
            records.append(item)
        self.ingest(records, first + 3)
        self.compare(first, first + 3)
        self.compare(first + 1, first + 2)
        counts = self.compact.report(first + 1, first + 2)["source_record_classifications"]
        self.assertEqual(counts["UNSUPPORTED_RECORD"], 1)
        self.assertEqual(counts.get("INTERNET", 0), 0)

    def test_original_and_replayed_delivery_observations_remain_distinct(self):
        item = record(eventtime="1792497600000000000")
        self.ingest([item], CHECKPOINT)
        replay = {**item, "timestamp": "2026-10-20T12:02:00Z"}
        self.ingest([replay], CHECKPOINT + accounting.NS)
        self.compare()
        self.assertEqual(self.compact.state("replay_candidates"), 1)

    def test_malformed_and_older_than_ledger_deliveries_without_events_are_retained(self):
        self.ingest([record()], CHECKPOINT)
        self.legacy.prune("2026-10-29T00:00:00Z")
        self.compact.prune("2026-10-29T00:00:00Z")
        late = record(srcname="different old observation")
        malformed = {"timestamp": "2026-10-29T00:00:00Z", "fields": {"bad": 3}}
        self.ingest([late, malformed], CHECKPOINT + 2)
        self.ingest([late, malformed], CHECKPOINT + 2)
        self.compare()
        self.assertEqual(self.compact.state("late_rejections"), 1)
        self.assertEqual(self.compact.state("parser_rejections"), 1)

    def test_frozen_mapping_and_endpoint_observations_do_not_follow_current_metadata(self):
        self.ingest([record(sentbyte="100", rcvdbyte="200")], CHECKPOINT)
        self.legacy.inventory = {"interfaces": []}
        self.compact.inventory = {"interfaces": []}
        self.ingest(
            [
                record(
                    "2026-10-20T12:01:00Z",
                    duration="120",
                    srcname="renamed",
                    srcip="192.0.2.99",
                    sentdelta="10",
                    rcvddelta="20",
                    sentbyte="110",
                    rcvdbyte="220",
                ),
            ],
            CHECKPOINT + accounting.NS,
        )
        self.compare()
        rows = self.compact.report(START, END)["rows"]
        self.assertEqual({row["vlan"]["status"] for row in rows}, {"MAPPED", "UNKNOWN"})

    def test_late_arrivals_recompute_existing_sessions_without_recounting(self):
        closing = record(
            "2026-10-20T12:01:00Z",
            logid="0000000013",
            duration="120",
            sentdelta="",
            rcvddelta="",
            sentbyte="150",
            rcvdbyte="300",
        )
        self.ingest([closing], CHECKPOINT)
        self.compare()
        self.ingest([record(sentbyte="100", rcvdbyte="200")], CHECKPOINT + 1)
        self.compare()
        self.assertEqual(self.compact.report(START, END)["totals"]["upload_bytes"], 150)

    def test_pruning_preserves_open_baseline_after_reopen_and_repeated_pruning(self):
        self.ingest([record(sentbyte="100", rcvdbyte="200")], CHECKPOINT)
        for store in (self.legacy, self.compact):
            store.prune("2026-10-29T00:00:00Z")
        self.compact.close()
        self.compact = accounting.Store(self.compact_path, CONFIG, deepcopy(INVENTORY))
        self.ingest(
            [
                record(
                    "2026-10-29T12:00:00Z",
                    duration="777660",
                    logid="0000000013",
                    sentdelta="",
                    rcvddelta="",
                    sentbyte="150",
                    rcvdbyte="300",
                ),
            ],
            CHECKPOINT + 1,
        )
        self.compare("2026-10-29T00:00:00Z", "2026-10-30T00:00:00Z")
        for store in (self.legacy, self.compact):
            store.prune("2026-10-30T00:00:00Z")
        self.compare()

    def test_pruning_removes_expired_detail_but_keeps_anchors_and_recent_evidence(self):
        expired = [
            record(
                sessionid="expired-" + str(index),
                subtype="local",
                logid="0000000014",
                mastersrcmac="",
                duration="",
                srcname="unique-observation-" + str(index),
                srcintf="expired-interface-" + str(index),
            )
            for index in range(60)
        ]
        self.ingest(expired + [record(sentbyte="100", rcvdbyte="200")], CHECKPOINT)
        self.legacy.inventory = {"interfaces": []}
        self.compact.inventory = {"interfaces": []}
        self.ingest(
            [
                record(
                    "2026-10-29T09:00:00Z",
                    sessionid="retained",
                    srcname="retained unknown mapping",
                    mastersrcmac="02:00:00:00:00:07",
                )
            ],
            CHECKPOINT + 1,
        )
        count = lambda: self.compact.db.execute("SELECT COUNT(*) FROM compact_events").fetchone()[0]
        before = count()
        for store in (self.legacy, self.compact):
            store.prune("2026-10-29T00:00:00Z")
        after = count()
        self.assertLess(after, before - 40, "Expired observation detail remains retained")
        self.compare("2026-10-29T00:00:00Z", "2026-10-30T00:00:00Z")
        for _ in range(3):
            for store in (self.legacy, self.compact):
                store.prune("2026-10-29T00:00:00Z")
            self.assertEqual(
                count(), after, "Repeated pruning grows or loses retained event evidence"
            )
        self.ingest(
            [
                record(
                    "2026-10-29T12:00:00Z",
                    duration="777660",
                    logid="0000000013",
                    sentdelta="",
                    rcvddelta="",
                    sentbyte="150",
                    rcvdbyte="300",
                )
            ],
            CHECKPOINT + 2,
        )
        self.compare("2026-10-29T00:00:00Z", "2026-10-30T00:00:00Z")
        self.assertEqual(self.compact.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_failed_transaction_cannot_leave_stale_snapshot_references(self):
        self.ingest([record()], CHECKPOINT)
        expected = self.compact.report(START, END)
        original = self.compact.ledger.insert_fact

        def fail_after_insert(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("synthetic failed transaction")

        failed = record(sessionid="failed", srcname="rolled back", mastersrcmac="02:00:00:00:00:04")
        with patch.object(self.compact.ledger, "insert_fact", side_effect=fail_after_insert):
            with self.assertRaises(RuntimeError):
                self.compact.ingest([failed], checkpoint=CHECKPOINT + 1)
        self.assertEqual(self.compact.report(START, END), expected)
        self.assertEqual(self.compact.state("checkpoint_ns"), CHECKPOINT)
        # Another committed insert can reuse a rolled-back row ID.
        self.ingest(
            [
                record(
                    sessionid="next",
                    srcname="different committed",
                    mastersrcmac="02:00:00:00:00:05",
                )
            ],
            CHECKPOINT + 2,
        )
        self.ingest([failed], CHECKPOINT + 3)
        self.compare()
        self.assertEqual(self.compact.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_process_exit_mid_ingestion_recovers_the_previous_committed_snapshot(self):
        self.ingest([record()], CHECKPOINT)
        expected = self.compact.report(START, END)
        next_record = record(
            sessionid="crash", srcname="interrupted fixture", mastersrcmac="02:00:00:00:00:09"
        )
        script = """
import json,os,sys
sys.path.insert(0,sys.argv[1])
import accounting
store=accounting.Store(sys.argv[2],json.loads(sys.argv[3]),json.loads(sys.argv[4]))
original=store._rollup
def crash_after_rollup(fact,sign):
    original(fact,sign)
    if sign>0:os._exit(73)
store._rollup=crash_after_rollup
store.ingest([json.loads(sys.argv[5])],checkpoint=int(sys.argv[6]))
raise SystemExit('Synthetic interruption was not reached')
"""
        for path in (self.legacy_path, self.compact_path):
            with self.subTest(database=path.name):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        script,
                        str(Path(accounting.__file__).parent),
                        str(path),
                        json.dumps(CONFIG),
                        json.dumps(INVENTORY),
                        json.dumps(next_record),
                        str(CHECKPOINT + 1),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 73, result.stderr)
        self.assertEqual(self.compact.report(START, END), expected)
        self.assertEqual(self.compact.state("checkpoint_ns"), CHECKPOINT)
        self.compare()
        self.compact.close()
        self.compact = accounting.Store(self.compact_path, CONFIG, deepcopy(INVENTORY))
        self.assertEqual(self.compact.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.ingest([next_record], CHECKPOINT + 1)
        self.compare()

    def test_seeded_mixed_batches_match_before_and_after_reopen(self):
        randomizer = random.Random(314159)
        first = accounting.timestamp_ns("2026-10-20T12:00:00Z")
        values = []
        for index in range(120):
            device = index % 12
            item = record(
                sessionid=str(device),
                srcname="fixture-" + str(device),
                srcip="192.0.2." + str(device + 1),
                mastersrcmac="02:00:00:00:00:" + format(device + 1, "02x"),
                duration=str(60 + index // 12 * 60),
            )
            item.pop("timestamp")
            item["timestamp_ns"] = first + (index // 12 * 60 + device) * accounting.NS + index
            item["fields"]["eventtime"] = str(item["timestamp_ns"])
            if index % 3 == 0:
                item["fields"]["subtype"] = "local"
            if index % 7 == 0:
                item["fields"]["mastersrcmac"] = ""
                item["fields"]["duration"] = ""
            if index % 11 == 0:
                item["fields"]["sentdelta"] = "invalid"
            if index % 13 == 0:
                item["fields"].pop("rcvddelta")
            values.append(item)
        randomizer.shuffle(values)
        for start in range(0, len(values), 15):
            self.ingest(values[start : start + 15], first + start * accounting.NS)
            self.compare()
        self.ingest(values[:20], first + 900 * accounting.NS)
        self.compact.close()
        self.compact = accounting.Store(self.compact_path, CONFIG, deepcopy(INVENTORY))
        self.compare()
        self.assertEqual(self.compact.db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


class CompactSnapshotTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="fortilens-compact-snapshot-")
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "compact report #fixture.sqlite"
        self.store = accounting.Store(
            self.path, CONFIG, deepcopy(INVENTORY), storage_format="compact"
        )
        self.addCleanup(self.store.close)
        self.store.ingest([record()], checkpoint=CHECKPOINT)
        with self.store.db:
            self.store.set_state("inventory", deepcopy(INVENTORY))
        self.engine = usage.Engine(self.store)
        self.addCleanup(self.engine.close)

    def test_read_only_reader_does_not_change_schema_contract_or_metadata(self):
        schema = self.store.db.execute("PRAGMA schema_version").fetchone()[0]
        contract = self.store.state("contract")
        reader = self.store.reader()
        self.assertIsNotNone(reader)
        self.addCleanup(reader.close)
        self.assertEqual(reader.ledger.format, "compact")
        self.assertEqual(reader.db.execute("PRAGMA query_only").fetchone()[0], 1)
        self.assertEqual(reader.report(START, END), self.store.report(START, END))
        with self.assertRaises(sqlite3.OperationalError):
            reader.set_state("reader_must_not_write", True)
        with self.assertRaises(sqlite3.OperationalError):
            reader.db.execute("CREATE TABLE reader_must_not_write(value INTEGER)")
        self.assertEqual(self.store.db.execute("PRAGMA schema_version").fetchone()[0], schema)
        self.assertEqual(self.store.state("contract"), contract)

    def test_reader_returns_committed_snapshot_while_ingestion_transaction_is_held(self):
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        errors, result = [], {}

        def writer():
            try:
                with self.engine.lock:
                    self.store.db.execute("BEGIN IMMEDIATE")
                    self.store.set_state("checkpoint_ns", CHECKPOINT + accounting.NS)
                    entered.set()
                    release.wait(10)
                    self.store.db.rollback()
            except Exception as exc:
                errors.append(exc)
                entered.set()

        def reader():
            try:
                result["report"] = self.engine.report("today", at=AT)
                result["diagnostics"] = self.engine.diagnostics()
            except Exception as exc:
                errors.append(exc)
            finally:
                completed.set()

        worker = threading.Thread(target=writer, daemon=True)
        request = threading.Thread(target=reader, daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(3))
            request.start()
            self.assertTrue(completed.wait(3), "Compact report waited for the ingestion lock")
            self.assertFalse(errors, errors)
            self.assertEqual(result["report"]["totals"]["upload_bytes"], 100)
            self.assertEqual(result["diagnostics"]["checkpoint_ns"], CHECKPOINT)
        finally:
            release.set()
            worker.join(5)
            if request.ident is not None:
                request.join(5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(request.is_alive())

    def test_report_cache_invalidates_after_compact_write_and_caller_mutation_isolated(self):
        initial = self.engine.report("today", at=AT)
        initial["totals"]["upload_bytes"] = -1
        self.assertEqual(self.engine.report("today", at=AT)["totals"]["upload_bytes"], 100)
        self.store.ingest([record(sessionid="second", sentdelta="500")], checkpoint=CHECKPOINT)
        latest = self.engine.report("today", at=AT)
        self.assertEqual(latest["totals"]["upload_bytes"], 600)
        self.assertEqual(latest["processing"]["checkpoint_ns"], CHECKPOINT)

    def test_one_read_snapshot_does_not_mix_concurrent_compact_commits(self):
        failures = []

        def writer():
            try:
                with self.engine.lock:
                    self.store.ingest(
                        [record(sessionid="next", sentdelta="500")], checkpoint=CHECKPOINT + 1
                    )
            except Exception as exc:
                failures.append(exc)

        with self.engine._read_snapshot() as (reader, _version):
            before = reader.report(START, END)
            thread = threading.Thread(target=writer, daemon=True)
            thread.start()
            thread.join(3)
            self.assertFalse(thread.is_alive(), "Compact WAL snapshot blocked the writer")
            self.assertFalse(failures, failures)
            self.assertEqual(reader.report(START, END), before)
            self.assertEqual(reader.state("checkpoint_ns"), CHECKPOINT)
        after = self.engine.report("today", at=AT)
        self.assertEqual(after["totals"]["upload_bytes"], 600)
        self.assertEqual(after["processing"]["checkpoint_ns"], CHECKPOINT + 1)


if __name__ == "__main__":
    unittest.main()
