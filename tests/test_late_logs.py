"""Lines Loki stores after the poll passed them are found and counted once.

Synthetic Loki rows only; no service or installation is contacted.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting as a
import usage_service as u
from test_accounting import CONFIG, INVENTORY, record

NS = a.NS
START = a.timestamp_ns("2026-10-20T12:00:00Z")
MINUTE = 60 * NS


def row(stamp, session, sent="100"):
    item = record(sessionid=session, sentdelta=sent, rcvddelta="0")
    return {
        "timestamp_ns": stamp,
        "line": " ".join('%s="%s"' % kv for kv in item["fields"].items()),
    }


class LateLoki:
    """Holds rows; counts and reads [start, end) like Loki."""

    def __init__(self, rows):
        self.rows, self.reads, self.max_records = list(rows), [], 30000

    def count(self, start, end):
        return sum(start <= r["timestamp_ns"] < end for r in self.rows)

    def read(self, start, end):
        self.reads.append(((start - START) // MINUTE, (end - START) // MINUTE))
        rows = [r for r in self.rows if start <= r["timestamp_ns"] < end]
        return rows, {"requests": 1, "records": len(rows)}


class LateLogTests(unittest.TestCase):
    def setUp(self):
        self.store = a.Store(":memory:", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(self.store.close)
        rows = [row(START + m * MINUTE, "s%d" % m) for m in range(45)]
        self.store.ingest(rows, checkpoint=START + 45 * MINUTE)
        with self.store.db:
            self.store.set_state("checkpoint_ns", START + 45 * MINUTE)
        self.engine = u.Engine(self.store)
        self.engine.loki = LateLoki(rows)

    def upload(self):
        report = self.store.report("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z")
        return report["totals"]["upload_bytes"]

    def test_nothing_is_read_again_while_counts_agree(self):
        self.assertEqual(self.engine.reconcile_late(), 0)
        self.assertEqual(self.engine.loki.reads, [])

    def test_a_line_stored_late_is_read_once_and_counted_once(self):
        before = self.upload()
        # Loki restarted: a line from minute 20 arrives after the poll passed it.
        self.engine.loki.rows.append(row(START + 20 * MINUTE + 5 * NS, "late", sent="7"))
        self.assertEqual(self.engine.reconcile_late(), 1)
        self.assertEqual(self.engine.loki.reads, [(20, 25)])
        self.assertEqual(self.upload(), before + 7)
        self.assertEqual(self.store.state("late_records_found"), 1)
        # The window now agrees; the next sweep reads nothing.
        self.assertEqual(self.engine.reconcile_late(), 0)
        self.assertEqual(self.upload(), before + 7)

    def test_only_the_last_half_hour_the_poll_passed_is_compared(self):
        self.engine.loki.rows.append(row(START + 2 * MINUTE, "too-old"))
        self.engine.loki.rows.append(row(START + 44 * MINUTE + 50 * NS, "not-passed"))
        self.assertEqual(self.engine.reconcile_late(), 0)

    def test_a_duplicate_line_never_recorded_costs_one_read_only(self):
        self.engine.loki.rows.append(dict(self.engine.loki.rows[30]))
        before = self.upload()
        self.assertEqual(self.engine.reconcile_late(), 1)
        self.assertEqual(self.engine.reconcile_late(), 0)
        self.assertEqual(self.upload(), before)

    def test_loki_count_query_covers_exactly_the_window(self):
        calls = []

        def fetch(base, path, params):
            calls.append((path, params))
            return {"data": {"resultType": "vector", "result": [{"value": [0, "12"]}]}}

        loki = u.LokiReader("http://loki", fetch=fetch)
        self.assertEqual(loki.count(START, START + 5 * MINUTE), 12)
        path, params = calls[0]
        self.assertEqual(path, "/loki/api/v1/query")
        self.assertEqual(params["query"], 'sum(count_over_time({job="fortigate-syslog"}[300s]))')
        self.assertEqual(params["time"], str(START + 5 * MINUTE - 1))
        empty = u.LokiReader(
            "http://loki", fetch=lambda *_: {"data": {"resultType": "vector", "result": []}}
        )
        self.assertEqual(empty.count(START, START + MINUTE), 0)
        broken = u.LokiReader("http://loki", fetch=lambda *_: {"data": {"resultType": "matrix"}})
        with self.assertRaises(u.ServiceError):
            broken.count(START, START + MINUTE)


if __name__ == "__main__":
    unittest.main()
