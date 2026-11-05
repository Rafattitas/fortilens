"""Explicit migrations use disposable synthetic databases and never activate them."""

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
from accounting import Store
from accounting_migrate import MigrationError, migrate
from accounting_storage import CompactLedger
from test_accounting import CONFIG, INVENTORY, record


class AccountingMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source, self.output = self.root / "old.sqlite", self.root / "new.sqlite"
        store = Store(self.source, CONFIG, INVENTORY)
        rows = [
            record(
                srcname="fixture-" + str(i),
                sessionid=str(i),
                sentdelta=str(2**64 - 1) if i == 1 else "100",
            )
            for i in range(12)
        ]
        store.ingest(rows, checkpoint=100)
        store.inventory = {"interfaces": []}
        store.ingest(
            [record("2026-10-20T12:01:00Z", sessionid="0", duration="120")], checkpoint=200
        )
        store.ingest([record(subtype="local", sessionid="other")], checkpoint=300)
        store.ingest([{"timestamp_ns": 10, "fields": {"bad": 4}}], checkpoint=400)
        store.close()
        self.before = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def run_migration(self, **kwargs):
        return migrate(self.source, self.output, min_free_bytes=0, batch_size=3, **kwargs)

    def test_complete_copy_exact_reports_reopen_and_source_preservation(self):
        result = self.run_migration()
        self.assertEqual(result["checkpoint_ns"], 400)
        self.assertTrue(result["foreign_keys_verified"])
        self.assertEqual(result["verified"]["events"]["rows"], 14)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)
        self.assertFalse(Path(str(self.output) + "-wal").exists())
        with_reader = []
        for path in (self.source, self.output):
            store = Store.open_readonly(path, CONFIG, INVENTORY)
            self.addCleanup(store.close)
            with_reader.append(store)
        self.assertEqual(with_reader[1].storage_format, "compact")
        for start, end in (
            ("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z"),
            ("2026-10-20T12:00:01Z", "2026-10-20T12:02:00Z"),
        ):
            self.assertEqual(with_reader[0].report(start, end), with_reader[1].report(start, end))

    def test_source_continues_writing_after_completed_copy(self):
        self.run_migration()
        old = Store(self.source, CONFIG, INVENTORY)
        new = Store(self.output, CONFIG, INVENTORY)
        self.addCleanup(old.close)
        self.addCleanup(new.close)
        replay = record()
        late = record("2026-10-20T12:00:30Z", duration="90", sentdelta="13", rcvddelta="17")
        self.assertEqual(
            old.ingest([replay, late], checkpoint=500), new.ingest([replay, late], checkpoint=500)
        )
        self.assertEqual(
            old.report("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z"),
            new.report("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z"),
        )

    def test_existing_destination_is_not_overwritten(self):
        self.output.write_bytes(b"keep-existing")
        with self.assertRaises(MigrationError):
            self.run_migration()
        self.assertEqual(self.output.read_bytes(), b"keep-existing")

    def test_same_file_is_rejected(self):
        with self.assertRaises(MigrationError):
            migrate(self.source, self.source, min_free_bytes=0)
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)

    def test_cannot_disable_verification(self):
        with self.assertRaises(MigrationError):
            self.run_migration(verify=False)

    def test_compact_source_cannot_be_migrated_as_legacy(self):
        self.run_migration()
        with self.assertRaises(MigrationError):
            migrate(self.output, self.root / "third.sqlite", min_free_bytes=0)

    def test_low_space_fails_without_a_published_or_partial_database(self):
        with patch(
            "accounting_migrate.shutil.disk_usage", return_value=type("Usage", (), {"free": 0})()
        ):
            with self.assertRaises(MigrationError):
                self.run_migration()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".*building-*")), [])

    def test_interrupted_progress_cleans_only_its_partial_file(self):
        def stop(_event):
            raise RuntimeError("synthetic interruption")

        with self.assertRaises(RuntimeError):
            self.run_migration(progress=stop)
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".*building-*")), [])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)

    def test_bad_source_evidence_never_publishes(self):
        with closing(sqlite3.connect(self.source)) as db, db:
            db.execute(
                "UPDATE facts SET payload=? WHERE rowid=(SELECT MIN(rowid) FROM facts)",
                (json.dumps({"uid": "bad"}),),
            )
        with self.assertRaises(MigrationError):
            self.run_migration()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".*building-*")), [])

    def test_progress_reports_counts_without_customer_fields(self):
        events = []
        self.run_migration(progress=events.append)
        self.assertGreater(len(events), 10)
        self.assertEqual(events[-1]["stage"], "publish")
        self.assertTrue(all(set(event) == {"stage", "rows", "elapsed_seconds"} for event in events))

    def test_encoder_corruption_prevents_publication_even_when_row_counts_match(self):
        original = CompactLedger._encode_event

        def corrupt(ledger, event):
            modified = {**event, "fields": {**event["fields"], "srcname": "synthetic-corrupted"}}
            return original(ledger, modified)

        with patch.object(CompactLedger, "_encode_event", corrupt):
            with self.assertRaisesRegex(MigrationError, "Logical database verification"):
                self.run_migration()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.root.glob(".*building-*")), [])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)


if __name__ == "__main__":
    unittest.main()
