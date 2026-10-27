# Architecture

FortiLens is a setup and observability layer around six upstream services. It uses existing FortiGate Exporter metrics for Performance SLA rather than adding a second collector.

| Service | Input | Purpose |
|---|---|---|
| FortiGate Exporter | Read-only FortiGate HTTPS API | Device, interface, and available Virtual WAN / SLA metrics. |
| Prometheus | FortiGate Exporter and Node Exporter | Time-series storage and PromQL. |
| Node Exporter | Linux host | Monitoring server health. |
| Alloy | Dedicated syslog receiver | Parse/forward logs to Loki. |
| Loki | Alloy log stream | Recorded device usage and log queries. |
| Grafana | Prometheus and Loki | Dashboards and query API. |

## Configuration and lifecycle

`portable/setup.py` validates settings and renders allowlisted templates. It generates Compose, datasource configuration, an exporter auth file, and an APIv2 dashboard. Stable generic UIDs avoid site-specific datasource references. Digest pins target the reviewed Linux/amd64 images; see `portable/templates/compose.json` for exact references.

Generated local files contain secrets and are never part of the source distribution. A new deployment has its own Compose project, volumes, and private directory. Only Grafana and syslog publish host ports by default. Exporters and databases use the internal Compose network.

## Dashboard scope

Four chained selectors scope FortiGate, VDOM, SLA, and WAN views. WAN supports multiple selections and repeating rows. All expands the listed options. Explicitly confirmed names constrain the initial device/VDOM choices; manual fallback options do not synthesize samples.

Host panels are shared across the installation. Device usage is a separate Loki path: VDOM filtering applies, but SLA and WAN selections do not filter its totals. The syslog receiver is dedicated to one FortiGate by operational convention, not a verified sender-identity mechanism.

Observed availability is based on received measurements. Sample coverage is shown separately because missing collection cannot establish an interface outage or full-period availability.

## Inventory and readiness

`interfaces.py` first uses fresh exporter labels. Read-only scoped API metadata fills gaps where available. Missing, conflicting, or limited evidence remains Needs review. Role and interface type are separate; VLAN ID zero alone does not imply a physical interface. The tool never changes device topology or roles.

`doctor.py` checks service/data readiness using original Prometheus sample timestamps. Its default mode does not issue a new direct FortiGate request. `--api-check` explicitly adds one HTTPS GET to `system/status`. Errors are categorized without logging tokens or response bodies.

## Boundaries

Single firewall per installation; no automatic complete topology discovery, authenticated-user accounting, billing guarantees, notification provisioning, or background Python usage collector. Real FortiOS compatibility and long-period accounting coverage need validation in each environment.
