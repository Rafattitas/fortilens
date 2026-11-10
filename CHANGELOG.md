# Changelog

## Unreleased

### Time zones

- Accounting days follow the FortiGate clock by default (`fortigate`). Every log
  carries the FortiGate's UTC offset; the first one sets it and a later change,
  such as daylight saving time or an administrator changing the timezone, applies
  from the next local day. Days already counted keep their boundaries; only the
  day a change takes effect is shorter or longer. A fixed IANA zone or UTC can
  still be chosen.
- An installation that already counts days in UTC or another fixed zone can
  switch to `fortigate` in Settings → Monitoring & Syslog → Accounting days. The
  switch is forward only; other accounting-scope changes stay locked.
- The display timezone defaults to `browser`: every viewer sees times in their
  own device timezone. Name a zone to show one timezone to everyone. The Overview
  timeline axis names the timezone it shows.
- Reports label their days, for example "Accounting days: FortiGate time
  (UTC+02:00)".

### Diagnostics

- `direct_api` shows the last Test Connection result and when it ran, instead of
  "Unavailable" five minutes after every test. A success stays until the
  connection settings change.
- New `INFO` status for choices made in Settings. Omitting optional exporter
  capabilities is `INFO`, not "Needs review"; custom exclusions stay `WARN`.
  `INFO` does not lower the overall status.
- An installation with no expected serial number can now bind one in Settings →
  Monitoring & Syslog. Apply checks it against Test Connection first, because the
  binding cannot be undone. `accounting_source` explains what binding changes.
- Settings no longer shows Accounting days as read-only on an existing
  installation, so the switch to `fortigate` can actually be made there.
- Data quality marks only the classifications that may hide Internet usage
  (`UNSCOPED SOURCE`, `UNRESOLVED DIRECTION`). Other log types and local
  traffic are listed without a warning.
- Test Connection labels the serial number it reports.

### Clock differences and slow reports

- A FortiGate clock ahead of the server no longer stops the console. Its newest
  records carried times after the server's "now", so every report for a period
  ending now (Today, Last 7 days, This month, This year, Recent 5 minutes) cut
  the current day short and read all of its raw records. On a test server 9
  minutes behind, Today took 215 seconds instead of 0.03, and requests queued
  behind it until every page showed "Monitoring is unavailable". These periods
  now end after the newest received record, from the daily summaries.
- New `clock_offset` check in Diagnostics: it compares FortiGate log times with
  the server's clock, says which one is ahead and by how much, and how to fix it.
  `WARN` above one minute, `FAIL` above five. Reports show the same note when
  the FortiGate is ahead.
- One slow request no longer blocks every page. A report request waits at most
  10 seconds for another to finish and then answers that the service is busy; a
  raw-record scan stops after 15 seconds and asks for whole days or a shorter
  range. When the console's 30-second limit is reached it says so and points to
  Diagnostics, instead of "Monitoring is unavailable".

### Upgrades and the CLI

- An upgrade now removes the `-pre-<time>` image copies left by earlier
  upgrades and keeps only its own, the one a rollback uses. A test server had
  collected 28 of them.
- `setup.py doctor` finds the services of a web-managed or adopted installation.
  It looked only under the CLI's own Compose project name and reported "no
  container found" for every service while they were all running. It now reads
  the project name from the containers started from the installation's compose
  file.

### Source formatting

- All Python source is now formatted with `ruff format`, and CI checks it. The
  runtime modules previously packed several statements on one line. No behavior
  changed: every file's syntax tree is identical before and after (docstring
  text aside), and the full test suite passes on the result.

### Documentation

- New [console guide](docs/getting-started/CONSOLE_GUIDE.md): what each page
  shows, where its numbers come from, and what to open when the Internet is slow.
- New [Diagnostics reference](docs/operations/DIAGNOSTICS.md) covering every
  check, its statuses and what to do.
- New [reporting API](docs/architecture/REPORTING_API.md) reference: periods,
  parameters, response fields and error codes.

### Grafana

