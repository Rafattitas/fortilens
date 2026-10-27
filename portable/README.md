# FortiLens: installation guide

An independent monitoring installation for one FortiGate per company. Requires **Linux x86-64, Docker Compose v2, and Python 3.10 or later**. Configuration generation also works on Windows; service deployment and host monitoring target Linux. No additional Python packages are required.

The package includes FortiGate Exporter, Prometheus, Grafana, Loki, Alloy, and Node Exporter. Its dashboard preserves the reviewed generation72 panel queries. Alerts, notifications, and a separate usage collector are outside this release. All supplied tool messages and documentation are in English.

Use a new installation directory. The tool refuses to overwrite an existing directory and does not migrate or replace an existing deployment's configuration or volumes.

## 1. Configure your installation

From the extracted package directory:

```bash
python3 setup.py init
```

Enter the company name, FortiGate IP or hostname, HTTPS API port, optional custom CA certificate, and syslog port and protocol. Token input is hidden. Credentials stay in the installation's `local/secrets/` directory and are excluded from the distribution bundle and Git.

After token entry, interactive setup offers a read-only interface inventory. It displays **VDOM, Interface, Type, Role, Parent, VLAN ID, and Review**. It does not select anything automatically. Enter the Internet interfaces for the configured VDOM and explicitly confirm them for this project's monitoring. Unreported roles/types, limited permissions, conflicting sources, or incomplete VLAN topology remain `Needs review`. Confirming a choice does not change its reported role on FortiGate.

Before services are running, inventory uses the interface API. After startup, it reads existing exporter labels first and requests missing metadata from the API. API reads are limited to the configured VDOM and selected metadata fields. Offline/failed discovery does not prevent manual preparation. Use `--skip-interface-discovery` to skip the interactive prompt.

Leave the SLA name and WAN interfaces blank to discover them from received metrics. Alternatively, enter your actual names, such as `ISP_Check` and `port3,port4.100`, as fallback dropdown choices. These choices do not fabricate measurements or imply that a WAN is UP. The default VDOM is `root`; change it to match your environment.

Grafana binds to `127.0.0.1` by default, making it accessible from the server itself. For access from company devices, enter the monitoring server's LAN IPv4 address at the Grafana listen address prompt. Default host ports are 3001 for Grafana and 1514 for syslog. Prometheus, Loki, and the exporters remain on the internal Compose network.

HTTPS certificate verification is enabled by default. For a private CA, provide its PEM certificate bundle. Disabling verification requires an explicit `no` at setup and affects only this installation. The API token is stored in the exporter's auth file, not in URL parameters or Prometheus labels. See the [exporter configuration reference](https://github.com/prometheus-community/fortigate_exporter).

For configuration from a file, copy `site.example.json` to a private `site.json` and edit it. The example contains a documentation address and no token. Put the token alone in a private file, then run:

```bash
python3 setup.py init --config site.json --token-file /private/fortigate-token --output local
```

A relative `api_ca_file` is resolved relative to the configuration file. Generated JSON configuration is also valid YAML. On Linux, the installation directory uses mode 0700; files inside it are readable by container users through their bind mounts. On Windows, normal account permissions apply; chmod does not establish private NTFS ACLs.

## 2. Start on Linux

```bash
python3 setup.py start
```

The tool validates Compose, starts the services, waits for Grafana and both datasources, and imports the dashboard through APIv2. Datasources use stable generic UIDs, and the Loki datasource timeout is 90 seconds. Repeating `start` preserves an existing dashboard rather than overwriting edits.

Grafana login is `admin`. A random initial password is stored in `local/secrets/grafana_admin_password`. Open the configured server address and port, then `/d/forti-monitor`. If the password changes in Grafana, or basic authentication is later disabled, the tool's authenticated API operations may require a credential review. Existing dashboard data is preserved.

