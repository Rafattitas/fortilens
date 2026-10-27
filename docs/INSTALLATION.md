# Installation

The complete, versioned procedure is in the [portable installation guide](../portable/README.md). The same guide is included in the standalone ZIP.

## Before you start

- A Linux x86-64 server with Docker Compose v2 and Python 3.10+. Windows can generate configuration, but deployment and host monitoring target Linux.
- One FortiGate reachable over HTTPS from that server, with an API token and suitable read permissions. Review the [exporter's permission requirements](https://github.com/prometheus-community/fortigate_exporter#fortigate-configuration).
- A trusted HTTPS certificate or a private CA PEM bundle. Verification is enabled by default.
- A reachable syslog destination on the monitoring server. The default is TCP1514, RFC5424. Configure forward traffic logging on the FortiGate manually.
- Your VDOM name and, if available, your Performance SLA name. Interface names and VLAN metadata are reviewed during setup; Internet interfaces require explicit confirmation.

## First installation

```bash
git clone https://github.com/Rafattitas/fortilens.git
cd fortilens/portable
python3 setup.py init
python3 setup.py start
python3 setup.py doctor
```

`init` asks for your settings and hidden token, creates a private local installation, and refuses to overwrite an existing directory. `start` validates Compose, starts six services, waits for Grafana and both datasources, and imports the first dashboard. Repeating it preserves an existing dashboard UID.

Grafana defaults to `127.0.0.1:3001`. To browse from a company workstation, choose the server's LAN address during setup and permit access according to your network policy. The initial admin password is in `portable/local/secrets/grafana_admin_password`.

Use a separate installation and separate secrets for each company. This version does not combine multiple firewalls into one installation.

## What requires administrator setup

FortiLens does not create API users, grant permissions, change FortiGate interfaces or roles, or configure logging destinations. Review your existing syslog destination before changing it. The [full guide](../portable/README.md#3-connect-your-fortigate) includes a FortiOS 7.2 reference example and UDP alternatives.

Restrict syslog ingress to the intended sender. The dedicated receiver assumes one device; it does not authenticate the sender. Do not expose the plain HTTP Grafana listener or unencrypted syslog directly to the Internet.

## When data is missing

| Observation | Next check |
|---|---|
| Grafana cannot be reached | Listen address, published port, server firewall, and container status. |
| API probe fails | Run `doctor`; `doctor --api-check` explicitly adds one read-only status request for HTTP/TLS/connectivity evidence. |
| No SLA series | Correct VDOM, Performance SLA configuration, API permissions, and exporter support. No series does not establish WAN DOWN. |
| No traffic logs | Sender address, TCP/UDP and message format, forward traffic logging, clocks, and recent traffic. |
| Role/type/VLAN metadata is incomplete | Review `interfaces`; restricted API scope and missing labels remain unresolved. |
| Usage is incomplete | Read [accounting limits](ACCOUNTING.md). Confirmed WAN selection does not change the query's WAN-role condition. |
| Long usage report is slow | Review Loki health, storage, query range, and retention. Large log scans remain expensive. |

After startup, `interfaces --select-wan "port1,uplink.100" --confirm-wan` creates a local candidate rather than overwriting a running dashboard. Review/import `local/grafana/dashboard.interfaces-candidate.json` in Grafana.

For deployment changes, keep configuration, secrets, and data-volume backups outside Git. This release has no automatic migration or upgrade manager.
