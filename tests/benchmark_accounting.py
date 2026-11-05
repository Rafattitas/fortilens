"""Compare ledger formats using disposable synthetic data, never an installation.

Timing is informational, not a flaky CI threshold. Optionally select a trusted
older accounting.py to measure a historical implementation on identical inputs.
"""

import argparse
import hashlib
import importlib.util
import json
import shutil
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting
from test_accounting import CONFIG, INVENTORY, record

BASE = accounting.timestamp_ns("2026-10-20T12:00:00Z")


def batches(rows):
    for offset in range(0, rows, 500):
        batch = []
        for index in range(offset, min(offset + 500, rows)):
            device = index % 100
            item = record(
                sessionid=str(device + (index // 1000) * 100),
                srcip="192.0.2." + str(device + 1),
                srcname="synthetic-" + str(device),
                mastersrcmac="02:00:00:00:00:" + format(device + 1, "02x"),
                duration=str(60 + (index // 100 % 10) * 60),
                sentdelta=str(100 + index % 23),
                rcvddelta=str(200 + index % 17),
            )
            item.pop("timestamp")
            item["timestamp_ns"] = BASE + (index // 100 * 60 + device) * accounting.NS
            if index % 2:
                item["fields"].update(subtype="local", logid="0000000014")
            if index % 4 == 1:
                for name in ("mastersrcmac", "sessionid", "duration"):
                    item["fields"].pop(name, None)
            batch.append(item)
        yield batch


def trial(directory, kind, rows, reference):
    path = directory / "usage.sqlite"
    store = (
        reference.Store(path, CONFIG, INVENTORY)
        if kind == "legacy" and reference is not None
        else accounting.Store(path, CONFIG, INVENTORY, storage_format=kind)
    )
    try:
        latencies, processed = [], 0
        cpu, wall = time.process_time(), time.monotonic()
        for batch in batches(rows):
            started = time.monotonic()
            processed += len(batch)
            store.ingest(batch, checkpoint=processed)
            latencies.append(time.monotonic() - started)
        elapsed, cpu_elapsed = time.monotonic() - wall, time.process_time() - cpu
        queries = []
        for _ in range(3):
            started = time.monotonic()
            report = store.report(BASE + accounting.NS, BASE + 4000 * accounting.NS)
            queries.append(time.monotonic() - started)
        store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        physical = path.stat().st_size
        started = time.monotonic()
        store.prune("2026-10-29T12:00:00Z")
        prune = time.monotonic() - started
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store.db.execute("PRAGMA foreign_key_check").fetchone() is None
        return {
            "format": kind,
            "records": processed,
            "ingest_seconds": elapsed,
            "cpu_seconds": cpu_elapsed,
            "max_batch_seconds": max(latencies),
            "report_median_seconds": statistics.median(queries),
            "prune_seconds": prune,
            "physical_bytes_before_prune": physical,
            "report_sha256": hashlib.sha256(
                json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=10000)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--legacy-source", type=Path, help="Trusted reference accounting.py; code runs"
    )
    args = parser.parse_args()
    if not 1 <= args.rows <= 250000 or not 1 <= args.repeats <= 4:
        parser.error("Use 1..250000 rows and 1..4 repeats")
    reference, reference_hash = None, None
    if args.legacy_source:
        source = args.legacy_source.resolve()
        reference_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        spec = importlib.util.spec_from_file_location("benchmark_reference_accounting", source)
        reference = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = reference
        spec.loader.exec_module(reference)
    results = []
    with tempfile.TemporaryDirectory(prefix="fortilens-synthetic-ledger-benchmark-") as folder:
        root = Path(folder)
        for repetition in range(args.repeats):
            order = ("legacy", "compact") if repetition % 2 == 0 else ("compact", "legacy")
            for kind in order:
                directory = root / str(len(results))
                directory.mkdir()
                result = trial(directory, kind, args.rows, reference)
                if results:
                    assert result["report_sha256"] == results[0]["report_sha256"], "Report mismatch"
                results.append(result)
                print(json.dumps(result), flush=True)
                assert directory.resolve().parent == root.resolve() and not directory.is_symlink()
                shutil.rmtree(directory)
    print(
        json.dumps(
            {"synthetic_only": True, "reference_sha256": reference_hash, "reports_equal": True}
        )
    )


if __name__ == "__main__":
    main()
