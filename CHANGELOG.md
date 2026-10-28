# Changelog

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
