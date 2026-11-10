"""Measure accounting capacity for many endpoints with disposable synthetic data.

Never point this at an installation. It creates a new database in a temporary
directory (or --directory), feeds FortiGate-like forward-traffic records for
--devices endpoints minute by minute, and reports sustained ingestion rate,
database growth and report latency. Timing is informational, not a CI threshold.

Each simulated minute every device has --sessions open sessions that each send
one interim record with Application Control labels; sessions close after
--lifetime minutes with a close record, and every device also sends one local
(unsupported) record, as real sites do.
"""

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "portable"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accounting
from test_accounting import CONFIG, INVENTORY, record

BASE = accounting.timestamp_ns("2026-10-20T08:00:00Z")
APPS = [
    ("Microsoft.Teams", "Collaboration", "elevated", "43541"),
    ("YouTube", "Video/Audio", "elevated", "31077"),
    ("QUIC", "Network.Service", "low", "40169"),
    ("SSL_TLSv1.3", "Network.Service", "medium", "47013"),
    ("OpenAI.ChatGPT", "GenAI", "medium", "52618"),
    ("Microsoft.Outlook", "Email", "medium", "15816"),
    ("WhatsApp_Web", "Collaboration", "elevated", "39999"),
    ("Windows.Update", "Update", "low", "16009"),
]
SEGMENTS = ("staff", "guests")


def minute(devices, sessions, lifetime, tick, locals_per_device=1):
    """All records of one simulated minute, in delivery order."""
    rows = []
    stamp = BASE + tick * 60 * accounting.NS
    for device in range(devices):
        mac = "02:%02x:%02x:00:00:%02x" % (device >> 16 & 255, device >> 8 & 255, device & 255)
        ip = "10.%d.%d.%d" % (device >> 16 & 255, device >> 8 & 255, device & 255 or 1)
        for slot in range(sessions):
            generation, age = divmod(tick + slot + device, lifetime)
            app, appcat, apprisk, appid = APPS[(device + slot) % len(APPS)]
            fields = dict(
                sessionid=str(((device * sessions + slot) << 20) + generation),
                srcip=ip,
                mastersrcmac=mac,
                srcname="host-%d" % device,
                srcintf=SEGMENTS[device % 2],
                dstport=str(443 + slot),
                duration=str((age + 1) * 60),
                sentdelta=str(2000 + device % 97 * 31),
                rcvddelta=str(9000 + device % 89 * 113),
                app=app,
                appcat=appcat,
                apprisk=apprisk,
                appid=appid,
                osname="Windows",
                srchwvendor="Dell",
                devtype="Computer",
            )
            if age == lifetime - 1:
                fields["logid"] = "0000000013"
            item = record(**fields)
            item.pop("timestamp")
            item["timestamp_ns"] = stamp + (device * sessions + slot) % 59 * accounting.NS
            rows.append(item)
        for extra in range(locals_per_device):
            local = record(
                subtype="local",
                logid="0000000014",
                srcip=ip,
                sessionid=str(900000000 + (tick * 64 + extra) * 100000 + device),
            )
            local.pop("timestamp")
            local["timestamp_ns"] = stamp + (device + extra) % 59 * accounting.NS
            rows.append(local)
    return rows


def size(path):
    return sum(p.stat().st_size for p in path.parent.glob(path.name + "*"))


def run(directory, devices, sessions, lifetime, minutes, batch, locals_per_device=1):
    path = Path(directory) / "usage.sqlite"
    store = accounting.Store(path, CONFIG, INVENTORY, storage_format="compact")
    records, busy, slowest = 0, 0.0, 0.0
    try:
        for tick in range(minutes):
            rows = minute(devices, sessions, lifetime, tick, locals_per_device)
            for offset in range(0, len(rows), batch):
                part = rows[offset : offset + batch]
                started = time.perf_counter()
                store.ingest(part, checkpoint=part[-1]["timestamp_ns"])
                spent = time.perf_counter() - started
                busy += spent
                slowest = max(slowest, spent)
                records += len(part)
        end = accounting.iso(BASE + minutes * 60 * accounting.NS)
        start = accounting.iso(BASE)
        timings = {}
        for name, call in (
            ("report", lambda: store.report(start, end)),
            ("applications", lambda: store.applications(start, end)),
            ("report_day", lambda: store.report("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z")),
        ):
            started = time.perf_counter()
            result = call()
            timings[name] = round(time.perf_counter() - started, 3)
            if name == "report":
                devices_seen = len(result["devices"])
        store.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db_bytes = size(path)
    finally:
        store.close()
    return {
        "devices": devices,
        "sessions_per_device": sessions,
        "minutes": minutes,
        "records": records,
        "records_per_minute": records // minutes,
        "ingest_seconds": round(busy, 2),
        "records_per_second": round(records / busy) if busy else None,
        "slowest_batch_seconds": round(slowest, 2),
        "batch_records": batch,
        "headroom": round(records / busy / (records / minutes / 60), 1) if busy else None,
        "database_bytes": db_bytes,
        "bytes_per_record": round(db_bytes / records, 1),
        "devices_in_report": devices_seen,
        "query_seconds": timings,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--devices", type=int, default=2000)
    parser.add_argument("--sessions", type=int, default=3, help="Open Internet sessions per device")
    parser.add_argument("--lifetime", type=int, default=5, help="Minutes a session stays open")
    parser.add_argument("--minutes", type=int, default=10)
    parser.add_argument(
        "--locals",
        type=int,
        default=1,
        help="Local or UTM (unsupported) records per device per minute",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=30000,
        help="Records per ingest call (the service reads at most 30,000 per poll)",
    )
    parser.add_argument(
        "--directory", help="Empty directory to keep the database; a temporary one is removed"
    )
    args = parser.parse_args()
    if args.directory:
        target = Path(args.directory)
        target.mkdir(parents=True, exist_ok=True)
        if any(target.iterdir()):
            parser.error("--directory must be empty")
        print(
            json.dumps(
                run(
                    target,
                    args.devices,
                    args.sessions,
                    args.lifetime,
                    args.minutes,
                    args.batch,
                    args.locals,
                ),
                indent=2,
            )
        )
    else:
        folder = tempfile.mkdtemp(prefix="fortilens-scale-")
        try:
            print(
                json.dumps(
                    run(
                        folder,
                        args.devices,
                        args.sessions,
                        args.lifetime,
                        args.minutes,
                        args.batch,
                        args.locals,
                    ),
                    indent=2,
                )
            )
        finally:
            shutil.rmtree(folder)


if __name__ == "__main__":
    main()