- Removed the Grafana panels that read raw syslog from Loki for a whole period:
  recorded device usage for Today, Last 7 days and This month, their help text,
  and the received-record counts. The weekly and monthly ones took 30 to 55
  seconds each, and 134 to 170 seconds when opened together, so they showed
  errors after the console gateway's 30-second limit. Reports and Data quality
  in the console show the same evidence from the accounting store.
- The first configuration apply after upgrading removes these panels from the
  running dashboard, only where they still match what FortiLens generated, and
  the rows they leave empty, and updates the header text if it is unedited.
  Everything else in the saved dashboard is kept, and the previous dashboard is
  saved privately first.
- `prepare-usage-quality` is removed; `prepare-dashboard-style` leaves the
  retired panels out of its candidate.

## v0.5.0 — 2026-11-08 — websites, clearer navigation and an online demo

### Websites (opt-in)

- New **Websites** page: recorded usage by site name (`whatsapp.com`), and the
  devices that used each site. Off by default; switch it on in Settings →
  Monitoring → Website Usage per Device.
- The site comes from FortiGate Application Control and Web Filter logs, linked
  to the traffic session with the same key. Website totals use exactly the
  bytes accounting already counted, so they never add to or change device totals.
- Only registrable site names are kept, never full hostnames or URLs, for 90
  days. The page shows what share of usage has a known website.
- Guide: [website usage](docs/operations/WEBSITES.md).

### Console navigation

- The sidebar is grouped by the question it answers: Overview; Right now (Live
  usage, Internet links, Firewall dashboard); Recorded usage (Devices, Segments,
  Applications, Websites, Reports); System (Status, Data quality, Diagnostics, Settings).
  Every page is one top-level item with its own icon, and the active page has an
  accent bar.
- Renamed pages: Usage is now Devices, Internet is Internet links, Evidence is
  Data quality, System is Status and Live monitoring is Firewall dashboard. URL
  fragments such as `#devices` and `#quality` are unchanged, so bookmarks keep
  working.
- Browser tabs show the FortiLens lens icon, with a PNG fallback and a
  home-screen icon. The standalone report page carries the same icon.

### Online demo

