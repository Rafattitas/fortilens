"""Summary rows are written once per batch; a failed batch must leave no trace.

Synthetic records only. Equality with the previous per-fact implementation on
real and synthetic data is checked by tests/support/compare_engines.py.
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting as a
from test_accounting import CONFIG, INVENTORY
from test_applications import DAY, labelled

TABLES = ("daily", "app_daily", "quality", "device_profile", "app_catalog", "state")


def dump(store):
    return {t: sorted(map(tuple, store.db.execute("SELECT * FROM " + t))) for t in TABLES}


class RollupBatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = a.Store(Path(self.folder.name) / "usage.sqlite", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(self.store.close)

    def test_failed_batch_changes_nothing_and_the_next_batch_is_complete(self):
        self.store.ingest([labelled(sessionid="a1", appid="41540", app="ChatGPT", appcat="GenAI", osname="Android")])
        before = dump(self.store)
        calls = {"n": 0}
        original = self.store.ledger.insert_fact

        def fail_later(fact, session):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("synthetic failure")
            return original(fact, session)

        batch = [labelled(sessionid="b1", appid="52618", app="Claude", appcat="GenAI", mastersrcmac="02:00:00:00:00:07",
                          osname="Windows"),
                 labelled("2026-10-20T12:05:00Z", sessionid="b2", appid="41540", app="ChatGPT", appcat="GenAI")]
        with mock.patch.object(self.store.ledger, "insert_fact", fail_later), self.assertRaises(RuntimeError):
            self.store.ingest(batch)
        self.assertEqual(dump(self.store), before)
        self.assertIsNone(self.store._batch)
        # The rolled-back application name is written by the next successful batch.
        self.store.ingest(batch)
        self.assertIn("52618", self.store.app_catalog(["52618"]))
        report, apps = self.store.report(*DAY), self.store.applications(*DAY)
        self.assertEqual(apps["totals"], report["totals"])
        self.assertEqual(len(report["devices"]), 2)

    def test_one_batch_and_many_small_batches_store_the_same_rows(self):
        rows = [labelled("2026-10-20T12:%02d:00Z" % m, sessionid="s%d" % (m % 3), appid=str(41540 + m % 2),
                         app=("ChatGPT", "Claude")[m % 2], appcat="GenAI", mastersrcmac="02:00:00:00:00:0%d" % (m % 3),
                         logid="0000000013" if m in (7, 8) else "0000000020", sentdelta=str(100 + m), rcvddelta=str(900 + m))
                for m in range(10)]
        self.store.ingest(rows)
        whole = dump(self.store)
        other = a.Store(Path(self.folder.name) / "other.sqlite", CONFIG, INVENTORY, storage_format="compact")
        self.addCleanup(other.close)
        for row in rows:
            other.ingest([row])
        split = dump(other)
        for table in ("daily", "app_daily", "quality", "device_profile", "app_catalog"):
            self.assertEqual(split[table], whole[table], table)


if __name__ == "__main__":
    unittest.main()
