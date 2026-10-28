# Recorded device usage: model and limits

These reports summarize bytes present in received FortiGate traffic logs. They are useful operational evidence, but they are not an authenticated-user ledger or a billing system.

Current-source dashboards add a reporting-period guide and collapsed received-record diagnostic tables. An offline audit flags repeat candidates and IP/name splits while keeping coverage unknown. See [usage-quality diagnostics](USAGE_QUALITY.md). Existing byte formulas and identity grouping remain unchanged.

## Record selection and byte accounting

The dashboard groups records by available MAC/IP/name fields and filters the selected VDOM. Its WAN condition is `dstintfrole="wan"`. Choosing Internet interfaces in setup affects monitoring selectors; it does not rewrite FortiGate roles or replace this accounting condition. If the device omits that role or reports another role, relevant traffic may be excluded.

Delta byte fields take priority, including an explicit zero. When a delta is absent, the cumulative byte fallback is limited to session-end records with log ID suffix 13. This avoids adding a full cumulative counter repeatedly to interim records. It does not deduplicate replayed log messages or recover missing records.

## Time periods

- Today uses the day's boundary.
- Weekly means the rolling last 7 days, not a named calendar week.
- Monthly starts at the calendar-month boundary in the selected timezone.
- An absolute Grafana time selection overrides relative panel periods.

The timestamp at which a record is logged can differ from the time its session transferred every byte. A session crossing a reporting boundary can therefore attribute bytes differently from a per-packet ledger. Validate clocks, timezone, and session logging policy before comparing totals with another system.

## What can distort totals

Missing or duplicated messages, ingestion interruptions, truncated retention, missing MACs, changing IPs, device naming, incomplete forward traffic logging, role filters, and session-boundary effects all matter. Two devices sharing identity fields may be grouped, and one device changing fields may appear in more than one group.

The receiver assumes one FortiGate per installation. It does not authenticate a sender or safely separate arbitrary additional firewalls. Do not route another device into the same stream.

## Query range and evidence

The default Loki configuration retains 60 days, permits a maximum 32-day query range, has a 1-minute query timeout, and a 90-second HTTP write timeout. Grafana's Loki datasource timeout is 90 seconds. Retention is a configured policy; verify actual disk cleanup and stored coverage operationally.

A successful 31-day query establishes that the request completed. It does not prove a continuous month of logs or correct device identification. Large scans can still time out as volume grows; this release does not pre-aggregate usage into a separate database.

Controlled validation has matched a 300-byte synthetic record to 100 TX +200 RX and checked delta-versus-session-end fallback logic. Historical sampled raw-log comparisons also supported the calculation change. Continuous full-month reconciliation on a new live FortiGate remains pending.

## Validate in your company

Start with a small, known traffic window. Check raw fields, expected WAN role, VDOM, duplicates, and logging coverage. Compare independently summed samples with dashboard totals, then expand to 7 days and the current month. Keep redacted evidence of gaps rather than treating missing data as zero.
