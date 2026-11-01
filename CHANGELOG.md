# Changelog

## Unreleased — unified console and Web Setup & Settings

- Restore one dark operations workspace with flat summary strips, wider borderless
  monitoring/reports, clearer pending configuration and scoped report styling.
- Add keyboard-accessible mobile navigation, compact settings selection, loading
  feedback and direct access to the existing data-quality evidence page.
- Make monitoring the configured entry, prevent a duplicate report sign-in flash,
  validate shipped JavaScript in CI and privately cache only fingerprinted native
  code assets while keeping authentication, HTML and API responses uncached.

- Primary FortiLens navigation with protected native Grafana monitoring, fixed-period
  device/VLAN reports, WAN evidence and data quality behind the existing Viewer boundary.
- Independent nonroot FastAPI management, one-time administrator bootstrap, secure
  sessions/CSRF/rate limits and permanent English setup/settings/diagnostics.
- Read-only TLS-tested FortiGate connection and interface discovery, explicit WAN
  selection, supported monitoring/syslog/retention controls and measured storage evidence.
- Idempotent Ubuntu/systemd installer, explicit existing-project adoption, authenticated
  restricted helper, revisioned candidates, private backups and configuration-only rollback.
- Preserve legacy implicit443 exporter credentials/probe selections, real scrape
  intervals and additional Alloy listener bindings during adoption/settings changes.
- Mount native Grafana at its configured `/grafana/` path under the management
  login; preserve runtime router settings, plugin IDs and external links.
- Repository `AGENTS.md`, public project context, QA guide and a discoverable
  `fortilens-maintainer` skill for future AI-assisted work.
- Existing accounting algorithms, persistent volumes, native dashboards and active
  alert/notification configuration are preserved. Standalone v0.2.0 artifacts are unchanged.

## 0.2.0 — received-log accounting and evidence

- Optional persistent Python/SQLite service: session-aware directional accounting, replay idempotency, conservative cumulative fallback, bounded incremental Loki reads and retained daily/monthly summaries.
- MAC endpoints with explicit unverified session/observation fallbacks; no permanent identity from an IP or hostname alone.
- Exact VDOM/interface/VLAN/parent mappings, Internet direction checks, unknown historical mappings, VLAN aggregation and device drilldown.
- Protected responsive reporting portal with fixed day/rolling-week/month periods, custom ranges, search, top consumers, history and data-quality evidence.
- Clearer native Grafana layout that preserves existing query groups/selectors; live dashboards are not automatically overwritten.
- WAN physical/Internet/route evidence separated; observed outages/degradation, coverage and unknown intervals.
- Read-only engine diagnostics and paused optional Grafana alert/notification candidates.
- Reviewable hash-guarded v0.1.0 upgrade/rollback, existing secrets/history/volumes preserved, explicit runtime activation.
- Repair unavailable original exporter digest to the published v1.25.0 linux/amd64 manifest.
- Linux/Windows automated tests and isolated Docker acceptance with synthetic logs, authenticated scrapes and restart/replay checks.

Received-log accounting is not certified billing. Real FortiOS compatibility, site-specific completeness, packet-time allocation and production capacity still require deployment-specific verification. See the verification and accounting guides.

## 0.1.0

Initial public single-device monitoring stack, guided setup, interface inventory, chained selectors and read-only readiness checks. Existing release artifacts remain unchanged.
