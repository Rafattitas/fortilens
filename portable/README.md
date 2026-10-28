# FortiLens installation and operations

For one FortiGate per independent company installation. Requires **Linux x86-64, Docker Compose v2 and Python 3.10+**. Windows supports configuration and tests; deployment and host monitoring target Linux. No third-party Python packages are required. All product text is English.

Six monitoring services provide exporter, Prometheus, Grafana, Loki, Alloy and Node Exporter. Explicitly enabling accounting adds a constrained Python/SQLite service and protected reports. Existing installations must use the upgrade procedure below; never rerun init over their directory.

## New installation

From this package directory:

```bash
python3 setup.py init
python3 setup.py start
python3 setup.py doctor
```

`init` asks for site name, FortiGate IP/hostname, HTTPS API port, TLS/custom CA, hidden API token, syslog settings, VDOM and optional SLA/WAN fallback names. It offers read-only interface discovery and asks you to confirm Internet interfaces. Missing type/role/parent/VLAN evidence stays Needs review. No FortiGate settings are changed.

The accounting prompt is explicit. Choose its timezone (UTC or an installed IANA name), expected FortiGate serial/device ID and reporting address. Leave a missing serial unbound only with the displayed evidence limitation. A device-ID filter is not sender authentication. File-based configuration can enable it with `--enable-accounting`:

```bash
python3 setup.py init --config site.example.json --token-file /private/token --enable-accounting --output local
```

Copy/edit the non-secret example privately first. Relative CA paths resolve against the input config file. TLS verification stays enabled by default; an explicit opt-out is available but not recommended. API credentials are headers/auth files, never URL parameters or metric labels.

The installation directory must be new. Linux uses a 0700 parent; mounted files remain readable by nonroot container users. Windows generation does not create private NTFS ACLs. Protect installation/backup folders and never publish them. Generated configuration is JSON, also valid YAML.

`start` validates Compose, builds the optional worker, starts services, waits for Grafana/datasources and imports the initial dashboard. It preserves an existing dashboard and its edits. It may recreate services whose reviewed configuration changed. Node Exporter measures the Linux Docker host, not a separate firewall or a Docker Desktop laptop.

## Open dashboards and reports

Grafana: configured address/port, default `http://127.0.0.1:3001/d/forti-monitor`, username `admin`, password in `local/secrets/grafana_admin_password`.

Protected reports: configured reporting address/port, default `http://127.0.0.1:3002/`, username `admin`, separate password in `local/secrets/usage_admin_password`. Credentials stay in page memory; sign-out clears them. Use an SSH tunnel for loopback access or a reviewed HTTPS reverse proxy. Plain HTTP Basic authentication is not encrypted; do not expose it across an untrusted network.

The portal provides Today, rolling Last 7 days, This month, Recent 5 minutes and explicit custom ranges; IP/MAC/name search; top/all devices; VLAN filtering; device drilldown; retained daily/monthly history; WAN health/history; and data-quality evidence. GB is decimal. Recent per-device averages are received bytes over 5 minutes, not instantaneous packet counters. Fixed periods use the engine's current time independently of Grafana global selection.

The native Grafana dashboard has four chained selectors and repeating WAN rows, with clearer compact cards and tables. Legacy Loki device tables remain diagnostics; their absolute-time and identity/cumulative limitations differ from the new engine. Host panels are shared. Use `public_usage_url` to add a reviewed report link to the generated dashboard.

## FortiGate logging and metadata

Configure a suitable read-only API user, permissions, VDOM scope and trusted hosts on FortiGate. Review the official exporter permission reference. The tool never creates users, writes roles/policies or rotates tokens.

Send forward traffic syslog to the monitoring server's reachable LAN address. Defaults are TCP1514/RFC5424. Review the existing destination before changing it manually:

```text
config log syslogd setting
    set status enable
    set server "<MONITORING_SERVER_LAN_IP>"
    set mode reliable
    set port 1514
    set format rfc5424
end
```

