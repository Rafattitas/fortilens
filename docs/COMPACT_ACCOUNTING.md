# Compact accounting storage

The optional compact SQLite ledger stores complete SHA256 event/session/delivery
identities as 32-byte values and avoids repeating field names and parent event
identities in every payload. Versioned JSON value vectors retain every original
field, including unknown extension fields and the distinction between absent,
empty and null values. Byte contributions remain exact JSON integers, including
values beyond SQLite's signed 64-bit range; SQLite never converts them to floats.
Historical metadata remains frozen in its owning record. It does not change the
session/counter algorithm, source identity, historical VLAN evidence, retention,
database ceiling, report API or the meaning of missing traffic.

Existing databases continue to use the legacy JSON layout. New databases also
default to that layout until explicitly selected by a maintainer. Opening an
existing database never converts it. A compact database has a version marker and
guard views that cause an older writer to fail during initialization. An older
binary cannot safely read or write a compact database; retain a matched runtime
and database rollback pair.

## Explicit offline copy

Use the same application revision that supplies both `accounting_storage.py`
and `accounting_migrate.py`. This is an operator tool, not a browser-selected
filesystem operation or an automatic installer action.

1. Inspect the registered project, actual accounting volume, source contract,
   checkpoint and available space. Do not guess installation or volume names.
2. Back up the runtime, configuration and a consistent database, including its
   WAL if present. Verify the backup and restrict access to customer evidence.
3. Stop the accounting writer. Other collectors may continue receiving logs.
   An online copy has a coherent snapshot but omits later commits; it must not
   be activated as a replacement for a writer that has continued collecting.
4. Run the tool against a **new, separate destination** on a filesystem with
   enough space for the copy, WAL and a safety reserve:

   ```sh
   python portable/accounting_migrate.py /actual/source/usage.sqlite /private/backup/compact.sqlite --progress
   ```

5. Require a successful result. The tool compares the count and ordered logical
   SHA256 stream of every events/facts/deliveries/daily/quality/state row. It also
   checks destination SQLite integrity and foreign keys. Failed copies are
   removed; the source is never replaced. Verification cannot be disabled.
6. Compare reports for pinned day/week/month/custom bounds, devices, VLANs,
   histories and diagnostics. Measure ingestion, report, retention and restart
   performance under representative resource limits before activation.
7. Coordinate a reviewed runtime/image and database switch while the writer is
   stopped. Preserve the original volume identity, ownership, permissions and
   source/timezone contract. Keep each database with its own WAL/SHM sidecars.
   The migration command intentionally provides no activation operation.

The default free-space reserve is 2 GiB; it is checked before and throughout
copying. The reserve is a floor, not an estimate of the required output size.
Publication uses an exclusive hard link on the destination filesystem after
checkpointing and syncing the completed file. Unsupported filesystems fail
without replacing the source.

## Rollback and evidence

Before activation, rollback means leaving the untouched source in use. After
activation, stop the new writer and retain its complete database and sidecars
before restoring the old runtime/database pair. Records collected after the
switch are absent from the old snapshot: returning to it requires an explicit
replay/reconciliation plan. Configuration rollback alone cannot reverse a
database format change. Never merge the two files or discard newer evidence.

Compact storage reduces representation overhead; it does not establish traffic
completeness or unlimited capacity. Keep the size ceiling and health checks.
Reported savings depend on actual data. Synthetic measurements and unit tests
do not prove production throughput or universal absence of regressions.

The field dictionaries and their order belong to format version 1. Do not derive
them from a changing normalization allowlist or reorder them. New field names can
use the extension dictionary without changing existing positions. A different
positional dictionary or physical layout requires a new format version and an
explicit migration. JSON remains the value codec; no pickle, compression library
or additional runtime dependency is introduced.

See [accounting semantics](ACCOUNTING_ENGINE.md), [QA](QA.md) and
[deployment verification](VERIFICATION.md).
