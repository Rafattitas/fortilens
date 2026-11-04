"""Synthetic WAL snapshots and precise report reuse preserve accounting results."""

import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting as accounting
import usage_service as usage
from test_accounting import CONFIG, INVENTORY, record

AT = "2026-10-20T13:00:00Z"
CHECKPOINT = accounting.timestamp_ns("2026-10-20T12:01:00Z")


class ReportConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="fortilens-report-")
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "accounting snapshot #fixture.sqlite"
        self.store = accounting.Store(self.path, CONFIG, deepcopy(INVENTORY))
        self.addCleanup(self.store.close)
        self.store.ingest([record()], checkpoint=CHECKPOINT)
        with self.store.db:
            self.store.set_state("inventory", deepcopy(INVENTORY))
        initial = self.store.report("2026-10-20T00:00:00Z", AT)
        self.device = initial["devices"][0]["device_id"]
        self.engine = usage.Engine(self.store)
        self.addCleanup(lambda: self.engine.close())

    def report(self, **changes):
        return self.engine.report("today", at=AT, **changes)

    def add_event(self, **changes):
        self.store.ingest(
            [record("2026-10-20T12:02:00Z", sessionid="11", **changes)],
            checkpoint=CHECKPOINT + 120 * accounting.NS,
        )

    def test_reports_diagnostics_inventory_and_history_finish_during_writer_transaction(self):
        entered, release, completed = threading.Event(), threading.Event(), threading.Event()
        output, failures = {}, []

        def writer():
            try:
                with self.engine.lock:
                    self.store.db.execute("BEGIN IMMEDIATE")
                    self.store.set_state("checkpoint_ns", CHECKPOINT + accounting.NS)
                    self.store.set_state("inventory", {"interfaces": []})
                    self.store.inventory = {"interfaces": []}
                    entered.set()
                    release.wait(20)
                    self.store.db.rollback()
                    self.store.inventory = deepcopy(INVENTORY)
            except Exception as exc:
                failures.append(exc)
                entered.set()

        def reader():
            try:
                output["report"] = self.report(device=self.device, include_history=True)
                output["diagnostics"] = self.engine.diagnostics()
                output["inventory"] = self.engine.inventory()
            except Exception as exc:
                failures.append(exc)
            finally:
                completed.set()

        writer_thread = threading.Thread(target=writer, daemon=True)
        reader_thread = threading.Thread(target=reader, daemon=True)
        writer_thread.start()
        try:
            self.assertTrue(entered.wait(5), "Synthetic writer never acquired its transaction")
            reader_thread.start()
            self.assertTrue(completed.wait(5), "Read-only requests waited for the writer lock")
            self.assertFalse(failures, failures)
            result = output["report"]
            self.assertEqual(result["totals"]["upload_bytes"], 100)
            self.assertEqual(result["daily_history"][0]["upload_bytes"], 100)
            self.assertEqual(result["monthly_history"][0]["download_bytes"], 200)
            self.assertEqual(result["processing"]["checkpoint_ns"], CHECKPOINT)
            self.assertEqual(output["diagnostics"]["checkpoint_ns"], CHECKPOINT)
            self.assertEqual(output["inventory"], INVENTORY)
        finally:
            release.set()
            writer_thread.join(5)
            if reader_thread.ident is not None:
                reader_thread.join(5)
        self.assertFalse(writer_thread.is_alive())
        self.assertFalse(reader_thread.is_alive())

    def test_single_snapshot_keeps_report_and_checkpoint_coherent_across_concurrent_commit(self):
        committed, failures = threading.Event(), []

        def writer():
            try:
                with self.engine.lock:
                    self.add_event()
            except Exception as exc:
                failures.append(exc)
            finally:
                committed.set()

        with self.engine._read_snapshot() as (reader, _version):
            first = reader.report("2026-10-20T00:00:00Z", AT)
            thread = threading.Thread(target=writer, daemon=True)
            thread.start()
            self.assertTrue(committed.wait(5), "Read snapshot blocked a WAL writer")
            thread.join(5)
            self.assertFalse(failures, failures)
            second = reader.report("2026-10-20T00:00:00Z", AT)
            self.assertEqual(first, second)
            self.assertEqual(reader.state("checkpoint_ns"), CHECKPOINT)
        latest = self.report()
        self.assertEqual(latest["totals"]["upload_bytes"], 200)
        self.assertEqual(latest["processing"]["checkpoint_ns"], CHECKPOINT + 120 * accounting.NS)

    def test_reader_is_read_only_and_does_not_change_schema_or_contract(self):
        schema = self.store.db.execute("PRAGMA schema_version").fetchone()[0]
        contract = self.store.state("contract")
        reader = self.store.reader()
        self.assertIsNotNone(reader)
        try:
            self.assertEqual(reader.state("contract"), contract)
            self.assertEqual(reader.db.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                reader.set_state("reader_wrote", True)
            with self.assertRaises(sqlite3.OperationalError):
                reader.db.execute("CREATE TABLE reader_wrote(value INTEGER)")
        finally:
            reader.close()
        self.assertEqual(self.store.db.execute("PRAGMA schema_version").fetchone()[0], schema)
        self.assertEqual(self.store.state("contract"), contract)
        self.assertIsNone(self.store.state("reader_wrote"))

    def test_repeated_exact_request_reuses_aggregation_without_returning_mutable_cache(self):
        with self.engine._read_snapshot() as (reader, _version):
            pass
        with patch.object(reader, "report", wraps=reader.report) as aggregate:
            first = self.report()
            first["totals"]["upload_bytes"] = -1
            first["devices"][0]["hostname"] = "mutated caller output"
            second = self.report()
            self.assertEqual(second["totals"]["upload_bytes"], 100)
            self.assertEqual(second["devices"][0]["hostname"], "fixture")
            self.assertEqual(aggregate.call_count, 1)

    def test_direct_commit_invalidates_cache_even_when_checkpoint_does_not_change(self):
        self.assertEqual(self.report()["totals"]["upload_bytes"], 100)
        self.store.ingest([record(sessionid="11", sentdelta="500")], checkpoint=CHECKPOINT)
        result = self.report()
        self.assertEqual(result["totals"]["upload_bytes"], 600)
        self.assertEqual(result["processing"]["checkpoint_ns"], CHECKPOINT)

    def test_uncommitted_update_and_rollback_never_change_report_or_checkpoint(self):
        expected = self.report()
        self.store.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.store.db.execute(
                "SELECT day,device,vlan,payload FROM daily LIMIT 1"
            ).fetchone()
            payload = json.loads(row[3])
            payload["upload_bytes"] = 999999
            self.store.db.execute(
                "UPDATE daily SET payload=? WHERE day=? AND device=? AND vlan=?",
                (json.dumps(payload), row[0], row[1], row[2]),
            )
            self.store.set_state("checkpoint_ns", CHECKPOINT + accounting.NS)
            result = self.report()
            self.assertEqual(result["totals"], expected["totals"])
            self.assertEqual(result["processing"]["checkpoint_ns"], CHECKPOINT)
        finally:
            self.store.db.rollback()
        self.assertEqual(self.report()["totals"], expected["totals"])

    def test_pruning_invalidates_cached_partial_period_instead_of_serving_expired_detail(self):
        self.assertEqual(self.report()["totals"]["upload_bytes"], 100)
        self.store.prune("2026-11-01T00:00:00Z", ledger_days=8, summary_days=400)
        with self.assertRaisesRegex(ValueError, "Partial-day detail expired"):
            self.report()

    def test_inventory_commit_updates_evidence_without_rewriting_historical_vlan(self):
        before = self.report()
        self.assertTrue(before["processing"]["inventory_available"])
        with self.store.db:
            self.store.set_state("inventory", {"interfaces": []})
        self.store.inventory = {"interfaces": []}
        after = self.report()
        self.assertEqual(after["totals"], before["totals"])
        self.assertEqual(after["vlans"], before["vlans"])
        self.assertFalse(after["processing"]["inventory_available"])
        self.assertEqual(self.engine.inventory(), {"interfaces": []})

    def test_precise_period_and_filter_keys_do_not_share_incompatible_results(self):
        self.assertEqual(self.report()["totals"]["upload_bytes"], 100)
        self.assertIsNone(
            self.engine.report("today", at="2026-10-20T12:00:00Z")["totals"]["upload_bytes"]
        )
        self.assertIsNone(self.report(search="no-such-endpoint")["totals"]["upload_bytes"])
        self.assertIsNone(self.report(vlan="no-such-segment")["totals"]["upload_bytes"])
        self.assertIsNone(self.report(device="f" * 64)["totals"]["upload_bytes"])

    def test_rolling_week_boundaries_move_even_without_a_new_commit(self):
        self.store.ingest([record("2026-10-13T12:00:30Z", sessionid="old", sentdelta="500")])
        earlier = self.engine.report("week", at="2026-10-20T12:00:00Z")
        later = self.engine.report("week", at="2026-10-20T12:01:00Z")
        self.assertEqual(earlier["totals"]["upload_bytes"], 500)
        self.assertEqual(later["totals"]["upload_bytes"], 100)
        self.assertNotEqual(earlier["start_utc"], later["start_utc"])

    def test_failed_range_releases_snapshot_and_observes_a_later_commit(self):
        with self.engine._read_snapshot() as (reader, _version):
            pass
        with patch.object(reader, "report", side_effect=ValueError("Synthetic report failure")):
            with self.assertRaises(ValueError):
                self.report()
        self.assertFalse(reader.db.in_transaction)
        self.add_event()
        self.assertEqual(self.report()["totals"]["upload_bytes"], 200)

    def test_cache_limits_entry_count_and_bypasses_oversized_unicode_results(self):
        for number in range(7):
            self.report(search=f"missing-device-{number}")
        self.assertLessEqual(len(self.engine._report_cache), 4)
        self.engine.close()
        with self.engine._read_snapshot() as (reader, _version):
            base = reader.report("2026-10-20T00:00:00Z", AT)
        # Its character count is below the limit but its UTF-8 byte size exceeds
        # it; a cache cannot budget only by len(str).
        base["note"] = "é" * (1024 * 1024 + 1)
        with patch.object(
            reader, "report", side_effect=lambda *a, **kw: deepcopy(base)
        ) as aggregate:
            first = self.report()
            second = self.report()
            self.assertEqual(first["totals"], second["totals"])
            self.assertEqual(aggregate.call_count, 2)
            self.assertFalse(self.engine._report_cache)

    def test_cache_hit_refreshes_runtime_status_and_lag_without_reaggregation(self):
        with self.engine._read_snapshot() as (reader, _version):
            pass
        with patch.object(reader, "report", wraps=reader.report) as aggregate:
            self.engine.last_poll_ok = True
            self.engine.last_error = None
            self.engine.last_attempt = 123
            with patch.object(usage.time, "time", return_value=CHECKPOINT / accounting.NS + 100):
                first = self.report()
            self.engine.last_poll_ok = False
            self.engine.last_error = "accounting_storage_limit"
            self.engine.last_attempt = 456
            with patch.object(usage.time, "time", return_value=CHECKPOINT / accounting.NS + 200):
                second = self.report()
            self.assertEqual(aggregate.call_count, 1)
        self.assertEqual(first["totals"], second["totals"])
        self.assertTrue(first["processing"]["last_poll_ok"])
        self.assertFalse(second["processing"]["last_poll_ok"])
        self.assertEqual(second["processing"]["last_error"], "accounting_storage_limit")
        self.assertEqual(second["processing"]["last_attempt"], 456)
        self.assertEqual(first["processing"]["processing_lag_seconds"], 100)
        self.assertEqual(second["processing"]["processing_lag_seconds"], 200)

    def test_database_diagnostics_scan_is_reused_only_for_unchanged_committed_data(self):
        with self.engine._read_snapshot() as (reader, _version):
            pass
        with patch.object(reader, "diagnostics", wraps=reader.diagnostics) as diagnostics:
            self.report()
            self.engine.diagnostics()
            self.report(search="not-present")
            self.assertEqual(diagnostics.call_count, 1)
            with self.store.db:
                self.store.set_state("parser_rejections", 12)
            self.assertEqual(self.engine.diagnostics()["parser_rejections"], 12)
            self.assertEqual(diagnostics.call_count, 2)

    def test_changed_file_identity_closes_old_reader_and_discards_cached_output(self):
        self.report()
        old = self.engine._reader
        tracked_path = self.store.path
        info = tracked_path.stat()
        original_stat = Path.stat

        def changed_identity(path, *args, **kwargs):
            if path == tracked_path:
                return SimpleNamespace(st_dev=info.st_dev, st_ino=info.st_ino + 1)
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", changed_identity):
            result = self.report()
        self.assertEqual(result["totals"]["upload_bytes"], 100)
        self.assertIsNot(old, self.engine._reader)
        with self.assertRaises(sqlite3.ProgrammingError):
            old.db.execute("SELECT 1")

    def test_changed_contract_is_rejected_before_cached_results_are_used(self):
        self.report()
        with self.store.db:
            contract = self.store.state("contract")
            self.store.set_state("contract", {**contract, "vdom": "different-source"})
        with self.assertRaises(ValueError):
            self.report()
        self.assertFalse(self.engine._reader.db.in_transaction)

    def test_delivery_observation_matches_rows_for_late_replay_and_malformed_input(self):
        for batch, checkpoint in (
            ([record("2026-10-20T12:10:00Z", sessionid="newer")], CHECKPOINT),
            ([record("2026-10-20T11:00:00Z", sessionid="older")], CHECKPOINT),
            ([record("2026-10-20T12:10:00Z", sessionid="newer")], CHECKPOINT),
            (
                [{"timestamp": "2026-10-20T12:11:00Z", "line": 'type="traffic" broken="'}],
                CHECKPOINT + 800 * accounting.NS,
            ),
        ):
            self.store.ingest(batch, checkpoint=checkpoint)
            actual = self.store.db.execute("SELECT MAX(stamp) FROM deliveries").fetchone()[0]
            self.assertEqual(self.store.delivery_max(), actual)
            self.assertEqual(self.engine.diagnostics()["last_delivery_ns"], actual)

    def test_failed_ingestion_rolls_back_derived_delivery_observation(self):
        before = self.store.delivery_max()
        with patch.object(
            accounting, "derive_session", side_effect=ValueError("Synthetic failure")
        ):
            with self.assertRaises(ValueError):
                self.add_event()
        self.assertEqual(self.store.delivery_max(), before)
        self.assertEqual(
            self.store.db.execute("SELECT MAX(stamp) FROM deliveries").fetchone()[0], before
        )

    def test_pruning_clears_observation_when_all_deliveries_expire(self):
        self.store.prune("2026-11-20T12:00:00Z")
        self.assertIsNone(self.store.delivery_max())
        self.assertIsNone(self.engine.diagnostics()["last_delivery_ns"])

    def test_legacy_read_only_snapshot_falls_back_and_writer_open_rebuilds(self):
        actual = self.store.db.execute("SELECT MAX(stamp) FROM deliveries").fetchone()[0]
        with self.store.db:
            self.store.db.execute("DELETE FROM state WHERE key='delivery_max_ns'")
        self.assertEqual(self.engine.diagnostics()["last_delivery_ns"], actual)
        self.engine.close()
        self.store.close()
        reopened = accounting.Store(self.path, CONFIG, deepcopy(INVENTORY))
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.state("delivery_max_ns"), actual)

    def test_new_snapshot_diagnostics_does_not_scan_delivery_rows(self):
        with self.engine._read_snapshot() as (reader, _version):
            queries = []
            reader.db.set_trace_callback(queries.append)
            try:
                self.assertIsNotNone(reader.diagnostics()["last_delivery_ns"])
            finally:
                reader.db.set_trace_callback(None)
        self.assertFalse(any("MAX(stamp) FROM deliveries" in query for query in queries))

    def test_initial_inventory_fallback_does_not_publish_uncommitted_metadata(self):
        with self.store.db:
            self.store.db.execute("DELETE FROM state WHERE key='inventory'")
        self.store.db.execute("BEGIN IMMEDIATE")
        try:
            self.store.inventory = {"interfaces": []}
            self.store.set_state("inventory", self.store.inventory)
            self.assertEqual(self.engine.inventory(), INVENTORY)
        finally:
            self.store.db.rollback()
            self.store.inventory = deepcopy(INVENTORY)

    def test_queued_reader_does_not_prevent_writer_commit_and_sees_new_revision(self):
        with self.engine._read_snapshot() as (reader, _version):
            pass
        original = reader.report
        entered, release, committed = threading.Event(), threading.Event(), threading.Event()
        output, failures = {}, []
        calls = 0

        def slow_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                release.wait(20)
            return original(*args, **kwargs)

        def read(label):
            try:
                output[label] = self.report()
            except Exception as exc:
                failures.append(exc)

        def write():
            try:
                with self.engine.lock:
                    self.add_event()
            except Exception as exc:
                failures.append(exc)
            finally:
                committed.set()

        first = threading.Thread(target=read, args=("first",), daemon=True)
        second = threading.Thread(target=read, args=("second",), daemon=True)
        writer = threading.Thread(target=write, daemon=True)
        with patch.object(reader, "report", side_effect=slow_first):
            first.start()
            try:
                self.assertTrue(entered.wait(5))
                second.start()
                writer.start()
                self.assertTrue(committed.wait(5), "Queued readers prevented WAL writer commit")
            finally:
                release.set()
                for thread in (first, second, writer):
                    if thread.ident is not None:
                        thread.join(5)
        self.assertFalse(failures, failures)
        self.assertEqual(output["first"]["totals"]["upload_bytes"], 100)
        self.assertEqual(output["second"]["totals"]["upload_bytes"], 200)


class MemoryReportCompatibilityTests(unittest.TestCase):
    def test_memory_fixture_keeps_existing_reports_and_history(self):
        store = accounting.Store(":memory:", CONFIG, deepcopy(INVENTORY))
        self.addCleanup(store.close)
        store.ingest([record()], checkpoint=CHECKPOINT)
        self.assertIsNone(store.reader())
        engine = usage.Engine(store)
        self.addCleanup(engine.close)
        report = engine.report("today", at=AT)
        detailed = engine.report(
            "today", at=AT, device=report["devices"][0]["device_id"], include_history=True
        )
        self.assertEqual(report["totals"], detailed["totals"])
        self.assertEqual(detailed["daily_history"][0]["upload_bytes"], 100)


if __name__ == "__main__":
    unittest.main()
