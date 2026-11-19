# Reporting API

The read-only HTTP API of the accounting service (`portable/usage_service.py`).
The console's Live usage, Internet links and Recorded usage pages are built on
it. It is an internal interface: it has no version number and its fields can
change between releases. Every endpoint is a `GET` and nothing here changes the
FortiGate, the configuration or stored data.

## Reaching it

| Path | Authentication |
|---|---|
| Through the console: `/console/api/...` | The console session. The helper signs in to the gateway server-side; the browser never holds the reporting credential. |
| Through the gateway on the reporting port: `/api/...` | A gateway session cookie from its sign-in page. |
| The accounting service itself (inside the Compose network, or its loopback port when no gateway is installed) | HTTP Basic, user `admin`, the password in `secrets/usage_admin_password`. |

`/health` needs no authentication and returns `{"service": "running", "poll_success": true}`.
A request without valid credentials gets `401` `authentication_required`.

## Periods

Report endpoints take the same selection:

| Parameter | Meaning |
|---|---|
| `period` | `today` (default), `week` (today plus the six days before), `month`, `year`, `recent` (the last 300 seconds) or `custom`. |
| `start`, `end` | For `custom`: timestamps with an explicit UTC offset, for example `2026-11-10T08:00:00+02:00`. The range is `[start, end)`. |
| `at` | Optional end instant for the other periods, with an explicit offset. Without it the period ends now. |

Days follow the accounting timezone, returned as `timezone` and `timezone_label`.
Each parameter may appear once and is limited to 512 characters.

When the FortiGate's clock is ahead of the server's, a period that ends now ends
just after the newest received record instead, and the response carries the
difference as `source_clock_ahead_seconds`. An explicit `at` or a custom range is
used as given.

