"""The report cache behaves the same for Reports, Websites and Applications.

Each kind of report is served from one shared snapshot cache: a repeated query
is not recomputed, a new write is never hidden, a caller's changes never reach
the next caller, an oversized result is not kept, and at most four results are
kept in all. All records are synthetic.
"""

import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting  # noqa: E402
import usage_service as usage  # noqa: E402
from test_accounting import CONFIG, INVENTORY, record  # noqa: E402
from test_applications import labelled  # noqa: E402
from test_websites import utm  # noqa: E402

AT = "2026-10-20T13:00:00Z"
CHECKPOINT = accounting.timestamp_ns("2026-10-20T12:01:00Z")

# kind -> (engine call, store method that computes it)
KINDS = {
    "report": (lambda engine, **kw: engine.report("today", at=AT, **kw), "report"),
    # Website totals are per day, so its period must end at midnight.
    "websites": (
        lambda engine, **kw: engine.websites(
            "custom", start="2026-10-20T00:00:00Z", end="2026-10-21T00:00:00Z", **kw
        ),
        "websites",
    ),
    "applications": (
        lambda engine, **kw: engine.applications("today", at=AT, **kw),
        "applications",
    ),
}


def stable(result):
    """A result without "processing", which reports the live lag on every request."""
    return {key: value for key, value in result.items() if key != "processing"}


def first_container(value):
    """A nested list or dict of a result, to change it as a careless caller might."""
    for item in value.values():
        if isinstance(item, (list, dict)) and item:
            return item
    raise AssertionError("result has no nested values")


class ReportCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="fortilens-cache-")
        self.addCleanup(directory.cleanup)
        config = {**CONFIG, "accounting_websites": True}
        self.store = accounting.Store(
            Path(directory.name) / "cache.sqlite", config, deepcopy(INVENTORY)
        )
        self.addCleanup(self.store.close)
        with self.store.db:
            self.store.set_state("websites_since_ns", 0)
            self.store.set_state("inventory", deepcopy(INVENTORY))
        self.store.ingest(
            [utm("web.whatsapp.com"), record(), labelled(sessionid="7", sentdelta="40")],
            checkpoint=CHECKPOINT,
        )
        self.engine = usage.Engine(self.store)
        self.addCleanup(self.engine.close)

    def computed(self, kind):
        """Count calls to the store method behind one kind of report."""
        name = KINDS[kind][1]
        return patch.object(
            accounting.Store, name, autospec=True, side_effect=getattr(accounting.Store, name)
        )

    def test_a_repeated_query_is_served_from_the_cache(self):
        for kind, (call, _name) in KINDS.items():
            with self.subTest(kind=kind), self.computed(kind) as compute:
                first = call(self.engine)
                second = call(self.engine)
                self.assertEqual(stable(first), stable(second))
                self.assertEqual(compute.call_count, 1)

    def test_a_new_write_is_not_hidden_by_the_cache(self):
        before = {kind: call(self.engine) for kind, (call, _name) in KINDS.items()}
        self.store.ingest(
            [
                record("2026-10-20T12:01:00Z", sentdelta="50", rcvddelta="70", duration="120"),
                labelled(sessionid="8", sentdelta="900"),
            ],
            checkpoint=CHECKPOINT + 60 * accounting.NS,
        )
        for kind, (call, _name) in KINDS.items():
            with self.subTest(kind=kind), self.computed(kind) as compute:
                after = call(self.engine)
                self.assertEqual(compute.call_count, 1, "a write must be a cache miss")
                self.assertNotEqual(stable(before[kind]), stable(after))

    def test_a_callers_changes_never_reach_the_next_caller(self):
        for kind, (call, _name) in KINDS.items():
            with self.subTest(kind=kind):
                first = call(self.engine)
                expected = deepcopy(stable(first))
                nested = first_container(first)
                if isinstance(nested, list):
                    nested.append("changed by a caller")
                else:
                    nested["changed"] = "by a caller"
                first["period"] = "changed by a caller"
                self.assertEqual(stable(call(self.engine)), expected)
                self.assertEqual(stable(call(self.engine)), expected)

    def test_each_filter_is_its_own_entry_and_four_entries_are_kept_in_all(self):
        call_report, _ = KINDS["report"]
        call_sites, _ = KINDS["websites"]
        call_apps, _ = KINDS["applications"]
        call_report(self.engine)
        call_sites(self.engine)
        call_sites(self.engine, site="whatsapp.com")
        call_apps(self.engine)
        self.assertEqual(len(self.engine._report_cache), 4)
        call_report(self.engine, search="missing-device")
        self.assertEqual(len(self.engine._report_cache), 4)
        # The oldest entry (the first report) was evicted; the newest stays.
        with self.computed("report") as compute:
            call_report(self.engine, search="missing-device")
            self.assertEqual(compute.call_count, 0)
            call_report(self.engine)
            self.assertEqual(compute.call_count, 1)

    def test_an_oversized_result_is_returned_but_not_kept(self):
        for kind, (call, name) in KINDS.items():
            with self.subTest(kind=kind):
                self.engine.close()
                original = getattr(accounting.Store, name)

                def oversized(store, *args, **kwargs):
                    result = original(store, *args, **kwargs)
                    # Below the limit in characters, above it in UTF-8 bytes.
                    result["note"] = "é" * (1024 * 1024 + 1)
                    return result

                with patch.object(
                    accounting.Store, name, autospec=True, side_effect=oversized
                ) as compute:
                    first = call(self.engine)
                    second = call(self.engine)
                self.assertEqual(stable(first), stable(second))
                self.assertEqual(compute.call_count, 2)
                self.assertFalse(self.engine._report_cache)


if __name__ == "__main__":
    unittest.main()
