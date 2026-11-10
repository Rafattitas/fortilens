"""A busy site must slow the read window down, never stall the checkpoint.

Synthetic Loki pages only; no service or installation is contacted.
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


class BusyLoki:
    """Holds `per_second` records per second and the real record budget."""

    def __init__(self, per_second, budget=300):
        self.per_second, self.max_records, self.reads = per_second, budget, []

    def first(self, start, end):
        return START

    def read(self, start, end):
        seconds = (end - start) / NS
        self.reads.append(round(seconds, 2))
        if seconds * self.per_second > self.max_records:
            raise u.ServiceError("loki_record_budget_exhausted")
        rows = []
        for index in range(int(seconds * self.per_second)):
            stamp = start + int(index * NS / self.per_second)
            item = record(sessionid="s%d" % (stamp // NS), sentdelta="1", rcvddelta="1")
            item.pop("timestamp")
            item["timestamp_ns"] = stamp
            rows.append(
                {
                    "timestamp_ns": stamp,
                    "line": " ".join('%s="%s"' % kv for kv in item["fields"].items()),
                }
            )
        return rows, {"requests": 1, "records": len(rows)}


class PollWindowTests(unittest.TestCase):
    def engine(self, per_second):
        store = a.Store(
            ":memory:",
            {**CONFIG, "accounting_step_seconds": 120, "accounting_overlap_seconds": 30},
            INVENTORY,
            storage_format="compact",
        )
        self.addCleanup(store.close)
        engine = u.Engine(store)
        engine.loki = BusyLoki(per_second)
        engine.fit_storage = lambda target: None
        with store.db:
            store.set_state("checkpoint_ns", START)
        return engine, store

    def test_window_shrinks_until_a_read_fits_then_advances(self):
        engine, store = self.engine(per_second=20)
        now = START / NS + 3600
        results = [engine.poll(now) for _ in range(6)]
        self.assertEqual(results[:4], [False, False, False, False])
        # Window plus overlap must hold at most 300 records at 20 per second.
        self.assertEqual(engine.loki.reads[:5], [150.0, 90.0, 60.0, 30.0, 15.0])
        self.assertGreater(store.state("checkpoint_ns"), START)
        self.assertEqual(engine.step_seconds, 7.5)

    def test_window_grows_back_when_traffic_is_light(self):
        engine, store = self.engine(per_second=1)
        engine.loki.max_records = 10000
        engine.step_seconds = 4
        engine.poll(START / NS + 3600)
        self.assertEqual(engine.step_seconds, 8)
        for _ in range(6):
            engine.poll(START / NS + 3600)
        self.assertEqual(engine.step_seconds, 120)

    def test_more_than_the_budget_in_one_second_is_still_reported(self):
        engine, _ = self.engine(per_second=1000)
        engine.step_seconds = 1
        with self.assertRaises(u.ServiceError):
            engine.poll(START / NS + 3600)


if __name__ == "__main__":
    unittest.main()
