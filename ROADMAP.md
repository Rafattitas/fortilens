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
- Today, last 7 days (today plus six previous days), month, year and custom-range reports, plus
  retained daily/monthly device history. These are existing capabilities.
- Observed WAN health/history/coverage with separate physical, SLA and
  destination-specific route evidence.
- Current-source Web Setup & Settings, independent management/helper services,
  reviewed configuration apply/rollback and protected native monitoring/reports.
- Read-only diagnostics and optional paused alert candidates.
- Application usage from FortiGate Application Control labels.
- Live usage from FortiGate realtime sessions and WAN interface counters.
- Compact accounting storage with a three-day raw ledger by default, and pruning
  of the oldest raw days instead of stopping at the storage limit.

The published v0.2.0 portable distribution predates web management and the unified
console; v0.3.0 is the first release with them. See [release preparation](docs/development/RELEASING.md) and the
[changelog](CHANGELOG.md) for distribution boundaries.

## Milestone 1 — Repository professionalization

Status: in place. `main` is protected (pull requests, all CI checks, no force
push or deletion), Dependabot security updates are on, and merged branches are
deleted. Publishing releases remains a maintainer action.

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
- Evaluate comparative reports (period against period) against retained summary coverage.
- Add reproducible report-generation and export compatibility tests.

## Milestone 4 — Website and application analytics

Status: application usage shipped on `main`; website and domain analytics planned.

- Shipped: usage by FortiGate Application Control label and category, the devices
  that used each application, GenAI and risk filters, daily history, and
  protocol-only labels (QUIC, SSL/TLS) kept apart from named applications. Labels
  never enter event identities, so accounting totals are unchanged.
- Planned: top-domain breakdowns from web-filter or DNS fields, with lawful,
  minimized retention and visible source coverage.
- Planned: measure storage and query cost before keeping per-domain history.

## Milestone 5 — Security analytics

Status: planned; no security-event ingestion or threat classification is added
by repository cleanup.

- Review security-log formats and supported FortiOS sources.
- Define event classification with synthetic fixtures and explicit uncertainty.
- Design separate dashboards and reviewed, opt-in alerting/notification behavior.
- Establish private vulnerability and sensitive-evidence handling for this scope.

## Milestone 6 — Performance and scalability

Status: in progress. Measured figures are in [capacity and sizing](docs/operations/CAPACITY.md);
they are not a capacity guarantee.

- Done: reproducible synthetic benchmark for 2,000+ endpoints
  (`tests/support/benchmark_scale.py`) and an old-versus-new comparison of every
  stored result (`tests/support/compare_engines.py`).
- Done: adaptive Loki read window, batched summary writes (about 35% faster with
  identical results) and a configurable accounting CPU limit.
- Planned: measure Loki storage, retention cleanup, backups and recovery at real
  rates for large sites.
- Validate larger single-device networks before considering a separate
  multi-writer, multi-device or high-availability design.

## Proposing work

Open a focused [issue](https://github.com/Rafattitas/fortilens/issues/new/choose)
with the use case and sanitized evidence. Follow [CONTRIBUTING.md](CONTRIBUTING.md)
and preserve the [accounting contract](docs/architecture/ACCOUNTING_ENGINE.md), existing API/CLI
interfaces and [security boundaries](SECURITY.md). Broad runtime refactoring,
database migrations and new collectors need their own reviewed changes.