- A read-only [online demo](https://rafattitas.github.io/fortilens/) runs the
  shipped console with synthetic data in the browser, published to GitHub Pages
  from `main` and checked on every pull request. See
  [online demo](docs/development/DEMO.md).

### Documentation

- README and guides use the new page names, show the dependency install option
  for a fresh Ubuntu 24.04 host, and say where the one-time setup token is.

### FortiOS compatibility

- Diagnostics shows the FortiOS version and build read from the FortiGate, and
  whether that version has been checked on a real device (only 7.2.14 so far).
- Live usage reads each FortiGate API part separately. Without FortiView, link
  totals still show; missing fields, an ignored bandwidth sort, and missing
  application statistics or interface counters are named on the page and in
  Diagnostics instead of showing as zero.
- Diagnostics warns when no Application Control IDs have arrived in logs.
- New guide: [FortiOS compatibility](docs/operations/COMPATIBILITY.md).

### Fixes

- Installations whose settings were saved before v0.4.0 now show the running
  accounting CPU limit. Before, the console showed 0.5 and a later Save reset a
  manually raised limit to 0.5.

## v0.4.0 — 2026-11-06 — live usage and busy sites

### Busy sites and capacity

- Accounting no longer stalls on busy sites. Each Loki read is bounded to 30,000
  records; when the 120-second window holds more, it halves until a read fits
  and grows back when traffic drops. Before, a full window stopped processing,
  which capped a site at about 200 records per second.
- Accounting is about 35% faster. Daily, application, quality and profile
  summary rows are updated in memory and written once per batch instead of
  being read, decoded, re-encoded and written for every fact, and repeated IP
  parsing and endpoint digests are cached. Results are unchanged:
  `tests/support/compare_engines.py` found every summary table and all 231,371
  ledger facts identical to the previous code on 510,000 real records, and on
  synthetic data with large and small batches.
- New capacity guide with measured figures: about 1,600 records per second at
  the production limits (0.5 CPU, 192 MiB) and 2,700 with 1.0 CPU, about 730
  bytes per raw record, and sizing for 100 to 3,000 devices.
  `tests/support/benchmark_scale.py` reproduces the measurement with synthetic data.
- An application name whose write was rolled back with a failed batch is now
  written again by the next batch.
- Settings → Storage has an Accounting Processing CPU choice (0.5, 1 or 2 cores)
  applied to the accounting service's Compose limit with the usual reviewed
  Apply and rollback. An existing installation shows its current limit.
- The README and screenshot gallery include Live usage; the roadmap marks Live
  usage and the performance work.

### Live usage

- A Live usage view shows who is using the Internet right now: download and
  upload per device, what each device is doing, applications, segments and WAN
  links. It refreshes every 10 seconds, pauses when hidden or on request, and
  keeps the last good update if a refresh fails.
- Figures come from FortiGate realtime FortiView statistics through the existing
  read-only API token, limited to traffic leaving through WAN interfaces and
  cached for 5 seconds. They are live estimates, not recorded usage totals.
- FortiView pages are sorted by bandwidth, so the 500-row page holds the
  busiest sources. Link totals come from WAN interface counters and include
  every session, including FortiGate's own traffic and VPN; the page shows them
  beside the share attributed to listed devices.
- Devices are named from recent accounting records (MAC, then IP) with their
  FortiGate profile. Application Control IDs (`appid`) are now kept as a
  descriptive label so live rows show application names; identities and totals
  are unchanged.

## v0.3.1 — 2026-11-06 — in-place upgrades

### Fixes

- Device details stay visible when the device's application list cannot load.
  Before, a failed applications request replaced the loaded details with
  "Evidence unavailable".
- `install.sh` no longer writes root-owned `__pycache__` folders into the source
  checkout when run with `sudo`.

### In-place upgrades

- `sudo ./install.sh --upgrade`, run from new reviewed source, upgrades a
  web-managed installation. It saves the management source, metadata and any
  service code it replaces, and tags the current accounting and syslog images.
  It rebuilds the management environment only when its requirements change and
  restarts and health-checks the management units. It then refreshes code in the
  accounting and native syslog services that already exist and rebuilds only
  those images.
- Any failed step restores the previous source, environment, service code and
  images and starts the previous version again. `--rollback-upgrade` returns to
  it after a completed upgrade. Settings, secrets and data volumes are never
  changed, and no service is added.
- Running the installer from changed source now points to `--upgrade` instead of
  a manual procedure.

## v0.3.0 — 2026-11-06 — unified console, Web Setup & Settings and application usage

### Applications view

- Reports now show recorded Internet usage by application and by Application
  Control category, which devices used an application, which applications a device
  used, and a GenAI filter. Records without a label are shown as unidentified.
- Labels (`app`, `appcat`, `apprisk`, `dstcountry`) come from forward-traffic
  records and never enter event identities, so records re-read across the upgrade
  are not counted twice. Application bytes are the same facts as device totals.
- Application summaries start when this version starts; earlier days remain in
  device totals only. The view shows the first day with labels.
- The view shows which device used which application. Organizations may need to
  tell employees before enabling it.
- Bytes recorded before labels were kept are reported apart, with the time labels
  began, instead of appearing as unidentified traffic.
- Protocol-only labels (QUIC, SSL/TLS, browser HTTP/HTTPS) are marked as generic,
  with guidance when they are a large share of traffic.
- The view adds summary cards (identified share, top application, GenAI use), a
  chart of the largest applications, a category mix, share columns, search, top-N
  and risk filters, and daily history for a selected application or category.
- FortiGate device detection (`osname`, `srchwvendor`, `devtype`) is kept as the
  latest profile per endpoint and shown beside device identities. It never merges
  identities.
- "Today" and other periods ending now no longer decode every retained record of
  the current day: day rows whose events all lie inside the window are used as is,
  as reports already did (about 6 s to well under a second on a real site).

### VLAN mapping and segment list

- Records received after an hourly interface refresh were marked "VLAN not
  verified" even when the mapping had not changed, splitting one VLAN into two
  rows. The time a mapping was first verified is now kept while it stays the same.
- The VLAN filter lists every VLAN the FortiGate reports, and the segment table
  names VLANs with no received records, with a hint to check policy logging.
- Summary cards are centred, and the application filters align on one row.
- A failed Applications request shows its error message again; the error path
  referred to a panel that no longer exists.

### Repository

- README, roadmap and screenshots describe the Applications view. Screenshot
  fixtures cover Applications and the interface inventory, and use the VLAN key
  format the accounting engine produces.
- Added a code of conduct.

### Smaller accounting storage for usage monitoring

- Unsupported records (local traffic, UTM, other log IDs) are stored as exact counted
  markers instead of full event and fact rows. They never contributed bytes.
- Compact storage version 2 compresses record details. Version 1 files upgrade in
  place on the next writer start; older rows stay readable. An older release cannot
  open a version 2 file, so keep a database backup with any rollback.
- New databases created by the accounting service use compact storage.
- Raw ledger retention defaults to 3 days (minimum 1). Existing installations keep
  their saved value until it is changed in Settings.
- Last 7 days now means today plus the six previous whole calendar days, and a This
  year period is added. Both read whole days from daily summaries.
- Near the database limit the worker expires the oldest raw days instead of stopping
  ingestion. Daily summaries are kept.
- `accounting_migrate.py --slim` builds a verified, vacuumed version 2 copy of an
  existing compact database without changing the source.

### Reliability and storage maintenance

- Keep accounting retention running when ingestion stops at the storage ceiling, cap
  the retained SQLite WAL at 128 MiB, and close the database on container stop.
- Add a delivery timestamp index to legacy accounting databases. It is created on the
  next service start and does not change stored evidence.
- Keep reports available when a stored hostname contains invalid Unicode.
- Report unknown states as unknown when the console cannot read service, log or
  pending-byte evidence, and resume Overview refresh after a hidden browser tab.
- Fix paused collection alert candidates that could not fire on probe, poll or lag
  failures, and stop exporting zero for a native receiver that has not forwarded yet.
- Tighten deployment, proxy and installer failure handling, the build context and
  report page content policy, and reduce repeated CI runs.

### A Precision console design

- Replace the console and report styling with the approved A Precision design,
  in light and dark themes, with IBM Plex Sans served locally.
- Add an Overview page built from existing APIs, an evidence status bar and a
  Ctrl K command palette. Existing routes, element IDs and API calls are kept.

### Source collection and diagnostics

- Include the vendored management fonts and their license in installer/full
  distribution inputs, and retain fonts plus the existing clock adapter in the
  Docker build context. Check dependencies referenced by the shipped stylesheet
  so installed and containerized management retain the source checkout's assets.
  Copy reviewed source files byte-for-byte so binary fonts and source hashes
  survive installation while preserving atomic writes and existing permissions.

- Add an opt-in compact SQLite ledger and a separately published, fully verified
  offline copy tool. Preserve exact event/fact/delivery evidence, frozen metadata,
  summaries, checkpoints and accounting calculations. Existing databases and
  fresh-install defaults remain legacy; no automatic conversion or activation.
  Include differential, failure, concurrency and isolated container rollback checks.
  Stream partial-day report inputs to bound memory, and reject occupied migration
  sidecars before copying or publishing a candidate.

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

- Reorganize the README around the questions FortiLens answers, who it suits and
  how to choose an installation path. Move maintainer guidance to the contribution
  guide and five historical records to `docs/history/`.
- Group guides under `docs/` by purpose and move integration and support scripts to
  `tests/integration/` and `tests/support/`. Five pointer pages keep the addresses
  the published v0.2.0 package links to. Application code and runtime behavior are
  unchanged.
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
- Preserve legacy implicit 443 exporter credentials/probe selections, real scrape
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
