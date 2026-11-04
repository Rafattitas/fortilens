# Changelog

## Unreleased — unified console and Web Setup & Settings

### A Precision console design

- Replace the console and report styling with the approved A Precision design,
  in light and dark themes, with IBM Plex Sans served locally.
- Add an Overview page built from existing APIs, an evidence status bar and a
  Ctrl K command palette. Existing routes, element IDs and API calls are kept.

### Source collection and diagnostics

- Replace short-window retention extrapolation with explicit next-24-hour
  additional file-size growth trends from bounded, persisted observations.
  Withhold accounting forecasts during catch-up, retain observation timestamps
  across refreshes, and separate applied retention/capacity facts from estimates.
  Compare additional growth on the measured filesystem; preserve all accounting
  calculations, retention policies, resource limits and stored records.

- Read accounting reports from committed WAL snapshots independently of the
  ingestion lock; reuse bounded exact report results and diagnostics, plus recent
  authenticated device/VLAN navigation results, while preserving accounting math,
  checkpoints, schemas, authentication and explicit refresh behavior.

- Display and edit the accounting database ceiling in GiB while preserving
  existing integer-byte API/configuration values and exact untouched limits.

- Add explicit source-preserving native UDP reception for rootful Linux Docker
  Engine, selectable in Web Setup/Settings and portable configuration. Keep the
  existing bridge default, exact source filter and data identities; use private
  loopback handoff/health ports without requiring an external rsyslog service.
  Document host-network isolation tradeoffs and local-only receiver metrics.

- Separate managed Device usage from period Reports: retain device inspection in
  the former and show aggregate totals, top consumers and segment distribution
  in the latter, reusing existing accounting evidence and filters.
- Preserve the applied SLA on inner-console Monitoring launches and compensate
  detected browser clock skew inside the native Grafana document only. Warn
  clearly, retain saved dashboards and explicit dates, and cover independent
  calendar-period panel queries without changing OS or stored timestamps.
- Add an explicit managed `fortigate`/UDP native log profile with a bounded
  standard-library receiver, internal RFC5424/TCP forwarding to existing Alloy,
  fixed internal health/metrics and additive source/build inputs. Keep the
  existing TCP 1514 / RFC5424 default and published releases unchanged.
- Separate native receiver readiness, TCP handoff, Loki receipt and accounting
  delivery; surface cumulative queue/validation losses without treating a source
  filter as authentication or a successful poll as complete capture.
- Keep blank SLA supported but require explicit diagnostic review; retain fresh
  VDOM-scoped observed names without selecting one automatically. A selected
  missing/stale/wrong-VDOM check cannot borrow another check's samples. SLA names
  remain site choices, and saved native dashboards are preserved.
- Expose narrowly scoped optional exporter exclusions as an explicit reviewed
  policy while preserving unchanged/legacy coverage and required probes. Omitted
  capabilities remain visible; TLS trust remains a separate owner decision.
- Document managed native UDP setup, best-effort limits and the retained
  operator-managed rsyslog alternative; add isolated source-path regressions.

### Error handling and recovery

- Keep configuration drafts independent of hidden browser autofill values,
  isolate the masked API-token form, and preserve reviewed or locked device
  identities during connection tests. Add first-run browser regressions.
- Collapse successful deployment progress into compact history, restore only
  active/failing operations, preserve explicit failure acknowledgement and guard
  duplicate Apply/rollback requests. Add real-browser lifecycle regressions.
- Resolve pending CA replacement/removal before candidate validation and return
  safe typed configuration errors. Retained active CA plus disabled TLS remains
  an explicit conflict requiring removal or verified TLS.
- Show connection-scope discovery readiness and recorded deployment revisions;
  diagnostics observe applied settings, distinguish polling from source delivery,
  report explicit optional-probe policy and warn about disabled TLS verification.
- Provide a narrowly validated optional-probe policy helper without changing
  default probe coverage or silently weakening existing installations.

- Return bounded, safe operational errors across management/helper HTTP,
  reporting/gateway services, CLI/installer/tools and browser transport. Keep
  successful API/CLI contracts and existing authentication checks.
- Recover partial configuration writes conservatively; release failed queue
  locks, retain observable job failures when journaling is unavailable, and avoid
  undoing committed configuration when only final diagnostics cannot be saved.
- Reject replayed deployment plans before entering automatic recovery, including
  a repeated request at the unchanged saved revision; explicit rollback remains
  a separate confirmed operation.
- Reject malformed upstream payloads before recording checkpoints or displaying
  telemetry. Keep missing evidence unknown and close resources on failed startup.
- Add browser deadlines and safe failure messages, clear rejected sessions,
  restore pending controls and require status review after unknown POST outcomes.
- Add synthetic failure-injection regressions without new runtime dependencies,
  schema changes, altered accounting/WAN formulas or production operations.
- Keep full-archive extraction timestamps compatible with the existing portable
  ZIP builder and verify rebuilding the identical portable payload after real
  extraction. Published assets are not replaced.

### Repository engineering

- Add an explicitly allowlisted full source distribution beside the compatible
  portable ZIP, with reproducible archive metadata, source provenance,
  SHA256 checksums and extracted-artifact tests. Existing release assets remain
  immutable; no new version is tagged or published by this preparation.
- Prepare read-only PR/manual package validation and disposable packaged
  Docker/Ubuntu acceptance before an explicitly published release can attach assets.
- Add development-only Ruff/EditorConfig, branch coverage, dependency advisory
  auditing, update automation and source/link/YAML checks while retaining the
  existing Python/OS matrix and real integration scenarios.
- Add a documentation index, realistic roadmap, sanitized issue/PR forms,
  maintainer recommendations, repository audit and validation report.
- Expand private-state/tool-output ignore patterns. Preserve runtime files,
  dependency pins, installer/build allowlists, CLI/API contracts, data formats,
  service/volume identity and existing security/accounting behavior.
- Document module responsibilities and defer broad runtime reformatting/import
  restructuring to independently characterized changes.

### Application changes already on main

- Restore one dark operations workspace with flat summary strips, wider borderless
  monitoring/reports, clearer pending configuration and scoped report styling.
- Add keyboard-accessible mobile navigation, compact settings selection, loading
  feedback and direct access to the existing data-quality evidence page.
- Make monitoring the configured entry, prevent a duplicate report sign-in flash,
  validate shipped JavaScript in CI and privately cache only fingerprinted native
  code assets while keeping authentication, HTML and API responses uncached.
- Load the initial protected view from server-rendered HTML and preserve its route
  on reload, including legacy hash links, without requiring Reload view in Chrome.

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