Images are pinned by digest to the Linux/amd64 images inspected on October 27, 2026, including Grafana 13.2.2 and Loki 3.7.0. ARM and all FortiOS versions have not been verified. Node Exporter measures the Linux Docker host; running it through Docker Desktop does not establish accurate monitoring of a Windows host.

The new installation stores Loki data at `/loki`, which is writable by the pinned image's user. This does not change an existing deployment's data path.

## 3. Connect your FortiGate

The setup tool does not change FortiGate configuration or create API users or tokens. Configure an API token with the exporter's required read permissions and permit access from the monitoring server. Consult the [exporter permission reference](https://github.com/prometheus-community/fortigate_exporter#fortigate-configuration).

Send syslog from the single FortiGate to the **monitoring server's reachable LAN address**, using the configured port and protocol. The default is TCP with RFC5424. This FortiOS 7.2 example is for review and manual application to the intended destination; review any existing destination before replacing it:

```text
config log syslogd setting
    set status enable
    set server "<MONITORING_SERVER_LAN_IP>"
    set mode reliable
    set port 1514
    set format rfc5424
end
```

Change 1514 if necessary. For UDP, select `mode udp` on FortiGate and `udp` during setup. `reliable` selects TCP/RFC6587; `rfc5424` selects the message format, as documented in the [Fortinet CLI reference](https://docs.fortinet.com/document/fortigate/7.2.7/cli-reference/401620). CEF and CSV are not supported by this receiver configuration. Forward traffic logging and correct clocks are needed for usage reports. Receiving some syslog does not prove complete traffic coverage.

## Discover and select interfaces

The dashboard retains **FortiGate → VDOM → SLA Check → WAN Interfaces**. It discovers received labels and repeats interface cards according to the selected interfaces. All expands listed choices; it does not select every LAN port. Manual fallback choices are restricted to their configured device, VDOM, and SLA.

When Internet interfaces are explicitly confirmed, the WAN dropdown is restricted to those names for the configured device/VDOM. Confirmed choices can appear before SLA data arrives, but measurement panels still show missing data when no samples exist. Other VDOMs require separate review. Legacy setups without a confirmed selection retain their previous SLA-based discovery behavior.

To list received SLA and interface labels without changing configuration:

```bash
python3 setup.py discover
```

`discover` lists received SLA labels only. For the interface/type/role/VLAN inventory, use:

```bash
python3 setup.py interfaces
python3 setup.py interfaces --json
python3 setup.py interfaces --metrics-only
```

`interfaces` uses fresh exporter `name/vdom/parent/vlanid` labels first, then read-only API metadata where needed. The current exporter does not report interface roles, and VLAN ID zero alone does not prove that an interface is physical. `--metrics-only` skips direct API requests and leaves missing fields unresolved. API access can be restricted or partial; the report states the scope and evidence limits. It does not classify interfaces based on names, default routes, SLA membership, or traffic volume.

To select from the reviewed inventory after startup:

```bash
python3 setup.py interfaces --select-wan "port1,uplink.100" --confirm-wan
```

This requires every selected name to exist in the configured VDOM. It backs up `site.json`, saves `confirmed_wan_interfaces` and a sanitized `interface-inventory.json`, and creates `grafana/dashboard.interfaces-candidate.json`. **The running Grafana dashboard is not overwritten**: review/import the candidate in Grafana to apply a selection made after startup. Selection during initial `init` is included in the first dashboard import automatically.

For offline preparation, supply a saved interface API JSON response. Extra fields such as passwords and IP configuration are discarded; only normalized inventory metadata is saved:

```bash
python3 setup.py init --config site.json --token-file /private/fortigate-token --inventory-file interfaces-response.json --select-wan "port1,uplink.100" --confirm-wan
python3 setup.py interfaces --inventory-file interfaces-response.json
```

Offline evidence is explicitly marked as not verified live. For file-based initialization with a live API read, use `--discover-interfaces`. No method changes FortiGate interface roles, VLAN definitions, or policy configuration.

If no metrics are available, SLA discovery returns an empty list with an explanation. It does not infer a WAN outage. The interface inventory covers accessible returned data rather than guaranteeing a complete device topology. VLAN and role are separate fields: a VLAN may have a LAN, WAN, DMZ, or undefined role.

## Check readiness

```bash
python3 setup.py doctor
```

The English report checks the six services, Grafana, both datasources, exporter scraping and probe results, SLA/interface metrics, and recent logs. It uses actual Prometheus sample timestamps and a default freshness window of 300 seconds.

| Status | Meaning |
|---|---|
| Ready | The specific check passed. A received interface metric with value 0 still proves data reception, not that the link is UP. |
| Problem | Evidence of a failed check, such as a stopped container or failed probe. A failed probe alone does not identify a bad token. |
| Unavailable | Data is absent or cannot be verified. Quiet traffic can produce no logs; missing SLA metrics do not establish WAN DOWN. |
| Needs review | Partial data, missing configured choices, initialization, or timestamps that need investigation. |

The command is read-only. It does not restart services, modify FortiGate, change tokens, or configure alerts. By default, it uses collected metrics and does not send a new direct request to FortiGate.

To explicitly add one read-only HTTPS request to `system/status`:

```bash
python3 setup.py doctor --api-check
```

This distinguishes HTTP 401, HTTP 403, DNS, timeouts, TLS, and connection failures. It uses the local token without printing it or response bodies, does not follow redirects, and preserves the configured TLS policy. Success on this endpoint does not verify every SLA or VDOM permission. A Grafana login rejection is reported separately from FortiGate token authentication.

For JSON output or a different freshness window:

```bash
python3 setup.py doctor --json
python3 setup.py doctor --freshness-seconds 600
```

The supported window is 60–3600 seconds. Exit code 0 means all checks passed; 1 means at least one failed check; 2 means warnings or unavailable evidence without a failed check. JSON excludes raw log lines, response bodies, and credentials.

## Usage accounting limits

- One FortiGate is assumed for the dedicated syslog receiver. This is a scope assumption, not a network filter or verified sender identity. Do not send another device's logs to it.
- Reports group by MAC/IP/name, not authenticated user accounts. VDOM filters usage; WAN selection affects SLA/interface panels, not device usage totals.
- Delta fields take priority, including zero. Cumulative fallback is allowed only on session-end records 13 when a delta is absent. Missing/duplicate logs, missing MACs, and period boundaries remain limitations.
- Confirmed interface choices scope monitoring dropdowns; they do not replace the existing usage query's `dstintfrole="wan"` condition. Selecting an interface whose device role is undefined does not automatically include its traffic in usage totals.
- The weekly report covers the last 7 days. The monthly report starts at the calendar month boundary in the selected timezone. An absolute Grafana time range overrides relative panel periods.
- Loki retention is 60 days, maximum query range 32 days, query timeout 1 minute, and response write timeout 90 seconds. Large scans remain expensive; a successful 31-day range does not prove continuous 31-day coverage.
- Prometheus scrapes every 60 seconds with a 40-second timeout, allowing the exporter's 30-second probe budget. These are new-package defaults and do not change the existing deployment.

## Build a distribution bundle

```bash
python3 setup.py bundle --destination ../forti-monitor-single-device.zip
```

The ZIP allowlist contains 13 files: setup, readiness checks, interface inventory, the MIT license, this guide, the example, gitignore, and six templates. It excludes installation directories, inventory responses, tokens, original site exports, handover documents, and client screenshots. The command does not publish anything to GitHub.

References: [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/), [Loki datasource timeout](https://grafana.com/docs/grafana/latest/datasources/loki/configure/), and [Alloy syslog](https://grafana.com/docs/alloy/latest/reference/components/loki/loki.source.syslog/). Dashboard import uses APIv2 rather than relying on file provisioning compatibility with the v2 schema.
