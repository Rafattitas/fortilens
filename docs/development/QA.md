# FortiLens QA and release checks

Test observable behavior and data preservation. Report the exact source head,
environment, results and unresolved source-data limitations. Do not treat a green
container/process status as evidence that all FortiGate traffic was captured.

Initial entry is a separate browser check from clicking navigation links. Verify
fresh root entry, full reload of Firewall dashboard and Devices, legacy hash-only
links and the URL query for the selected view, without pressing Reload view.
Authenticated initial HTML uses a parser-created protected launch frame; the
launch endpoint must reject unknown/disabled views and unexpected redirect paths.
The root HTML must remain available with the helper offline and never include
session credentials. Mobile drawer/section controls and later navigation still
need actual browser review. Passing API tests alone does not prove these flows.

## Automated coverage

For public interface images, use the [synthetic screenshot procedure](../design/screenshots/README.md).
It renders the shipped management and hosted-report frontend without a live
installation. The current seventeen-image set was captured twice with matching
PNG hashes, no JavaScript errors, external requests or outer-page horizontal
overflow, and visually reviewed in desktop/light/degraded/mobile states.
The manifest ties images to source hashes. This is presentation evidence, not
authentication, ingestion, native Grafana or real-device accounting acceptance.

The 2026-11-05 presentation refresh passed twelve release tests and six
repository-check tests locally, including extracted-artifact link checks and
reproducible payload checks. Grammar, Markdown links/anchors, workflow YAML,
Ruff lint/style and frontend/capture-script syntax checks passed. A local
candidate full archive contained 169 files and its portable archive retained
27 files. Runtime source, installer inputs and Docker allowlists were unchanged;
no server deployment or live-device test was performed for this refresh.

`tests/test_storage_projection.py` verifies independently calculated 24-hour
growth, bounded persisted sampling, refresh/restart stability and invalidation
after shrinkage, stale/changed history, clock rollback or ineligible processing.
`tests/test_storage_runtime.py` uses temporary synthetic volumes and a fake
registered project to check applied versus draft retention, separate accounting
ceilings, catch-up/disabled/unhealthy states, first-run availability, corrupt
cache recovery and additional-growth warnings on the volume filesystem.
Browser acceptance checks explicit horizon/window timestamps, unavailable reasons,
legacy projection suppression, safe text rendering and compact-screen layout.
These fixtures do not establish a real site's representative growth rate or
predict its full-retention capacity. Never insert synthetic forecast history into
a live installation to make a numeric estimate appear.

`tests/test_report_concurrency.py` uses disposable file-backed WAL databases to
verify reporting while an uncommitted writer holds the ingestion lock, consistent
checkpoints/history across concurrent commits, rollback isolation, read-only
enforcement, exact-bound/filter cache keys, pruning/metadata invalidation and
bounded caches. Browser acceptance also tests report reuse across managed iframe
navigation, explicit refresh, expiry and session revocation. These checks do not
replace live ingestion/capacity measurement on the intended host.
Delivery-observation regressions compare the derived timestamp with the actual
retained rows across late/replayed/malformed input, failed transactions, pruning
and legacy restart recovery; diagnostics must avoid the full scan when the
derived observation is present.

`python tests/integration/integration_native_host.py` runs only on a disposable rootful Linux
Docker host. It renders the actual host receiver profile, sends synthetic UDP
from two distinct bridge containers through the host socket, verifies the exact
allowed sender/body and rejected sender, checks nonroot low-port binding and
removes only its own generated project. Existing `integration_stack.py` retains
the bridge receiver → Alloy → Loki → independently known accounting totals test.
Unit coverage also checks bridge/host/RFC transitions, unchanged data identities,
adjacent health-port conflicts and the fixed loopback health route. Neither
scenario establishes complete production traffic collection.

