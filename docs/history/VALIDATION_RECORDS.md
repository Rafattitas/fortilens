# Validation records

Dated test runs, measurements and review notes moved out of the current guides so
that those guides describe how FortiLens works today. Each record describes the
source revision it tested. Its counts and results are not a claim about the
current source: use the actual head, its CI run and the
[local checks](../../CONTRIBUTING.md#local-checks) for that.

Records are grouped by the guide they came from, oldest guide sections first.

## From `development/QA.md`

### Presentation refresh, 2026-11-05

The refresh captured the seventeen-image set twice with matching PNG hashes and
reviewed it in desktop/light/degraded/mobile states. It passed twelve release tests
and six repository-check tests locally, including extracted-artifact link checks
and reproducible payload checks. Grammar, Markdown links/anchors, workflow YAML,
Ruff lint/style and frontend/capture-script syntax checks passed. A local
candidate full archive contained 169 files and its portable archive retained
27 files. Runtime source, installer inputs and Docker allowlists were unchanged;
no server deployment or live-device test was performed for this refresh.

### Record: 2026-11-10, source head `772c7bf`

On the test installation (web-managed, FortiOS 7.2.14 source), after
`install.sh --upgrade` from this head:

- The unit suite (601 tests) and CI passed on the head before deployment.
- Management restarted and the accounting service and gateway returned healthy.
  Settings, secrets and data volumes were not changed.
- Today, This month and Recent 5 minutes reports answered in under a second.
  Before the fix, with the server clock about nine minutes behind the FortiGate,
  Today took 215 seconds and requests queued behind it.
- `clock_offset` reported PASS after the server clock was corrected.
- The saved Grafana dashboard lost the seven retired raw-log panels and kept its
  other 24, its folder and its permissions. The previous dashboard was saved
  privately first.
- Read-only comparison for 2026-11-09, 08:00 to 22:00 local: download bytes in
  the received traffic logs were 84% to 107% of the WAN interface counters in
  every 30-minute window. In four readings the FortiGate's realtime per-device
  estimates explained 19% to 42% of the link total.

Not verified: entry and reload of the live console in a browser (the test host
uses a self-signed certificate), and totals against an independently known
transfer.

### Record: 2026-11-10, release `v0.6.0` from `bf99856`

- CI on `bf99856` failed once in the Docker step of `Web management / Python
  3.12` during a GitHub incident (the commit changed only CHANGELOG and README).
  The re-run passed; the other checks passed on the first run.
- The tag was created on `bf99856` and the release published. The release
  workflow rebuilt both archives from the tag, ran the packaged-source checks and
  attached them. Downloaded assets matched `SHA256SUMS.txt`; README download links
  returned 200.
- The ZIP matched the candidate built on the test server byte for byte. The full
  archive differs only in `RELEASE-MANIFEST.json`, which records the Git revision
  when built from a checkout.
- Found afterwards: the full archive left out 11 test modules and
  `tools/render_icons.cjs`, so the packaged-source check ran 30 of 41 test
  modules. Runtime and installer files were complete. Fixed for the next release;
  published assets are not replaced.

### Record: 2026-11-11, refactor stage 1: Prepare's file patching

- The characterization test `tests/test_configuration_patch.py` was committed
  before the code change (`4c83ad5`). Its 56 scenarios were recorded twice with
  identical results and reach 241 of the 243 statements of the old `_patch`.
- On the split code it passed unchanged, together with the management,
  native-host, repair, CA and runtime-error suites (88 tests).
- From a Git checkout in `python:3.12-slim` on the test server: ruff, the
  repository checks and 607 tests passed; `management/configuration.py`
  coverage rose from 85% to 91.7%. Both archives built and verified, and the
  extracted full archive ran the same 607 tests.

Not verified: an Apply on the test installation. Prepare's output is
byte-identical, so the installed files would not change.

## From `development/ERROR_HANDLING.md`

### Validation record — 2026-11-02

Baseline: repository-engineering branch `3fc21b37535e3e91e3c4b938e077f2e2c0765210`;
implementation branch: `codex/production-error-handling`. No installation was
updated. The runtime layout and existing dependency/schema/deployment contracts
remain; failure paths and internal recovery boundaries changed. No files moved
or were removed.

Executed on Windows with Python 3.12.14 and the pinned development environment:

| Check | Observed result |
|---|---|
| Complete unit suite with branch coverage | 260 source tests passed before the packaging follow-up; all 261 passed from the corrected, actually extracted Full archive, with no skips and combined line/branch coverage 73.58%. |
| Dependency-free portable environment | 197 tests, 6 explicit optional-dependency skips; passed before the final test-only cleanup synchronization correction. |
| Failure regressions | 82 new tests across management, deployment/configuration, services/storage, CLI/tools and browser transport. |
| Ruff correctness/imports and scoped formatting | Passed. Existing runtime-wide formatting remains separate. |
| Repository checker | Python grammar, local Markdown links/anchors, source filename hygiene and pinned workflow YAML passed. |
| Shipped frontend syntax | All three scripts passed Node compilation. |
| Workflow actionlint | Both workflows passed, with external shellcheck/pyflakes integrations disabled. |
| Dependency advisory audit | `pip_audit --strict -r requirements-dev.txt` reported no known vulnerabilities at execution time. |
| Candidate archive build/verify | Both distributions and SHA256 verified: 122 Full files and the existing 23 Portable files. |
| Real extraction and portable rebuild | Passed, including the added packaging regression; validated source payload tree `22dee490ac4825174514eb1ac85a4b257e295c2e`. |

Regression evidence includes auth/error headers, secret-bearing synthetic
exceptions, disconnects, worker checkpoint preservation, secondary storage/log
failures, partial-write recovery, lock release, committed-state preservation,
malformed upstream data, browser deadlines/session rejection and control cleanup.
A rejected historical APPLIED plan must not restore a previous configuration
unless this operation actually attempted installation. Threaded HTTP cleanup
tests synchronize on completion rather than assume response receipt means that
the server's `finally` block already ran.

The release tests exercise manifest completeness, byte reproducibility,
checksums and extracted CLI/accounting behavior. Actual extraction exposed a
pre-1980 ZIP timestamp failure that byte-only extraction had missed. Full archive
member times now use 1980-01-02 UTC; the unchanged portable bundle selector works
from the extracted source, and all 261 tests pass there. No tag or release was
published. Packaged Ubuntu/systemd installation still requires the separate
disposable acceptance procedure in [RELEASING.md](../development/RELEASING.md).

Docker, Unix peer/socket permissions and real Ubuntu/systemd installation cannot
run on this Windows host without a disposable Linux environment. Use the exact
branch head's [GitHub Actions runs](https://github.com/Rafattitas/fortilens/actions)
for that evidence; an earlier passing source revision does not validate this one.
Actual browser rendering/navigation/reload, live upgrades, real FortiOS traffic
completeness, production capacity and sustained fault/load testing remain
unverified here. Unit coverage is not a zero-regression guarantee.

### Review summary

Operational and malformed-input failures previously could escape HTTP/CLI
boundaries, leave queued operations locked, obscure partial configuration writes
or expose raw upstream error content. This change keeps the existing architecture
and successful interfaces while making those failure results safe and recoverable.
It adds no automatic mutation retries, deployment operations or runtime packages.

Review management/configuration recovery separately from portable transport and
browser handling. Preserve existing accounting/WAN fixtures and the real isolated
integration gates. Follow-up work includes sustained fault/load tests, actual
browser acceptance and separately authorized live-install upgrade validation;
none is implied by passing the local suite.

### Pre-merge replay review — 2026-11-02

An additional review of candidate `ae886eef6abf650c2caf2c8fb5b766670b64a843`
found that replaying an already-started plan at the same saved revision could
mistake its existing recovery marker for a partial write from the new request.
Deployment now requires the plan to remain PREPARED under the configuration lock
before entering the installation/recovery boundary. The existing `stale_plan`
rejection is retained; explicit rollback remains a separate confirmed operation.

A regression replays APPLIED, APPLYING and FAILED markers at the unchanged
revision and verifies no automatic restore, revision/file changes or service
start. Existing partial-write recovery remains covered. All 14 runtime failure
tests and the complete 262-test suite passed locally on Python 3.12.14, with
combined line/branch coverage of 73.58%. Ruff, scoped formatting, repository
grammar/links/YAML checks and actionlint also passed. PR acceptance must validate
the final candidate's packaged Linux/Docker/Ubuntu paths before the authorized
merge; source merging does not authorize a production rollout or release.

## From `architecture/COMPACT_ACCOUNTING.md`

### Version 2 validation record (2026-11-06)

A consistent snapshot of a production compact version 1 database (14.7 GB,
9,223,741 received records over about 2.6 days, 3.1 million a day) was tested in
disposable containers on the test host. The running installation was not changed.

| Check | Result |
|---|---|
| `--slim` copy | 14.7 GB to 5.2 GB; 4,914,661 unsupported records (53.3%) became markers; deliveries, daily, quality, state, 4,309,080 events and facts, and 16 reports (3 whole days, 12 six-hour unaligned windows, whole span) matched by SHA-256 |
| Full re-ingestion, old vs new code | Same records replayed from an empty file: 15.0 GB vs 5.8 GB (61% smaller), 10,473 s vs 9,045 s |
| Reports, old vs new code | 16 of 16 reports with identical totals and classification counts; 1,305 device rows with identical bytes, pending bytes, records, flag counts and identities; 136 of 136 device histories identical |

142 of 153 compared report lines were byte-identical. The remaining lines differed
only in fields copied from the first fact that opened a daily row (`uid`,
`generation_seed`, `flags`, `wan`): version 2 processes affected sessions in sorted
order, while the earlier set order varied between processes. These fields are not
aggregated usage values and the report page does not display them.

### Validation record (2026-11-05)

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

### Further storage review before publication

A follow-up compared the deployed format with additional physical layouts and
version-isolated prototypes. The application runtime, registered installation,
retention, authentication and source contract were not changed by this review.
Tests used a private 40,000-record fixture in disposable containers with 0.5 CPU,
192 MiB memory and networking disabled. Offline SQLite temporary files used the
private fixture filesystem; these maintenance tests do not certify execution in
the worker's 16 MiB temporary mount. No timing threshold was added to CI.

| Alternative | Observed storage result | Decision |
|---|---|---|
| 8/16/32 KiB SQLite pages | Approximately 1.9/3.2/3.6% smaller before compaction than the 4 KiB control | Keep 4 KiB: selected report reads were slower in the paired trials. |
| Offline compaction at 4 KiB | 67,514,368 to 63,750,144 bytes (5.6%) in the layout fixture | Optional maintenance of a separate copy, not an automatic writer operation. |
| Shared frozen-evidence dictionary | Approximately 13.4% smaller in an experimental layout | Defer: mean ingestion time/CPU increased about 2.6/2.8%; this small sample does not establish absence of a regression. A versioned migration and full compatibility testing would also be required. |
| Order-preserving nested metadata vectors | Approximately 7.3% smaller in an experimental layout | Defer: mean ingestion time/CPU increased about 6.9/7.0%, with slower selected report reads. |

Eight asserted-page-size trials checked all six logical table streams, report
digests, pruning, SQLite integrity and foreign keys. Four shared-evidence and
four order-preserving vector trials also passed the unchanged six-table/report
equality gate. Earlier unordered vector prototypes failed that strict gate and
were discarded; the verification requirement was not relaxed. All experimental
formats remained private and were not deployed or added to the runtime.

Two further control/copy comparisons verified compaction before and after 2,000
new fixture writes each. All logical streams and reports matched. Those short
write batches took approximately 4–17% longer on the compacted copies. Savings
and performance vary with fragmentation and workload; neither the 5.6% sample
saving nor a cost-free write rate should be assumed for a running installation.

`VACUUM INTO` can build a separate compacted copy while retaining the source;
follow [SQLite's documented behavior](https://www.sqlite.org/lang_vacuum.html).
Activation still requires a stopped writer, verified logical streams, integrity,
adequate temporary space and a matched recovery pair. Do not vacuum the active
writer as an implicit settings or report operation. `WITHOUT ROWID` is also not
a drop-in change here: the current delivery fingerprint depends on insertion
order through `rowid`, and event/fact ordinals must remain stable. SQLite itself
documents workload-dependent tradeoffs in its [layout guidance](https://www.sqlite.org/withoutrowid.html).

Archived recovery files should be measured separately from the active ledger.
Lossless archive compression is another capacity option, provided decompressed
content is verified against the original SHA256, source stability is checked,
recovery instructions retain the original filenames and SQLite sidecar pairing,
and restoration is tested before any original copy is retired. No recovery
snapshot was removed or reformatted during this review.

The follow-up added an offline-compaction regression preserving table streams,
report ordering, restart readability, replay and later arrivals. The complete
Windows/Python 3.14 suite ran 483 tests with the previously documented Windows
symlink skip; all 65 compact tests passed in the nonroot Linux/Python 3.12 runtime.
These results justify retaining format 1 for this change, not a claim that no
further optimization is possible. Full-corpus sustained measurements remain
necessary before adopting another format.

A final read-only observation of the authorized test deployment found catch-up
complete to approximately ten seconds behind server time, with successful polls,
no current processing error and recorded current-day results. Current day/week/
month API samples completed in approximately 0.013/0.018/0.075 seconds. The original
event cohort remained present while new records accumulated, and protected files
and the seven other running containers retained their previous identities.
These are observed samples, not a latency SLA or proof of complete source traffic.

## From `development/MANAGEMENT_VERIFICATION.md`

### Management suite, 2026-11-01

154 tests passed on Windows/Python 3.12 with the management dependencies
installed: the existing 131 tests plus 23 management tests. The earlier 153-test
suite also passed on Ubuntu.

## From `development/VERIFICATION.md`

### Portable verification

The earlier portable verification record covered 118 cases.

## From `operations/SYSLOG_NATIVE_UDP.md`

### Legacy validation record

An isolated check on 2026-11-02 used installed rsyslog **8.2512.0** and the pinned
Alloy **1.19.2** image. The example configuration was used with test-address
substitutions, and the Alloy container ran with Docker `--network none`. A
dedicated transient systemd unit used the `syslog` user/group and the example's
capability and sandbox restrictions while AppArmor remained enforced.

The check established that the nonroot process could bind test UDP port 514,
wrap a synthetic native message, and deliver it to the isolated Alloy parser
without a parsing error. The parsed body retained the tested `date`, `eventtime`
and UTF-8 content. A separate disallowed loopback sender did not forward.

This validates the tested adapter/parser path and restricted service startup. It
does not establish live sender delivery, all message-size/escaping cases, queue
overload or crash durability, independent byte totals, traffic completeness, or
compatibility with every Ubuntu/FortiOS version. Repeat acceptance on the intended
host; production validation is a separate, private deployment record.
