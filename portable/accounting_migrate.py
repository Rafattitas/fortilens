"""Explicit, verified legacy-to-compact copy. Never replaces the source database."""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from accounting_storage import open_ledger


class MigrationError(ValueError):
    """A bounded migration diagnostic without customer data or raw exceptions."""


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
    if source.resolve() == destination.resolve() or destination.exists():
        raise MigrationError("The destination must be a new, separate database file")
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
        dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dst.close()
        dst = None
        src.rollback()
        src.close()
        src = None
        with temporary.open("r+b") as stream:
            os.fsync(stream.fileno())
        notify("publish")
        # Hard-link publication is atomic and refuses an existing destination.
        # The file remains on the same filesystem and contains no pending WAL.
        os.link(temporary, destination)
        published = True
        temporary.unlink()
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--min-free-gib", type=float, default=2)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args(argv)
    try:
        reserve = int(args.min_free_gib * 1024**3)
        result = migrate(
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