Portable CLI checks retain Python 3.10+ standard-library compatibility. Management
uses Python 3.11+ with a separate pinned environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r management/requirements-test.txt
python -m unittest discover -s tests -v
python tests/support/check_frontend.py
python portable/setup.py bundle --destination dist/fortilens-single-device.zip
```

The standalone frontend syntax check uses a development Node.js executable to
compile the console, reporting and managed-status scripts. The failure-regression
suite also executes the actual transport and pending-control functions under Node
with synthetic requests, deadlines and response bodies; it explicitly skips when
Node is unavailable. These checks do not add a production Node dependency or
replace real browser entry, rendering and reload acceptance. See
[error handling and recovery](ERROR_HANDLING.md) for failure boundaries and limits.

## Repository quality and release checks

The complete development environment uses Python 3.11+ and
`requirements-dev.txt`; this does not change the portable Python 3.10+ runtime.
From the repository root:

```bash
python -m pip install -r requirements-dev.txt
python -m ruff check .
python -m ruff check tools tests/test_repository_checks.py tests/test_release.py tests/test_cli_errors.py tests/test_frontend_errors.py tests/test_management_errors.py tests/test_runtime_errors.py tests/test_service_errors.py tests/test_configuration_ca.py tests/test_probe_diagnostics.py tests/test_repair_runtime.py --extend-select I
python -m ruff format --check .
python tools/check_repository.py
python -m coverage run -m unittest discover -s tests -v
python -m coverage report -m
python -m coverage xml
python -m pip_audit --strict -r requirements-dev.txt
```

Ruff formatting is enforced for every Python file. Import-order enforcement
starts with the new tooling/tests. The repository checker validates
local documentation links and repository configuration without contacting a
firewall. Coverage reports executed paths and does not prove live compatibility;
dependency-audit findings require a separately reviewed dependency update.

For release changes, build and verify both distributions using
[RELEASING.md](RELEASING.md). Validate extracted artifacts, required runtime inputs
and absence of generated/private files. The full installer acceptance must run
from the packaged source on a disposable Ubuntu VM; building archives alone is
not an installation pass. Record any unavailable environment checks explicitly.

## Isolated integration environments

For compact storage, follow the measured [validation record](../architecture/COMPACT_ACCOUNTING.md#validation-record-2026-11-05).
Test migration destination sidecars and concurrent publication separately from
accounting equivalence. Partial-day report checks must include a corpus large
enough to expose decoded-payload accumulation: a successful small full-day
summary query does not validate memory or latency for a large partial range.
Offline compaction checks compare all six logical streams, stable report ties,
read-only reopening, replay and subsequent late/new writes. Assert actual SQLite
page size in layout experiments; a PRAGMA on an uninitialized empty connection
alone does not establish the next connection's file layout. See the
[follow-up storage review](../architecture/COMPACT_ACCOUNTING.md#further-storage-review-before-publication).

The stack lab also copies its synthetic legacy ledger into compact storage,
checks every logical table, starts the actual accounting container on that copy,
checks transport replay and restart, and restores the original fixture database.
It uses only its generated project/volume. The compact differential tests cover
late arrivals, exact large integers, pruning, rollback, process death and parallel
read snapshots. The migration tests cover space and verification failures.
These checks do not replace a full real-data comparison and resource-limited
performance measurements before an operator activates a migrated installation.

Run `python tests/support/benchmark_accounting.py --rows 40000 --repeats 2` for an
informational synthetic comparison. It creates and removes only its own temporary
databases, verifies report hashes and SQLite integrity, and reports wall/CPU time,
report latency, pruning and checkpointed file size. `--legacy-source` accepts a
trusted older `accounting.py` for an exact historical comparison; it executes that
code. Use identical Python, CPU/memory limits and an otherwise quiet test host.
Performance varies by workload; the benchmark has no CI timing threshold.

On a disposable Linux Docker-enabled test account:

```bash
python tests/integration/integration_management.py
python tests/integration/integration_stack.py
```

The management scenario runs an actual nonroot web container, authenticated local
helper and eight actual monitoring services with unique names/volumes/ports. It
checks fresh setup, TLS rejection/custom-CA success, WAN confirmation,15s scrapes,
protected native Grafana/reports, existing-install import, settings, volume markers,
SQLite integrity, rollback and management survival after test Grafana/Loki stop.
The existing pipeline checks independently known synthetic usage through the
received-log pipeline, replay/restart and old CLI behavior. These checks use
synthetic sources and cannot certify a real site's completeness.

The stack scenario also creates a separate disposable native UDP project. It
checks the configured-source filter, normalization through the pinned Alloy/Loki
pipeline, preserved source event time, independently known synthetic totals,
replay/restart behavior and receiver metrics. This is a separate acceptance path
from the unchanged direct RFC syslog project; record the actual executed result
for each rather than treating one as proof of the other.

For native receiver changes, run the focused standard-library tests too:

```bash
python -m unittest discover -s tests -p test_syslog_receiver.py -v
python -m unittest discover -s tests -p test_probe_diagnostics.py -v
python -m unittest discover -s tests -p test_repair_runtime.py -v
```

Use synthetic sources and disposable bindings to exercise allowed/disallowed
IPv4 senders, malformed/oversize/control-character rejection, exact native body
preservation, bounded queue overflow, reconnect/backoff, shutdown and failure
after a possibly partial TCP send. Keep health readiness separate from recent
handoff; neither establishes Loki storage or complete accounting. Cumulative
queue/validation losses remain visible after forwarding recovers; source-only
rejections do not automatically imply loss of authorized input. Ensure legacy
RFC profiles do not call the optional health adapter or acquire a new service.

For setup/apply changes, preserve a blank SLA without automatically selecting a
received name. A missing, stale, wrong-VDOM or future-dated selected check must
not pass by borrowing another SLA. Verify an explicitly applied SLA reaches the
managed report scope while saved native dashboards remain unchanged. Test
null/unchanged, explicit empty and selected optional-probe policies independently;
the empty list removes only the three known exclusions and retains custom rules,
other targets and secret bytes. No live FortiGate writes or TLS
policy changes are part of these fixtures.

CI additionally runs the actual Ubuntu 24.04/systemd installer twice and exercises
its real host services. `--installed` acceptance is restricted to a disposable
Actions VM; never point it at production. Secret/bootstrap output stays private.

## Read-only live deployment acceptance

Before an authorized update, record the actual project/Compose path, container
identities/start times, image IDs, mounts, config/secret hashes, dashboard generation
and queries/selectors, datasource/ACL/alert routing hashes and database integrity.
Take private configuration and consistent SQLite backups using the site's existing
operational identity. Keep all of this evidence out of public Git.

After deployment, check:

- Management/helper active, real HTTP readiness, correct nonroot user and denied
  web access to Docker/control/stack. Confirm loopback/HTTPS access policy.
- Bootstrap closure after admin creation, correct/incorrect login, revocation,
  unauthenticated/role/CSRF/origin/host rejection and masked secret responses.
- Real native Grafana Viewer identity, dashboard/selector reads, Prometheus/Loki
  datasource queries, all fixed report periods and WAN/quality evidence.
- Existing configuration/secrets/dashboards/images/volumes/history unchanged
  except for the reviewed scope; unrelated services and alerts untouched.
- Responsive browser navigation, first-run completion, permanent settings,
  clear failures/unknowns and review invalidation after editing a field/secret/WAN.

Configuration mutations and restart/failure injection belong in an isolated clone
unless specifically needed and authorized for the live change. Do not stop live
Grafana/Loki, inject logs, trigger notifications, delete volumes or change firewall
policies to prove a test case. Document the distinction in the delivery report.

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

## Real FortiGate source checks

When an authorized source is reachable, run the optional read-only compatibility
test documented in [MANAGEMENT_VERIFICATION.md](MANAGEMENT_VERIFICATION.md), then
compare configured VDOM/serial/interface/VLAN/SLA labels with actual metadata.
Validate fresh metrics/log timestamps, processing lag and independently known
traffic windows using [VERIFICATION.md](VERIFICATION.md). If the source is absent,
report it as unavailable; do not fabricate zero usage, an outage or 100% accuracy.

## Publishing

For Monitoring No data investigations, compare browser and server clocks before
changing queries. Execute the actual saved queries with their current variables
at both end timestamps. A correct server-default query does not validate a
browser-chosen range. Preserve legitimate missing SLA values on a failed link.
Verify management and inner-portal launch paths retain the applied SLA. Test the
optional page-local clock adapter with positive/negative skew, healthy clocks,
explicit dates, Date construction/static methods, subclasses and parent-window
isolation. Check calendar-day/month panel overrides in actual Grafana; shifting
only the global URL range does not cover those independent calculations.
Never change an operator's OS clock or stored timestamps to hide this failure.

Use the current authorized branch/release scope and expected-head merges. Verify
the deployed source against the intended source files/tree, wait for all required
checks on the final head, then merge/publish when authorized. Verify main's result
as well. Update README/context/changelog if behavior or installation changes.
Never force-push divergent local history or publish a generated company workspace.
