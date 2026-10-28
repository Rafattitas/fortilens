# Verification and compatibility

Verification separates source tests, synthetic integration and real deployment evidence. Received-log correctness is not certification that every packet was logged and delivered.

## Reproduce automated checks

```bash
python3 -m unittest discover -s tests -v
python3 portable/setup.py bundle --destination dist/fortilens-single-device.zip
python3 tests/integration_stack.py
```

The current suite has 118 cases covering configuration/packaging, original query preservation, identity conflicts and missing MACs, directional zero/delta/cumulative rules, session baselines/resets, retries/replays/restart/late arrivals, retained summaries, exact VLAN/direction/scope, fixed periods, missing WAN evidence, observed interval math, HTTP authentication, doctor boundaries, alert candidates and hash-guarded upgrades/rollback.

CI runs Python 3.10/3.12/3.13 on Linux and Python 3.12 on Windows. UTC works without an IANA database; one timezone case can skip on a configuration-only host without installed timezone data. Deployment remains Linux/amd64. The Docker job requires Linux Docker Compose v2 and OpenSSL; no real FortiGate is contacted.

## Isolated Docker acceptance

The reproducible job uses unique project-owned volumes and loopback ports. It validates configuration using the actual pinned Promtool, Loki and Alloy, builds the nonroot 192 MiB worker, and starts the stack plus a synthetic trusted-CA HTTPS metadata fixture. It exercises the generated v0.1.0 upgrade candidate while preserving original dashboard and Grafana/API secrets.

Two RFC5424/TCP records travel through real Alloy → Loki → accounting. Independent expected totals are 1,300,000,000 upload bytes and 2,700,000,000 download bytes, split across VLAN 100 and 200. Today/week/month, device histories, replay idempotency, worker restart persistence, authenticated reporting endpoints and Prometheus scrape are checked. Missing SLA metrics must stay NO DATA; a synthetic destination-specific route lookup is tested separately.

Optional Grafana rules load paused, without external contact points. Their actual provisioning representation and PromQL are validated. Promtool fixtures exercise explicit DOWN/healthy states and failed/missing collection gates. No notification is sent. Test cleanup removes only this disposable project's resources. A release requires the associated CI and release-bundle jobs to pass; consult the [workflow history](https://github.com/Rafattitas/fortilens/actions) for the exact source revision.

Earlier controlled tests also exercised read-only inventory pagination/sanitization, source-label scoping and legacy diagnostic comparisons. These remain synthetic/source evidence, not universal FortiOS compatibility proof.

## Visual QA

The local synthetic preview is explicitly labelled and excluded from the standalone bundle. Browser checks cover login, periods, VLAN filter, IP search, device history, WAN NO DATA/gaps and responsive layout. It is not a screenshot of live company measurements. The native Grafana style transformer preserves complete original query groups and all selector/time settings; runtime dashboard import never overwrites an existing dashboard.

## Deployment checks still required

- Real FortiOS endpoint/permission compatibility and expected device identity in each installation.
- Independent controlled traffic reconciliation, physical endpoint/MAC assumptions and source logging completeness.
- Continuous live week/month coverage and clock/interval-boundary behavior.
- Historical VLAN validity; present inventory cannot certify an old assignment.
- Per-flow SD-WAN route choices; IPv4 route lookup is destination-specific.
- Production capacity, database growth, retention cleanup, backups and recovery at the site's actual log rate.
- Intended email/Telegram recipient delivery after explicit operator activation.
- ARM images and non-Linux deployment.

Never post tokens, private upgrade plans, client configurations or raw company logs when reporting a failure. Supply a sanitized synthetic reproduction and exact revision.
