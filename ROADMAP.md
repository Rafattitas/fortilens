# FortiLens roadmap

This roadmap describes priorities, not committed delivery dates or implemented
features. Each milestone needs a scoped design, compatibility review and evidence
before release. FortiLens currently targets one FortiGate per independent
Linux/amd64 installation.

## Implemented baseline

- Native Grafana monitoring backed by the existing exporter, Prometheus, Loki,
  Alloy and Node Exporter.
- Optional persistent Python/SQLite received-log accounting: directional bytes,
  replay handling, exact VLAN metadata, device search and retained history.
- Today, rolling 168-hour week, calendar month and custom-range reports, plus
  retained daily/monthly device history. These are existing capabilities.
- Observed WAN health/history/coverage with separate physical, SLA and
  destination-specific route evidence.
- Current-source Web Setup & Settings, independent management/helper services,
  reviewed configuration apply/rollback and protected native monitoring/reports.
- Read-only diagnostics and optional paused alert candidates.

The published v0.2.0 portable distribution predates web management and the unified
console. See [release preparation](docs/development/RELEASING.md) and the
[changelog](CHANGELOG.md) for distribution boundaries.

## Milestone 1 — Repository professionalization

Status: prepared for review; publication and repository-policy changes remain
maintainer actions.

- Clear contribution/documentation navigation and sanitized issue/PR templates.
- Reproducible lint, incremental formatting, coverage and dependency checks.
- Separate full and backward-compatible portable distribution preparation.
- Archive/checksum validation, audit evidence and reviewable maintenance guidance.

Acceptance requires existing tests and applicable artifact checks to pass;
source/unit checks alone do not establish deployment compatibility.

## Milestone 2 — Reliable device and WAN accounting

Status: planned validation and extensions beyond the existing accounting engine.

- Reconcile controlled real-device windows against independent byte references.
- Validate late delivery, clock changes, long-lived sessions and historical
  VLAN/source evidence across documented FortiOS versions.
- Design per-WAN consumption attribution with explicit handling of SD-WAN,
  policy-dependent routes and unknown egress evidence.
- Review long-term aggregation and source-completeness evidence without
  converting missing records into zero usage or claiming certified billing.

Accounting/source contracts and retained databases require an explicit migration
design before any incompatible change.

## Milestone 3 — Advanced reporting

Status: planned extensions; day/week/month/custom reports already exist.

- Review export formats, access controls and protection of endpoint identity data.
- Design scheduled weekly/monthly delivery as an explicit opt-in capability.
- Evaluate annual and comparative reports against retained summary coverage.
- Add reproducible report-generation and export compatibility tests.

## Milestone 4 — Website and application analytics

Status: planned; not currently a shipped analytics capability.

- Evaluate available log fields and lawful, minimized retention requirements.
- Design top-domain/application and per-device breakdowns with source coverage
  and identity limits visible.
- Measure storage/query cost before adding historical analytics.

## Milestone 5 — Security analytics

Status: planned; no security-event ingestion or threat classification is added
by repository cleanup.

- Review security-log formats and supported FortiOS sources.
- Define event classification with synthetic fixtures and explicit uncertainty.
- Design separate dashboards and reviewed, opt-in alerting/notification behavior.
- Establish private vulnerability and sensitive-evidence handling for this scope.

## Milestone 6 — Performance and scalability

Status: planned; existing resource bounds are not a capacity guarantee.

- Publish reproducible ingestion/query/load benchmarks using synthetic data.
- Measure SQLite growth, retention cleanup, backups and recovery at real log rates.
- Evaluate indexing/storage improvements with accounting-preservation tests.
- Validate larger single-device networks before considering a separate
  multi-writer, multi-device or high-availability design.

## Proposing work

Open a focused [issue](https://github.com/Rafattitas/fortilens/issues/new/choose)
with the use case and sanitized evidence. Follow [CONTRIBUTING.md](CONTRIBUTING.md)
and preserve the [accounting contract](docs/architecture/ACCOUNTING_ENGINE.md), existing API/CLI
interfaces and [security boundaries](SECURITY.md). Broad runtime refactoring,
database migrations and new collectors need their own reviewed changes.
