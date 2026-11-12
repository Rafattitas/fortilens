"""Characterization of the accounting store: the same inputs give the same results.

Runs one synthetic record stream through ``accounting.Store`` in several ways
(once, twice, in reverse batches, across a restart, with late records after
pruning) and in both storage formats, then records every report the services
read and a digest of every SQLite table. ``tests/fixtures/accounting_flow.json``
pins today's results, so the store can be reorganized with the accounting
logic, the SQLite schema and the report shapes unchanged. Each entry keeps the
totals readably beside the digests. Regenerate it only for an intended change:

    FORTILENS_UPDATE_GOLDEN=1 python -m unittest tests.test_accounting_flow

All devices, sessions and sites are synthetic.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
sys.path.insert(0, str(ROOT / "tests"))
import accounting  # noqa: E402
from test_accounting import CONFIG, INVENTORY, record  # noqa: E402
from test_applications import labelled  # noqa: E402
from test_websites import utm  # noqa: E402

GOLDEN = ROOT / "tests" / "fixtures" / "accounting_flow.json"
UPDATE = os.environ.get("FORTILENS_UPDATE_GOLDEN") == "1"
DAY1 = ("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z")
DAY2 = ("2026-10-21T00:00:00Z", "2026-10-22T00:00:00Z")
BOTH = ("2026-10-20T00:00:00Z", "2026-10-22T00:00:00Z")
LATER = "2026-10-25T12:00:00Z"
CHECKPOINT = accounting.timestamp_ns("2026-10-21T03:00:00Z")


def without(item, *keys):
    for key in keys:
        item["fields"].pop(key, None)
    return item


def stream():
    """Three devices on two VLANs: outbound, inbound, local, labelled, cumulative,
    across midnight, on an unknown interface and in another VDOM."""
    second = {"srcip": "192.0.2.11", "mastersrcmac": "02:00:00:00:00:02", "srcname": "laptop"}
    guest = {
        "srcip": "192.0.2.20",
        "mastersrcmac": "02:00:00:00:00:03",
        "srcname": "guest",
        "srcintf": "guests",
    }
    return [
        utm("web.whatsapp.com"),
        record(),
        record("2026-10-20T12:01:00Z", sentdelta="50", rcvddelta="70", duration="120"),
        labelled(
            "2026-10-20T12:30:00Z", sessionid="20", sentdelta="10", rcvddelta="5000", **second
        ),
        labelled(
            "2026-10-20T12:31:00Z",
            sessionid="21",
            app="ChatGPT",
            appcat="GenAI",
            apprisk="medium",
            sentdelta="300",
            rcvddelta="30",
            **second,
        ),
        record("2026-10-20T13:00:00Z", sessionid="30", sentdelta="7", rcvddelta="9", **guest),
        record(
            "2026-10-20T14:00:00Z",
            sessionid="40",
            srcip="203.0.113.5",
            dstip="192.0.2.10",
            srcintf="uplink-a",
            dstintf="staff",
            srcintfrole="wan",
            dstintfrole="lan",
            sentdelta="3",
            rcvddelta="4",
        ),
        record(
            "2026-10-20T15:00:00Z",
            sessionid="50",
            dstip="192.0.2.20",
            dstintf="guests",
            dstintfrole="lan",
        ),
        without(
            record("2026-10-20T16:00:00Z", sessionid="60", sentbyte="900", rcvdbyte="1900"),
            "sentdelta",
            "rcvddelta",
        ),
        without(
            record(
                "2026-10-20T16:05:00Z",
                sessionid="60",
                sentbyte="1000",
                rcvdbyte="2100",
                duration="360",
            ),
            "sentdelta",
            "rcvddelta",
        ),
        record("2026-10-20T23:59:30Z", sessionid="70", sentdelta="11", rcvddelta="12"),
        record(
            "2026-10-21T00:00:30Z", sessionid="70", sentdelta="13", rcvddelta="14", duration="120"
        ),
        record("2026-10-21T01:00:00Z", sessionid="80", srcintf="mystery", srcintfrole="undefined"),
        record("2026-10-21T02:00:00Z", sessionid="90", vd="other"),
    ]


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def table_digests(store):
    names = [
        row[0]
        for row in store.db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]
    schema = [
        tuple(row)
        for row in store.db.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' "
            "ORDER BY type,name"
        )
    ]
    result = {
        "schema": digest(schema),
        "user_version": store.db.execute("PRAGMA user_version").fetchone()[0],
    }
    for name in names:
        rows = sorted(
            (repr(tuple(row)) for row in store.db.execute('SELECT * FROM "' + name + '"')),
        )
        result[name] = [len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest()]
    return result


def observe(store, websites):
    """Every read the services make, as totals (readable) and digests (exact)."""
    reads = {
        "report_day1": store.report(*DAY1),
        "report_day2": store.report(*DAY2),
        "report_both": store.report(*BOTH),
        "report_search": store.report(*DAY1, search="laptop"),
        "applications": store.applications(*DAY1),
        "applications_genai": store.applications(*DAY1, category="GenAI", history=True),
        "classifications": store.classifications(
            accounting.timestamp_ns(BOTH[0]), accounting.timestamp_ns(BOTH[1])
        ),
        "diagnostics": store.diagnostics(),
    }
    vlans = [v.get("key") for v in reads["report_day1"].get("vlans", []) if v.get("key")]
    if vlans:
        reads["report_vlan"] = store.report(*DAY1, vlan=vlans[0])
    for device in sorted(d["device_id"] for d in reads["report_both"]["devices"]):
        reads["history_" + device[:12]] = store.history(device)
        reads["history_month_" + device[:12]] = store.history(device, months=True)
    if websites:
        reads["websites"] = store.websites(*DAY1, now=LATER)
    totals = {
        name: value.get("totals")
        for name, value in reads.items()
        if isinstance(value, dict) and "totals" in value
    }
    return {
        "totals": totals,
        "diagnostics": reads["diagnostics"],
        "reads": {name: digest(value) for name, value in reads.items()},
        "tables": table_digests(store),
    }


def open_store(path, storage, websites=True):
    config = {**CONFIG, "accounting_websites": websites}
    store = accounting.Store(path, config, deepcopy(INVENTORY), storage_format=storage)
    with store.db:
        if websites and store.state("websites_since_ns") != 0:
            store.set_state("websites_since_ns", 0)
        store.set_state("inventory", deepcopy(INVENTORY))
    return store


def run_scenario(name, storage, folder):
    path = Path(folder) / "flow.sqlite"
    records = stream()
    websites = name != "websites-off"
    store = open_store(path, storage, websites)
    try:
        if name in ("once", "websites-off"):
            store.ingest(records, checkpoint=CHECKPOINT)
        elif name == "twice":
            store.ingest(records, checkpoint=CHECKPOINT)
            store.ingest(stream(), checkpoint=CHECKPOINT)
        elif name == "reverse-batches":
            for item in reversed(records):
                store.ingest([item])
        elif name == "restart":
            store.ingest(records[:7])
            store.close()
            store = open_store(path, storage, websites)
            store.ingest(records[7:], checkpoint=CHECKPOINT)
        elif name == "pruned-then-late":
            store.ingest(records, checkpoint=CHECKPOINT)
            store.prune(LATER, ledger_days=3, summary_days=400)
            store.ingest(
                [
                    record("2026-10-20T12:02:00Z", sessionid="10", sentdelta="5", rcvddelta="5"),
                    record("2026-10-25T11:00:00Z", sessionid="99", sentdelta="1", rcvddelta="2"),
                ]
            )
        else:
            raise AssertionError(name)
        return observe(store, websites)
    finally:
        store.close()


SCENARIOS = ("once", "twice", "reverse-batches", "restart", "pruned-then-late", "websites-off")
FORMATS = ("compact", "legacy")


class AccountingFlowCharacterization(unittest.TestCase):
    maxDiff = None

    def actual(self):
        results = {}
        for storage in FORMATS:
            for name in SCENARIOS:
                with tempfile.TemporaryDirectory(prefix="fortilens-accounting-flow-") as folder:
                    results[storage + "/" + name] = run_scenario(name, storage, folder)
        return results

    def test_accounting_results_match_the_recording(self):
        actual = self.actual()
        if UPDATE:
            GOLDEN.parent.mkdir(exist_ok=True)
            GOLDEN.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n", encoding="utf-8")
            self.skipTest("Recorded " + str(GOLDEN.relative_to(ROOT)))
        expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
        self.assertEqual(sorted(expected), sorted(actual))
        for key in actual:
            with self.subTest(case=key):
                self.assertEqual(expected[key], json.loads(json.dumps(actual[key])))

    def test_both_storage_formats_give_the_same_reports(self):
        expected = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
        for name in SCENARIOS:
            if "compact/" + name in expected:
                with self.subTest(scenario=name):
                    compact, legacy = expected["compact/" + name], expected["legacy/" + name]
                    self.assertEqual(compact["totals"], legacy["totals"])
                    same = {k for k in compact["reads"] if k != "diagnostics"}
                    self.assertEqual(
                        {k: compact["reads"][k] for k in same},
                        {k: legacy["reads"][k] for k in same},
                    )


if __name__ == "__main__":
    unittest.main()