Whole days are answered from daily totals. A range that starts or ends inside a
day reads raw records, which are kept for `accounting_ledger_days` and are
bounded in time (see [Errors](#errors)).

## Endpoints

### `/api/alerts`

The alert state of the accounting service: `enabled` (a channel is on),
`channels` (`telegram`, `email`: whether each is on), `rules` (per rule its
`label`, `critical`, `summary` (listed in the daily summary only) and
`enabled`), `at_work` (now inside the working hours), `reports` (each report's
schedule), `reports_sent` (the period each report was last delivered for, and
`daily_at`), `open` (alerts open now: `rule`, `subject`, `title`, `text`,
`opened`) and `history` (the last 100 messages: `at`, `kind` of `start`,
`reminder`, `end`, `event` or `report`, `title`, `text`, `held` and
`deliveries` with each `channel` (`null` when a report could not be built),
`status` and fixed `error` code). It never carries a secret. The management
console reads it as `GET /api/alerts`; `POST /api/notifications/test` with
`revision` and `channel` sends one test message from the saved draft.

`/api/alerts/report?kind=daily|weekly|monthly` returns the report that would be
sent now as `subject`, `text` and `html`. It sends nothing; the console uses it
for Preview and Send a sample now.

### `/api/report`

Recorded Internet usage per device and segment.

| Parameter | Meaning |
|---|---|
| period selection | See [Periods](#periods). |
| `vlan` | A segment key from `vlans[].vlan.key`. |
| `device` | A `device_id`. |
| `search` | Text matched against IP address, MAC address, hostname and device ID. |

Main response fields: `start_utc`, `end_utc`, `totals` (`upload_bytes`,
`download_bytes` and their `pending_` counterparts), `devices`, `vlans`, `rows`
(one per device and segment), `flags`, `source_record_classifications`,
`observed_endpoint_identities`, `unverified_endpoint_identities`, `status`
(`RECORDED_ONLY` or `UNAVAILABLE`), `traffic_coverage` (always `UNKNOWN`),
`processing` (the same fields as `/api/diagnostics`) and `period`.

Totals are `null`, not zero, when no record matched.

### `/api/device`

`/api/report` plus `daily_history` and `monthly_history` for the selected
`device`. Used by the device details panel.

### `/api/applications`

Recorded usage by application and category.

| Parameter | Meaning |
|---|---|
| period selection, `device`, `vlan` | As above. |
| `app`, `category` | Select one application or category and add its devices. An empty value selects unidentified or uncategorized records; leaving the parameter out applies no filter. |

Main response fields: `applications`, `categories`, `devices`, `totals`,
`summary`, `untracked` (bytes recorded before labels were kept),
`tracking_started_ns` and `processing`.

### `/api/websites`

Recorded usage by website, when website accounting is enabled.

| Parameter | Meaning |
|---|---|
| period selection, `device`, `vlan` | As above. Totals are kept per day, so a period must start at midnight and end at midnight or within the current day. |
| `site` | Select one website and add its devices. |

Main response fields: `enabled`, `sites`, `devices`, `summary`, `first_day` and
`last_day`.

### `/api/live`

Current Internet bandwidth from the FortiGate's realtime statistics, cached for
five seconds. No parameters.

Main response fields: `observed_at`, `unit` (`bits_per_second`), `totals`,
`totals_source` (`INTERFACE_COUNTERS` when link totals come from interface
counters, otherwise `LISTED_SESSIONS`), `listed` (what the listed devices add up
to), `links`, `devices`, `vlans`, `applications`, `truncated` and
`compatibility` (which FortiGate API parts responded). These are estimates, not
recorded usage.

### `/api/wan`

Health of the Internet links from stored metrics.

| Parameter | Meaning |
|---|---|
| `seconds` | History window, 60 to 604800. Default 86400. |

Main response fields: `status` (`OBSERVED` or `NO DATA`), `interfaces` (state,
physical state, current `sessions` (the FortiGate's own count), rates, latency,
jitter, loss and `history` per link), `route`
and `observed_at`.

### `/api/wan/insights`

Diagnosis of the Internet links over a period, from the SD-WAN health-check and
session series in Prometheus. Results are reused until the next interval ends.

| Parameter | Meaning |
|---|---|
| `seconds` | Period, 3600 to 604800. Default 604800. |

The period is split into at most 1,008 intervals (10 minutes for 7 days,
2 minutes for 24 hours). Main response fields: `start`, `step`, `buckets`,
`check` (the configured health check), `loss_threshold_pct`,
`incident_min_seconds`, and:

- `links`: per line, `check` and `check_fallback` (the health check that judges
  it), `states` (one character per interval: `g` working, `w` losing packets,
  `d` down, `n` no evidence), `disagree` (`p` where its health checks gave
  opposite answers), `sessions` per interval, `summary` (share of sessions,
  average rates, loss and latency, `loss_minutes`, `down_minutes`,
  `disagree_minutes`, coverage) and `loss_by_load` (average loss by session
  range).
- `incidents`: trouble lasting `incident_min_seconds` or longer, with
  `peak_loss_pct`, `avg_sessions` and, for each other line, the share of the
  same time it was also affected.
- `findings`: rule-based conclusions with `kind`, `severity` (`warn`, `probe`,
  `info` or `good`), `interfaces`, `title`, `detail` and `evidence` (the page
  section that shows it).
- `links[].speed`: when the line's speed is set in Settings (`wan_speeds` in the
  site configuration), its `down_mbps`, `up_mbps` and the average and busy-time
  (95th percentile of interval averages) use of each, in percent; otherwise
  `null`.
- `routing`: the SD-WAN rules read from the FortiGate (`cmdb/system/sdwan`,
  read-only, kept for 5 minutes). `status` is `OBSERVED` or `UNAVAILABLE` with a
  `reason`. Each rule has `order`, `name`, `enabled`, `mode`, `sources`,
  `destinations`, `services`, `restricted_by`, `lines` (in priority order),
  `health_checks` and `issues` (`shadowed`, `overlap`, `manual` or
  `catch_all_manual`, each with its `text`).

### `/api/wan/incident`

Devices whose recorded Internet traffic left through one line in a window,
largest first. It reads the raw records, so the window must be within the ledger
retention.

| Parameter | Meaning |
|---|---|
| `interface` | A confirmed Internet interface. |
| `start`, `end` | Unix seconds; at most 12 hours apart. |

Response: `available` (`false` with `reason` `expired` when the raw records are
gone), `ledger_days`, `complete` and `scanned_until` (reading stops after 15
seconds and reports how far it got), `device_count`, `upload_bytes`,
`download_bytes` and `devices` (up to 15, each with `device_id`, `hostname`,
`ip`, `mac`, `vlan`, `upload_bytes`, `download_bytes` and `records`).

### `/api/inventory`

The FortiGate interface inventory used to map records to segments.

### `/api/diagnostics`

Processing state: `checkpoint_ns`, `last_delivery_ns`, `last_received_event_ns`,
`processing_lag_seconds`, `last_poll_ok`, `last_error`, `parser_rejections`,
`late_rejections`, `replay_candidates`, `ledger_floor_ns`, `summary_floor_day`,
`inventory_available`, `source_identity_bound`, `app_ids_seen` and `fortigate`
(FortiOS version and Live usage support).

### `/metrics`

Prometheus text format, scraped by the installation's own Prometheus:
`fortilens_accounting_*` (poll success, last poll, record and delivery times,
processing lag, rejections, truncated rows), `fortilens_usage_*` (bytes, pending
bytes, observed identities, quality records) and `fortilens_vlan_*`. Device rows
are capped by `accounting_metrics_rows`; `fortilens_accounting_metrics_truncated_rows`
says when the cap applied. Use `/api/report` for complete totals.

## Errors

Errors are JSON: `{"error": "<code>", "message": "<text>"}`. Messages are fixed
texts; exception details, paths and credentials are never returned.

| Status | Code | Meaning |
|---|---|---|
| 400 | `invalid_or_unavailable_report_range` | The period, timezone or range is invalid, or its partial-day detail has expired. Expired detail is never returned as zero. |
| 400 | `report_scan_too_long` | The range needs more raw records than can be read in 15 seconds. Choose whole days or a shorter part of a day. |
| 401 | `authentication_required` | Missing or wrong credentials. |
| 404 | `not_found` | Unknown path. |
| 414 | `request_too_large` | The request line is longer than 4096 characters. |
| 503 | `reports_busy` | Another long request holds the reader and this one waited 10 seconds. Try again; if it persists, check `clock_offset` in Diagnostics. |
| 503 | `accounting_storage_unavailable`, `timeout`, `upstream_unavailable`, `http_<status>` and other fixed codes | Required evidence is unavailable. No outage or zero usage is inferred. |

Through the console, a request that the gateway cannot finish in 30 seconds
returns `504` `console_timeout`; a stopped service returns `503`
`console_unavailable`.
