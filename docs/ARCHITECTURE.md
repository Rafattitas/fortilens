# Architecture

One FortiGate per independent Linux installation. Six monitoring services remain; an explicitly enabled seventh service adds persistent accounting. The existing exporter remains the WAN SLA source.

| Service | Purpose |
|---|---|
| FortiGate Exporter | Read-only HTTPS API metrics for device, interface and Virtual WAN/SLA. |
| Prometheus | Monitoring time series plus authenticated accounting snapshots. |
| Node Exporter | Linux monitoring-host health. |
| Alloy | Dedicated syslog receiver; accounting-enabled installs use receipt timestamps in Loki and retain source eventtime in the line. |
| Loki | Raw received logs and legacy diagnostics. |
| Grafana | Monitoring dashboards and separately reviewed optional alerts. |
| Optional Python/SQLite worker | Bounded incremental reads, event/session identity, transactional daily summaries and protected device/VLAN/history/quality reports. |

SQLite uses one writer, WAL and full synchronous transactions. Idempotent ingestion/checkpoint commits and retained baselines prevent ordinary retry/restart duplication. Daily summaries avoid reparsing a month of raw logs on every request. PostgreSQL is unnecessary for this single-host design; multiple writers/HA need a separate architecture review.

The worker reuses normalized interface discovery and exporter labels before scoped read-only API metadata. Exact VDOM/interface/parent/VLAN evidence is frozen per event. Later inventory cannot rewrite old mappings. A successful IPv4 route lookup applies to its stated destination, not every policy or per-flow SD-WAN decision.

Bounds include query windows, adaptive paging, response/record/session budgets, ledger/summary retention, a database-size check and a constrained worker (192 MiB, 0.5 CPU, read-only root, dropped capabilities, separate data volume). These are initial small-installation defaults, not a capacity guarantee. Measure actual load and size accordingly.

Grafana and reports default to loopback; syslog uses its configured address. Other services stay on the internal Compose network. FortiGate TLS verification is enabled with custom CA support. Secrets stay in local mounted files. The expected device-ID filter scopes records but does not authenticate a syslog sender.

Four Grafana selectors scope FortiGate/VDOM/SLA/WAN. Host panels are shared; legacy Loki tables have separate scope. Fixed-period device/VLAN answers come from the portal. Prometheus gauges are snapshots: a Grafana absolute historical endpoint can select older snapshots, so they are not independent fixed-period reports.

Repeated starts preserve saved dashboards. Upgrade preparation writes private candidates; file application and service activation are separate explicit actions. No volumes are deleted and no FortiGate settings are written. Optional alerts are paused candidates outside active provisioning and preserve existing routing.
