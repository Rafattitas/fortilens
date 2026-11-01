# FortiLens QA and release checks

Test observable behavior and data preservation. Report the exact source head,
environment, results and unresolved source-data limitations. Do not treat a green
container/process status as evidence that all FortiGate traffic was captured.

## Automated coverage

Portable CLI checks retain Python 3.10+ standard-library compatibility. Management
uses Python 3.11+ with a separate pinned environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r management/requirements-test.txt
python -m unittest discover -s tests -v
python portable/setup.py bundle --destination dist/fortilens-single-device.zip
```

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

Use the current authorized branch/release scope and expected-head merges. Verify
the deployed source against the intended source files/tree, wait for all required
checks on the final head, then merge/publish when authorized. Verify main's result
as well. Update README/context/changelog if behavior or installation changes.
Never force-push divergent local history or publish a generated company workspace.
