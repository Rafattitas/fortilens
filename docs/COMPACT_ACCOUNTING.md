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

A covering classification index avoids reading large payloads when counting
source evidence at partial-period boundaries. The original one-fact-per-event
constraint remains separate and intact. Index size and write cost must be
included in performance and capacity measurements.

Partial-day reports stream facts into the existing grouped totals instead of
retaining a complete list of decoded ledger payloads. Compact reads discard
non-Internet classifications before decoding their payloads; the original
classification evidence and source-wide counts remain stored and reported.
The direct `range_rows` list interface, ordering, exact boundaries, integer
arithmetic and tie handling remain compatible. Arbitrary large partial-day
selections can still be expensive; bounded memory does not imply constant query
time.

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

The destination and its `-wal`, `-shm` and `-journal` names must all be unused.
The tool checks again immediately before publication and preserves any unrelated
files. Use a private directory that no concurrent writer can access.

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

## Validation record (2026-11-05)

Runtime and regression changes through `6c5fbd6` were checked with:

- Windows/Python 3.14: 482 unit/regression tests, one Windows symlink skip.
  The same migration case passed in the Linux/Python 3.12 runtime container:
  64 compact tests and 14 migration tests, no skips. Linux checks ran nonroot
  with no network or writable source mount.
- Both extracted-artifact Docker scenarios, `integration_stack.py` and
  `integration_management.py`, passed. The stack scenario includes explicit
  legacy-to-compact copying, replay, restart and restoration of its original
  synthetic database. The full/portable payloads contain 147/27 files.
- All six logical table streams matched on a private corpus with 6,134,904
  events, facts and deliveries each. Eight pinned report selections, 40 device
  histories and diagnostics matched the original representation. Final streamed
  day/week/month results also matched under 0.5 CPU and 192 MiB limits.
- The indexed compact file measured 9,534,877,696 bytes versus a legacy main
  file of 20,435,488,768 bytes plus 1,040,061,072 bytes of WAL. These are one
  corpus's physical measurements, not a promised compression ratio.
- A private 40,000-record sample in legacy/compact/compact/legacy order under
  the same limits retained exact report digests. Compact files were about half
  the size; aggregate ingestion time and CPU improved in those trials. This
  sample does not establish sustained throughput at the final database size.
- Measured statement/branch coverage: accounting 87.70%, storage 87.88%,
  migration 84.77%, usage service 67.78% (81.26% across those four modules).
  Ruff, portable/management grammar, documentation links, workflow YAML/pinning,
  source hygiene and artifact allowlists passed.
- A manually authorized test deployment resumed from the preserved checkpoint,
  retained the original event cohort and continued committing new records.
  A controlled accounting-worker restart preserved committed progress and the
  source contract. The seven other running collectors/gateway containers and
  protected configuration, secrets and TLS files remained unchanged. Existing
  authenticated current week/month API requests completed in under 0.3 seconds
  in the observed samples, while unauthenticated requests remained rejected.
  Recovery main/WAL archives were verified both on and off the test server.

The bounded reader fixes a reproduced partial-day out-of-memory failure, but
does not eliminate scanning cost: the three large partial-day selections took
approximately 42–93 seconds in the capped lab while other checks used the disk.
Such requests can exceed the console bridge timeout. Further selective-query
work and sustained ingestion/catch-up testing remain separate acceptance items;
the deployment observations above do not claim that its backlog was cleared.
Unit/integration success is not proof of complete firewall traffic, billing
accuracy, universal FortiOS compatibility or absence of every regression.

No new GitHub CI run, authenticated live-browser session or disposable root-level
Ubuntu installer run was available for these changes. No release was published.
