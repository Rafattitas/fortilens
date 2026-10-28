# Reliability release plan

The release answers device upload/download and ranking, VLAN Internet usage, WAN health and observed history, and collection quality. The existing Loki reports remain a legacy received-record view until the new accounting path is explicitly enabled.

## P0: accounting and VLAN correctness

1. Normalize forward-traffic records, preserve zero deltas, deduplicate stable event identities, and track session counter baselines. Quarantine unsupported/ambiguous contributions instead of inventing missing bytes.
2. Identify MAC endpoints when available. Missing-MAC identities stay explicitly session/observation scoped; IP and hostname alone never identify a persistent physical device.
3. Reuse interface discovery for VDOM/interface/VLAN/parent mapping and Internet direction. Never infer VLAN membership from subnets.
4. Persist a bounded event ledger and transactional daily device/VLAN summaries in SQLite. Recompute affected sessions on late arrival; stable IDs and checkpoints make retries/restarts idempotent.
5. Report calendar day/month in an explicit accounting timezone, rolling seven days, and custom ranges independently of Grafana's global time selection. Expose pending/uncertain contributions and collection evidence alongside totals.

## P1: usable reporting and monitoring

1. Improve the native Grafana layout and add fixed-period accounting panels. Provide searchable device/VLAN reports, device drilldown and retained daily/monthly history from summaries.
2. Preserve existing exporter/SLA metrics; distinguish physical link, observed traffic, route-selection evidence, Internet SLA state and missing telemetry.
3. Calculate availability from valid observed intervals; show gaps and coverage, and never bridge missing intervals into an outage.
4. Extend read-only doctor checks, metadata freshness, aggregation lag and service readiness with sanitized causes.

## Optional alerts and safe delivery

- Generate reviewed opt-in WAN/performance/collection alert candidates. Existing production rules and notification destinations remain untouched.
- Ship an explicit upgrade plan with backups, configuration validation and rollback. Never change FortiGate settings, replace secrets, delete volumes or reset a live dashboard.
- Test synthetic accounting, identities, duplicates, late delivery, VLANs, periods, WAN gaps and restart behavior. Exercise the rendered stack in an isolated Docker project before publishing.
- Publish reviewed source and release artifacts to GitHub. Production activation of the new accounting service is a separate explicit upgrade action; code/release publication does not imply a migration of the running site.

## Architecture decision

A single standard-library Python service plus SQLite is justified by the observed multi-million-line month scans and the need for persistent idempotency/session baselines. Loki remains the raw log store. SQLite is sufficient for one local writer on a small single-host installation and avoids a second database daemon; PostgreSQL is a future multi-writer/HA option, not a prerequisite. Resource limits, ledger/summary retention and late-arrival limits must be configurable. A successful ingestion query proves processing of received records, not complete source traffic.
