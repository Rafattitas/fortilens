"""Explicit, verified accounting database copies. Never replaces the source database.

``migrate`` copies legacy JSON storage to compact storage. ``slim`` copies a
compact database to compact version 2: unsupported records become counted
markers, remaining details are compressed and the copy is vacuumed.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from accounting_storage import (
    _class_text,
    _decode_fields,
    _hash,
    _pack,
    _unpack,
    open_ledger,
)


class MigrationError(ValueError):
    """A bounded migration diagnostic without customer data or raw exceptions."""


def _require_unused_destination(destination):
    # A stale WAL can be replayed over a different main database. A missing main
    # file alone therefore does not establish a safe publication destination.
    for path in (
        destination,
        *(Path(str(destination) + suffix) for suffix in ("-wal", "-shm", "-journal")),
    ):
        try:
            path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            raise MigrationError("The destination safety could not be established") from None
        raise MigrationError("The destination and its sidecars must be new, separate files")


def logical_rows(db, ledger, table):
    """Stream a deterministic logical representation, including legacy row order."""
    if table in ("daily", "quality", "state"):
        order = {"daily": "day,device,vlan", "quality": "day,classification", "state": "key"}[table]
        yield from (tuple(r) for r in db.execute("SELECT * FROM " + table + " ORDER BY " + order))
    elif ledger.format == "legacy":
        for r in db.execute("SELECT * FROM " + table + " ORDER BY rowid"):
            yield (*r[:3], json.loads(r[3])) if table != "deliveries" else tuple(r)
    elif table == "events":
        for r in db.execute(ledger.EVENT_SELECT + " ORDER BY e.id"):
            event = ledger._decode_event(r)
            yield event["uid"], event["session"], event["event_ns"], event
    elif table == "facts":
        for r in db.execute(ledger.FACT_SELECT + " ORDER BY f.id"):
            fact = ledger._decode_fact(r)
            yield fact["uid"], r["event_session"].hex(), fact["event_ns"], fact
    elif table == "deliveries":
        for r in db.execute(
            "SELECT uid,event_hash,invalid,stamp FROM compact_deliveries ORDER BY rowid"
        ):
            yield r[0].hex(), "invalid" if r[2] else r[1].hex(), r[3]
    else:
        raise MigrationError("Unknown migration table")


def fingerprint_rows(rows):
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return {"rows": count, "sha256": digest.hexdigest()}


def migrate(
    source, destination, *, batch_size=1000, min_free_bytes=2 * 1024**3, progress=None, verify=True
):
    """Copy one consistent legacy snapshot to an exclusively published new file.

    Callers must close the old writer before activating this copy. A live source
    can be read consistently, but transactions committed after the snapshot are
    deliberately absent from the result. This function never activates a copy.
    """
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if type(batch_size) is not int or not 1 <= batch_size <= 10000:
        raise MigrationError("Choose a batch size between 1 and 10000")
    if type(min_free_bytes) is not int or min_free_bytes < 0:
        raise MigrationError("Choose a nonnegative free-space reserve")
    if (
        source.is_symlink()
        or destination.is_symlink()
        or any(p.is_symlink() for p in (*source.parents, *destination.parents))
    ):
        raise MigrationError("Migration paths must not traverse symbolic links")
    if not source.is_file() or not destination.parent.is_dir():
        raise MigrationError("Use an existing source file and destination directory")
    if source.resolve() == destination.resolve():
        raise MigrationError("The destination must be a new, separate database file")
    _require_unused_destination(destination)
    if not verify:
        raise MigrationError("Migration verification cannot be disabled")
    if shutil.disk_usage(destination.parent).free < min_free_bytes + 1024**2:
        raise MigrationError("Insufficient destination space for the requested reserve")
    started = time.monotonic()
    src = dst = None
    temporary = None
    published = False
    tables = ("events", "facts", "deliveries", "daily", "quality", "state")
    counts = {}

    def notify(stage, rows=0):
        if shutil.disk_usage(destination.parent).free < min_free_bytes:
            raise MigrationError("The migration reached its free-space reserve")
        if progress:
            progress(
                {
                    "stage": stage,
                    "rows": rows,
                    "elapsed_seconds": round(time.monotonic() - started, 2),
                }
            )

    try:
        src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        src.row_factory = sqlite3.Row
        src.execute("PRAGMA query_only=ON")
        src.execute("PRAGMA cache_size=-2048")
        src.execute("BEGIN")
        legacy = open_ledger(src, initialize=False)
        if legacy.format != "legacy":
            raise MigrationError("The source must use legacy accounting storage")
        # Pin the snapshot and ensure it is an actual initialized accounting DB.
        contract = src.execute("SELECT value FROM state WHERE key='contract'").fetchone()
        if contract is None or not isinstance(json.loads(contract[0]), dict):
            raise MigrationError("The source accounting contract is unavailable")
        checkpoint = src.execute("SELECT value FROM state WHERE key='checkpoint_ns'").fetchone()
        fd, name = tempfile.mkstemp(
            prefix="." + destination.name + ".building-", dir=destination.parent
        )
        os.close(fd)
        temporary = Path(name)
        dst = sqlite3.connect(temporary)
        dst.row_factory = sqlite3.Row
        dst.executescript(
            "PRAGMA page_size=4096;PRAGMA journal_mode=WAL;PRAGMA synchronous=FULL;PRAGMA foreign_keys=ON;PRAGMA cache_size=-2048;"
        )
        compact = open_ledger(dst, storage_format="compact")
        with compact.write_unit():
            for table in tables:
                count = 0
                if table in ("events", "facts"):
                    rows = src.execute("SELECT rowid,* FROM " + table + " ORDER BY rowid")
                else:
                    rows = src.execute("SELECT * FROM " + table + " ORDER BY rowid")
                for row in rows:
                    if table == "events":
                        event = json.loads(row[4])
                        if (event["uid"], event["session"], event["event_ns"]) != tuple(row[1:4]):
                            raise MigrationError(
                                "Source event columns disagree with their evidence"
                            )
                        compact.insert_event(event)
                    elif table == "facts":
                        fact = json.loads(row[4])
                        if (fact["uid"], fact["event_ns"]) != (row[1], row[3]):
                            raise MigrationError("Source fact columns disagree with their evidence")
                        compact.insert_fact(fact, row[2], ordinal=row[0])
                    elif table == "deliveries":
                        compact.insert_delivery(*row)
                    else:
                        dst.execute(
                            "INSERT INTO " + table + " VALUES(" + ",".join("?" for _ in row) + ")",
                            tuple(row),
                        )
                    count += 1
                    if count % batch_size == 0:
                        dst.commit()
                        notify("copy:" + table, count)
                dst.commit()
                counts[table] = count
                notify("copied:" + table, count)
        dst.commit()
        verified = {}
        for table in tables:
            notify("verify-source:" + table)
            before = fingerprint_rows(logical_rows(src, legacy, table))
            notify("verify-copy:" + table)
            after = fingerprint_rows(logical_rows(dst, compact, table))
            if before != after or before["rows"] != counts[table]:
                raise MigrationError("Logical database verification failed")
            verified[table] = before
        notify("integrity")
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("Destination database integrity verification failed")
        if dst.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MigrationError("Destination evidence references are inconsistent")
        if dst.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
            raise MigrationError("The verified copy could not be checkpointed")
        dst.close()
        dst = None
        wal = Path(str(temporary) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise MigrationError("The verified copy still has pending changes")
        src.rollback()
        src.close()
        src = None
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        notify("publish")
        # Recheck after copying and verification; another process or a progress
        # callback may have created destination files while the copy was built.
        _require_unused_destination(destination)
        # Hard-link publication is atomic and refuses an existing destination.
        # The file remains on the same filesystem and contains no pending WAL.
        os.link(temporary, destination)
        published = True
        temporary.unlink()
        if os.name == "posix":
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        result = {
            "source_format": "legacy",
            "destination_format": "compact",
            "verified": verified,
            "foreign_keys_verified": True,
            "checkpoint_ns": json.loads(checkpoint[0]) if checkpoint else None,
            "source_file_bytes": source.stat().st_size,
            "destination_file_bytes": destination.stat().st_size,
            "seconds": round(time.monotonic() - started, 3),
            "activation": "Not activated; close the writer and retain a matched database/runtime rollback pair before switching.",
        }
        return result
    except MigrationError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        raise MigrationError("The verified migration could not complete safely") from exc
    finally:
        if dst is not None:
            dst.close()
        if src is not None:
            src.close()
        if temporary is not None and not published:
            for path in (temporary, Path(str(temporary) + "-wal"), Path(str(temporary) + "-shm")):
                path.unlink(missing_ok=True)


def _snapshot_store(db, ledger):
    """A report reader bound to an already pinned snapshot connection."""
    import accounting

    row = db.execute("SELECT value FROM state WHERE key='contract'").fetchone()
    contract = json.loads(row[0]) if row else None
    if not isinstance(contract, dict) or not isinstance(contract.get("accounting_timezone"), str):
        raise MigrationError("The source accounting contract is unavailable")
    store = object.__new__(accounting.Store)
    store.config, store.inventory, store.path = dict(contract), {"interfaces": []}, None
    store.db, store.ledger, store.storage_format = db, ledger, ledger.format
    return store


def _report_digest(store):
    """Digest whole days, unaligned partial windows, the whole span and diagnostics.

    Partial windows read retained ledger facts and boundary classification
    counts, so they exercise the raw records and not only daily summaries.
    """
    from datetime import datetime, timedelta

    import accounting

    days = [r[0] for r in store.db.execute("SELECT DISTINCT day FROM quality ORDER BY day")]
    digest = hashlib.sha256()
    if not days:
        return digest.hexdigest(), 0
    zone = accounting.tz(store.config["accounting_timezone"])

    def bound(day):
        return accounting.timestamp_ns(
            datetime.combine(datetime.fromisoformat(day).date(), datetime.min.time(), zone).isoformat()
        )

    ranges = []
    for day in days:
        start = bound(day)
        end = bound((datetime.fromisoformat(day) + timedelta(days=1)).date().isoformat())
        ranges.append((start, end))
        edge = start + 1037 * accounting.NS
        while edge < end:
            ranges.append((edge, min(end, edge + 6 * 3600 * accounting.NS)))
            edge += 6 * 3600 * accounting.NS
    ranges.append((ranges[0][0], bound(days[-1]) + 86400 * accounting.NS))
    count = 0
    for start, end in ranges:
        try:
            value = store.report(start, end)
        except ValueError:
            # Expired partial-day detail is refused identically by both copies.
            value = "refused"
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
        digest.update(len(encoded).to_bytes(8, "big") + encoded)
        count += 1
    digest.update(json.dumps(store.diagnostics(), sort_keys=True, default=str).encode())
    return digest.hexdigest(), count


def slim(source, destination, *, batch_size=2000, min_free_bytes=2 * 1024**3, progress=None):
    """Build a verified, smaller compact version 2 copy of a compact database.

    Callers must stop the writer before activating the copy. Unsupported records
    keep their identity, time and classification; daily summaries, quality
    counts, state, deliveries and every report must be identical.
    """
    import accounting

    source, destination = Path(source).absolute(), Path(destination).absolute()
    if type(batch_size) is not int or not 1 <= batch_size <= 50000:
        raise MigrationError("Choose a batch size between 1 and 50000")
    if type(min_free_bytes) is not int or min_free_bytes < 0:
        raise MigrationError("Choose a nonnegative free-space reserve")
    if any(p.is_symlink() for p in (source, destination, *source.parents, *destination.parents)):
        raise MigrationError("Paths must not traverse symbolic links")
    if not source.is_file() or not destination.parent.is_dir():
        raise MigrationError("Use an existing source file and destination directory")
    if source.resolve() == destination.resolve():
        raise MigrationError("The destination must be a new, separate database file")
    _require_unused_destination(destination)
    started = time.monotonic()
    src = work = out = None
    temporaries = []

    def notify(stage, rows=0):
        if shutil.disk_usage(destination.parent).free < min_free_bytes:
            raise MigrationError("The copy reached its free-space reserve")
        if progress:
            progress({"stage": stage, "rows": rows,
                      "elapsed_seconds": round(time.monotonic() - started, 2)})

    def temporary(label):
        fd, name = tempfile.mkstemp(prefix="." + destination.name + "." + label + "-",
                                    dir=destination.parent)
        os.close(fd)
        path = Path(name)
        temporaries.append(path)
        return path

    try:
        notify("start")
        src = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        src.row_factory = sqlite3.Row
        src.execute("PRAGMA query_only=ON")
        src.execute("BEGIN")
        original = open_ledger(src, initialize=False)
        if original.format != "compact":
            raise MigrationError("The source must use compact accounting storage")
        src.execute("SELECT COUNT(*) FROM state").fetchone()  # pin the snapshot
        building = temporary("building")
        work = sqlite3.connect(building)
        src.backup(work)
        notify("copied")
        work.row_factory = sqlite3.Row
        work.executescript("PRAGMA journal_mode=MEMORY;PRAGMA synchronous=OFF;PRAGMA foreign_keys=ON;")
        ledger = open_ledger(work)
        unsupported_class = _class_text("UNSUPPORTED_RECORD")
        removed = 0
        last = 0
        while True:
            rows = work.execute(
                """SELECT e.id,e.uid,e.stamp,e.details,f.id AS fact_id,f.classification
                FROM compact_events e LEFT JOIN compact_facts f ON f.event_id=e.id
                WHERE e.id>? ORDER BY e.id LIMIT ?""",
                (last, batch_size),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                details = _unpack(row["details"])
                fields = _decode_fields(details[1])
                if (accounting.unsupported(fields) and row["classification"] == unsupported_class
                        and not details[2].get("anchor")):
                    work.execute("INSERT INTO compact_skipped VALUES(?,?,?)",
                                 (row["uid"], row["stamp"], unsupported_class))
                    work.execute("DELETE FROM compact_facts WHERE id=?", (row["fact_id"],))
                    work.execute("DELETE FROM compact_events WHERE id=?", (row["id"],))
                    removed += 1
                elif isinstance(row["details"], str):
                    work.execute("UPDATE compact_events SET details=? WHERE id=?",
                                 (_pack(details), row["id"]))
            last = rows[-1]["id"]
            work.commit()
            notify("events", last)
        last = 0
        while True:
            rows = work.execute(
                "SELECT id,details FROM compact_facts WHERE id>? ORDER BY id LIMIT ?",
                (last, batch_size),
            ).fetchall()
            if not rows:
                break
            for row in rows:
                if isinstance(row["details"], str):
                    work.execute("UPDATE compact_facts SET details=? WHERE id=?",
                                 (_pack(_unpack(row["details"])), row["id"]))
            last = rows[-1]["id"]
            work.commit()
            notify("facts", last)
        work.commit()
        del ledger
        final = temporary("final")  # VACUUM INTO accepts an existing empty file
        notify("vacuum")
        work.execute("VACUUM INTO ?", (str(final),))
        work.close()
        work = None
        out = sqlite3.connect(final)
        out.row_factory = sqlite3.Row
        compact = open_ledger(out, initialize=False)
        if out.execute("PRAGMA user_version").fetchone()[0] != 2:
            raise MigrationError("The copy did not reach compact version 2")
        notify("verify")
        skipped_uids = {
            r[0] for r in out.execute("SELECT uid FROM compact_skipped")
        }
        if len(skipped_uids) < removed:
            raise MigrationError("Logical database verification failed")
        verified = {}
        for table in ("deliveries", "daily", "quality", "state"):
            before = fingerprint_rows(logical_rows(src, original, table))
            after = fingerprint_rows(logical_rows(out, compact, table))
            if before != after:
                raise MigrationError("Logical database verification failed")
            verified[table] = before
        for table in ("events", "facts"):
            before = fingerprint_rows(
                r for r in logical_rows(src, original, table) if _hash(r[0]) not in skipped_uids
            )
            after = fingerprint_rows(logical_rows(out, compact, table))
            if before != after:
                raise MigrationError("Logical database verification failed")
            verified[table] = before
        notify("verify-reports")
        before = _report_digest(_snapshot_store(src, original))
        after = _report_digest(_snapshot_store(out, compact))
        if before != after:
            raise MigrationError("Report verification failed")
        verified["reports"] = {"rows": before[1], "sha256": before[0]}
        if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("Destination database integrity verification failed")
        if out.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MigrationError("Destination evidence references are inconsistent")
        out.execute("PRAGMA journal_mode=WAL")
        out.close()
        out = None
        src.rollback()
        src.close()
        src = None
        with final.open("r+b") as stream:
            os.fsync(stream.fileno())
        notify("publish")
        _require_unused_destination(destination)
        os.link(final, destination)
        if os.name == "posix":
            directory = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return {
            "source_format": "compact",
            "destination_format": "compact-v2",
            "unsupported_records_counted_not_stored": removed,
            "verified": verified,
            "source_file_bytes": source.stat().st_size,
            "destination_file_bytes": destination.stat().st_size,
            "seconds": round(time.monotonic() - started, 3),
            "activation": "Not activated; stop the writer and keep the original file before switching.",
        }
    except MigrationError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, IndexError) as exc:
        raise MigrationError("The verified copy could not complete safely") from exc
    finally:
        for connection in (work, out, src):
            if connection is not None:
                connection.close()
        for path in temporaries:
            for item in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"),
                         Path(str(path) + "-journal")):
                item.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--min-free-gib", type=float, default=2)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--slim", action="store_true",
                        help="Copy a compact database to a smaller verified compact version 2 file")
    args = parser.parse_args(argv)
    try:
        reserve = int(args.min_free_gib * 1024**3)
        operation = slim if args.slim else migrate
        result = operation(
            args.source,
            args.destination,
            batch_size=args.batch_size,
            min_free_bytes=reserve,
            progress=(lambda event: print(json.dumps(event), flush=True))
            if args.progress
            else None,
        )
    except (MigrationError, ValueError, OverflowError, OSError, sqlite3.Error):
        parser.exit(
            1,
            "Migration did not complete safely. Preserve the source and review free space, destination and format.\n",
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
