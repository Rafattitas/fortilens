# FortiLens QA and release checks

Test observable behavior and data preservation. Report the exact source head,
environment, results and unresolved source-data limitations. Do not treat a green
container/process status as evidence that all FortiGate traffic was captured.

Initial entry is a separate browser check from clicking navigation links. Verify
fresh root entry, full reload of Monitoring and Device usage, legacy hash-only
links and the URL query for the selected view, without pressing Reload view.
Authenticated initial HTML uses a parser-created protected launch frame; the
launch endpoint must reject unknown/disabled views and unexpected redirect paths.
The root HTML must remain available with the helper offline and never include
session credentials. Mobile drawer/section controls and later navigation still
need actual browser review. Passing API tests alone does not prove these flows.

## Automated coverage

Portable CLI checks retain Python 3.10+ standard-library compatibility. Management
uses Python 3.11+ with a separate pinned environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r management/requirements-test.txt
python -m unittest discover -s tests -v
python tests/check_frontend.py
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
python -m ruff format --check tools tests/test_repository_checks.py tests/test_release.py tests/test_cli_errors.py tests/test_frontend_errors.py tests/test_management_errors.py tests/test_runtime_errors.py tests/test_service_errors.py tests/test_configuration_ca.py tests/test_probe_diagnostics.py tests/test_repair_runtime.py
python tools/check_repository.py
python -m coverage run -m unittest discover -s tests -v
python -m coverage report -m
python -m coverage xml
python -m pip_audit --strict -r requirements-dev.txt
```

Ruff formatting/import-order enforcement starts with the new tooling/tests;
runtime-wide formatting is a separate change. The repository checker validates
local documentation links and repository configuration without contacting a
firewall. Coverage reports executed paths and does not prove live compatibility;
dependency-audit findings require a separately reviewed dependency update.

For release changes, build and verify both distributions using
[RELEASING.md](RELEASING.md). Validate extracted artifacts, required runtime inputs
and absence of generated/private files. The full installer acceptance must run
from the packaged source on a disposable Ubuntu VM; building archives alone is
not an installation pass. Record any unavailable environment checks explicitly.

## Isolated integration environments

On a disposable Linux Docker-enabled test account:

```bash
python tests/integration_management.py
python tests/integration_stack.py
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
