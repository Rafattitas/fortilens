"""Feed identical records to two accounting implementations and compare every table.

For reviewing performance changes that must not change results. Both engines get
the same batches in the same order, each in its own new database; then every
summary table, the decoded ledger facts and the durable state are compared row
by row. Never point --output at an installation.

Input is either synthetic (`--synthetic DEVICES:SESSIONS:LOCALS:MINUTES`) or a file of
JSON lines `{"timestamp_ns": ..., "line": ...}` exported from Loki (`--records`).
"""

import argparse
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))
from test_accounting import CONFIG, INVENTORY  # noqa: E402

VOLATILE_STATE = {"contract", "delivery_max_ns", "storage_format"}


def load(portable, name):
    sys.path.insert(0, str(portable))
    try:
        for module in ("interfaces", "accounting_storage"):
            sys.modules.pop(module, None)
        spec = importlib.util.spec_from_file_location(name, Path(portable) / "accounting.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(portable))


def snapshot(store):
    """Every table that carries results, as sorted canonical rows."""
    tables = {}
    for table in ("daily", "app_daily", "quality", "device_profile", "app_catalog"):
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
            tables[table] = sorted(json.dumps([*row], sort_keys=True) for row in store.db.execute("SELECT * FROM " + table))
    tables["state"] = sorted(json.dumps([k, v], sort_keys=True) for k, v in store.db.execute("SELECT key,value FROM state")
                             if k not in VOLATILE_STATE)
    facts = []
    for fact, labels in store.ledger.range_facts_labels(0, 2**63 - 1):
        facts.append(json.dumps([fact, labels], sort_keys=True))
    tables["ledger_facts"] = sorted(facts)
    return tables


def feed(module, folder, batches, inventory):
    store = module.Store(Path(folder) / "usage.sqlite", CONFIG, inventory, storage_format="compact")
    spent, count = 0.0, 0
    try:
        for batch in batches():
            started = time.perf_counter()
            store.ingest(batch, checkpoint=batch[-1]["timestamp_ns"])
            spent += time.perf_counter() - started
            count += len(batch)
        return snapshot(store), count, spent
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", required=True, help="portable/ directory of the reference implementation")
    parser.add_argument("--new", required=True, help="portable/ directory of the changed implementation")
    parser.add_argument("--output", required=True, help="Empty directory for the two databases")
    parser.add_argument("--synthetic", help="DEVICES:SESSIONS:LOCALS:MINUTES")
    parser.add_argument("--records", help="JSON lines exported from Loki")
    parser.add_argument("--batch", type=int, default=30000)
    parser.add_argument("--inventory", help="Interface inventory JSON for real records")
    parser.add_argument("--config", help="Accounting config JSON for real records (timezone, WAN, source)")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("--output must be empty")
    inventory = json.loads(Path(args.inventory).read_text()) if args.inventory else INVENTORY
    if args.config:

        CONFIG.update(json.loads(Path(args.config).read_text()))
    if args.synthetic:
        sys.path.insert(0, str(HERE.parent))
        import benchmark_scale
        devices, sessions, locals_per_device, minutes = (int(x) for x in args.synthetic.split(":"))

        def batches():
            pending = []
            for tick in range(minutes):
                pending += benchmark_scale.minute(devices, sessions, 5, tick, locals_per_device)
                while len(pending) >= args.batch:
                    yield pending[:args.batch]
                    pending = pending[args.batch:]
            if pending:
                yield pending
    else:
        def batches():
            pending = []
            with open(args.records, encoding="utf-8") as stream:
                for line in stream:
                    pending.append(json.loads(line))
                    if len(pending) == args.batch:
                        yield pending
                        pending = []
            if pending:
                yield pending
    results = {}
    for name in ("old", "new"):
        module = load(Path(getattr(args, name)).resolve(), "accounting_" + name)
        (output / name).mkdir()
        results[name] = feed(module, output / name, batches, inventory)
    old, new = results["old"][0], results["new"][0]
    report = {"records": results["new"][1], "old_seconds": round(results["old"][2], 2), "new_seconds": round(results["new"][2], 2),
              "speedup": round(results["old"][2] / results["new"][2], 2) if results["new"][2] else None, "tables": {}}
    identical = True
    for table in sorted(set(old) | set(new)):
        a, b = old.get(table, []), new.get(table, [])
        same = a == b
        identical &= same
        report["tables"][table] = {"rows": len(b), "identical": same,
                                   "sha256": hashlib.sha256("\n".join(b).encode()).hexdigest()[:16]}
        if not same:
            report["tables"][table]["first_difference"] = next(
                ([x, y] for x, y in zip(a, b) if x != y), ["rows", len(a), len(b)])
    report["identical"] = identical
    print(json.dumps(report, indent=2))
    sys.exit(0 if identical else 1)


if __name__ == "__main__":
    main()