For UDP choose matching UDP settings on both ends. CEF/CSV are not supported. Restrict receiver ingress to the intended device; the default TCP input is not encrypted or sender-authenticated. Correct clocks and complete forward-traffic logging are operational prerequisites, not settings FortiLens silently fixes.

```bash
python3 setup.py discover
python3 setup.py interfaces
python3 setup.py interfaces --metrics-only
python3 setup.py interfaces --select-wan "port1,uplink.100" --confirm-wan
```

Discovery lists received SLA labels; inventory reuses fresh exporter metadata then scoped read-only API fields. VLAN ID zero does not prove a physical interface. Exact VDOM/interface keys map VLANs; no subnet guessing. A post-start confirmed selection backs up configuration and writes a dashboard candidate, preserving live edits. Offline API JSON can be supplied with `--inventory-file`; it is sanitized and marked offline.

## Readiness and evidence

```bash
python3 setup.py doctor --json
python3 setup.py doctor --freshness-seconds 600
python3 setup.py doctor --api-check
```

Doctor checks configured services, Grafana/datasources, original metric timestamps, scrape/API probe, SLA/interfaces and recent logs. With accounting enabled it also checks engine authentication, polling/lag, inventory/source binding, rejected inputs and receipt-time configuration. It never restarts services or prints secrets/raw records. The optional `--api-check` adds one read-only GET to system/status; a successful endpoint does not verify all permissions.

Ready means that particular check passed. Problem means a demonstrated failure. Needs review/Unavailable preserve ambiguous or missing evidence. A failed probe alone does not identify a bad token; missing metrics are not WAN DOWN; quiet logs are not a proven delivery failure. Exit codes:0 all pass,1 any failure,2 warning/unknown without failure.

Accounting counts eligible outbound internal-origin sessions, sent=upload and received=download. Valid deltas include zero. Closing cumulative counters require a safe baseline; uncertain candidates stay pending. MAC is preferred; missing-MAC identities stay session/observation scoped. Replay IDs/checkpoints prevent ordinary duplicate counting; weak source identity/time is flagged. Late records inside the ledger recompute affected sessions transactionally.

Current inventory cannot establish historical VLAN/direction where contemporaneous evidence is absent. Cross-midnight deltas do not reveal exact packet-time allocation. No records means unavailable, not zero. Traffic completeness remains UNKNOWN. WAN availability uses valid observed UP/DOWN intervals and shows coverage/gaps separately. Route lookup is destination-specific, not a universal SD-WAN active-link claim.

## Storage and capacity

Defaults: Loki raw retention60 days; Prometheus90 days/5 GB; accounting ledger8 days, daily summaries400 days, database soft limit4 GiB. Poll windows120 seconds, overlap30 seconds, settle5 seconds and bounded paging prevent unbounded raw scans. Initial backfill31 days is incremental and can take time; processing lag stays visible. Historical whole-day/month summaries outlive raw detail; expired partial-day slices are rejected.

Configure `accounting_ledger_days`, `accounting_summary_days`, `accounting_max_bytes`, backfill/poll/window settings in site.json before deployment. Increase actual filesystem capacity too. The worker defaults to192 MiB/0.5 CPU; review its Compose limits for your measured load. The database limit stops ingestion visibly; it is not automatic disk expansion or a reason to delete a volume. Never change timezone/source/VDOM/device identity in an existing database without a separately reviewed rebuild.

Back up Loki, Prometheus, Grafana and SQLite using application-consistent volume snapshots or by stopping the relevant writer. Retain private config/secrets too. A retention setting is not proof that cleanup has happened or that the entire interval was received.

## Upgrade from v0.1.0

Use the new code with the existing installation at its original absolute path. Back up config and take application-consistent volume snapshots first. Never run init over the existing folder.

```bash
python3 setup.py prepare-upgrade --output /absolute/path/to/existing/local --enable-accounting --accounting-timezone UTC --expected-devid YOUR_FORTIGATE_SERIAL
```

Review `.upgrades/usage-<ID>/plan.json`, private `candidate/` files and the visual dashboard candidate. Preparation changes no running service. It preserves custom settings, existing secrets, volumes, retention, alerts and live dashboards. It adds a separate worker/volume, authenticated scrape and receipt timestamps for new Loki entries. The known unavailable original exporter digest is repaired; custom image references are preserved. Custom YAML/multiple listeners need a manual merge. Keep this private plan out of Git.

After explicit approval in your environment:

```bash
python3 setup.py apply-upgrade --output /absolute/path/to/existing/local --plan usage-<ID> --accept-config-changes
python3 setup.py start --output /absolute/path/to/existing/local
python3 setup.py doctor --output /absolute/path/to/existing/local
```

File/candidate hashes are checked before application; originals are saved. Applying files does not start services. Starting is the separate runtime action. Existing Grafana edits stay intact; manually review/import the visual candidate if wanted. Old Loki timestamps/history are not rewritten and missing data is not restored.

To roll back, stop the new writer first:

```bash
python3 setup.py stop-accounting --output /absolute/path/to/existing/local
python3 setup.py rollback-upgrade --output /absolute/path/to/existing/local --plan usage-<ID> --accept-config-changes
python3 setup.py start --output /absolute/path/to/existing/local
```

Rollback refuses to overwrite later operator edits. Added private worker files/passwords and the usage-data volume remain for recovery; no data is deleted. An old unavailable exporter digest may require the previously cached image or a manual merge retaining its reviewed repair. Do not move the installation: its absolute path determines the Compose project/volume namespace.

## Optional alert candidates and visual updates

```bash
python3 setup.py prepare-alerts
python3 setup.py prepare-alerts --notification telegram
python3 setup.py prepare-alerts --notification email
python3 setup.py prepare-dashboard-style --input /private/latest-dashboard.json
```

Alert files are paused candidates outside active provisioning. No notification policy or destination is changed. Supply environment secrets privately only when explicitly provisioning/testing your chosen contact point. Quiet syslog has no alert by default; `--expected-log-interval-seconds` is an explicit expectation, not an inference of lost traffic. Review candidates in an isolated Grafana before enabling.

The style command creates a unique local candidate preserving query groups/selectors; it never overwrites live Grafana. Legacy usage diagnostics/audit remain available via `prepare-usage-quality` and `usage-audit`. Full limits and procedures are in the linked source documentation.

## Troubleshooting and packaging

If reports are empty, check received evidence, scope, direction, clocks and metadata. NO DATA is not outage/zero traffic. If processing lags, check initial backfill, query budgets, storage and the sanitized engine error. If a range expired, request retained whole calendar days. Reporting and Grafana passwords are separate. Never delete history to clear an error.

```bash
python3 setup.py bundle --destination ../fortilens-single-device.zip
```

The 22-file source allowlist includes setup/doctor/inventory/quality/style/accounting/WAN/service/upgrade/alerts/UI, example config, guide/license/gitignore and 7 templates. It excludes local installs, private plans, original site exports, client logs, handover files and screenshots. The command does not publish to GitHub. Container images are pinned separately; ARM and every FortiOS version are not certified.

Source guides: [accounting](https://github.com/Rafattitas/fortilens/blob/main/docs/ACCOUNTING_ENGINE.md), [upgrade](https://github.com/Rafattitas/fortilens/blob/main/docs/UPGRADE.md), [troubleshooting](https://github.com/Rafattitas/fortilens/blob/main/docs/TROUBLESHOOTING.md), [optional alerts](https://github.com/Rafattitas/fortilens/blob/main/docs/OPTIONAL_ALERTS.md), [verification](https://github.com/Rafattitas/fortilens/blob/main/docs/VERIFICATION.md).

Primary references: [FortiGate Exporter](https://github.com/prometheus-community/fortigate_exporter), [Fortinet syslog CLI](https://docs.fortinet.com/document/fortigate/7.2.7/cli-reference/401620), [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/), [Alloy syslog](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.syslog/).
