# Web Setup & Settings

The current source includes an independent administrative web application. The
published v0.2.0 ZIP does not include it. Use a reviewed source checkout containing
`install.sh`; the dependency-free portable CLI continues to work separately.

A full source archive prepared by the [release tooling](RELEASING.md) contains
the same installer/runtime inputs. Verify its checksum and extracted contents
before using it, then run the commands below from its `fortilens/` directory.
This archive strategy does not modify or replace an existing published release.

## Ubuntu prerequisites and installation

Supported host: Ubuntu x86-64, Python 3.11 or later (Ubuntu 24.04+ recommended),
Python venv, systemd, Docker Engine and Docker Compose v2. Allow outbound access
to PyPI and the pinned monitoring image registries. Budget additional memory for
the web service (256 MiB ceiling) and helper (512 MiB ceiling), above the existing
monitoring workload. Size storage from actual ingestion and retained data.

```bash
git clone https://github.com/Rafattitas/fortilens.git
cd fortilens
sudo ./install.sh
```

The default management address is `http://127.0.0.1:3030`. The installer checks
host compatibility, Docker access, Compose and the management port; it creates
the `fortilens` system user, a private state directory, a locked Python venv and
two system services. It does **not** initialize or start a monitoring stack.

Missing OS packages are reported. To explicitly authorize Ubuntu prerequisite
installation, use `sudo ./install.sh --install-dependencies`. This option runs
apt update and installs `python3-venv ca-certificates tzdata docker.io
docker-compose-v2`; it is not an OS upgrade. Review Docker package compatibility
first if the server already uses Docker's own packages. The normal command never
installs system packages silently.

Repeat the same installer command to resume an interrupted installation or
verify an installed one. It preserves the admin, bootstrap, service credentials,
configuration, volumes and databases. It refuses changed source versions,
different access settings and unknown nonempty installation prefixes. Version
upgrades and host access changes require the reviewed manual procedure below.

From your workstation:

```bash
ssh -N -L 3030:127.0.0.1:3030 user@monitoring-server
```

Open `http://127.0.0.1:3030` locally. Use the one-time token printed by the
installer to create your own administrator username and password (12–256
characters). No default management password exists. The account-creation endpoint
closes once this account exists, and the token file is consumed. Do not put this
token in a URL, ticket or public screenshot. Before it is consumed, a host
administrator can read `/var/lib/fortilens/web/bootstrap.token` privately.

## HTTPS on a trusted LAN

Supply a server certificate/key that clients trust and whose SAN matches the
browser hostname. Install on a management LAN and restrict access to intended
administrators with host/network access controls. Do not publish first-run setup
to the public Internet.

```bash
sudo ./install.sh --bind 192.0.2.10 --port 3030 \
  --origin https://fortilens.example.internal:3030 \
  --tls-cert /path/to/fullchain.pem --tls-key /path/to/server.key
```

The address above is documentation only: use this server's real local address.
Non-loopback listeners require TLS. HTTP is supported only on loopback for SSH
or a separately configured trusted HTTPS reverse proxy. Such a proxy must retain
the configured Host/Origin, disable access logging of bodies, and never expose
the helper. Set `--origin` to the exact external HTTPS origin during installation;
keep the listener on loopback. Forwarded headers are not trusted. The portal uses
Secure/HttpOnly/SameSite=Strict cookies on HTTPS, server sessions and CSRF checks.

## The seven setup steps

1. **Administrator:** present the one-time token and create the first account.
2. **General:** company/installation name, IANA timezone and preferred date format;
   inspect read-only host, CPU, RAM, disk and Docker information.
3. **FortiGate Connection:** enter address, HTTPS port, VDOM and a read-only API
   token. Keep certificate verification enabled. For a private PKI, paste its PEM
   CA bundle. Test Connection performs a bounded HTTPS GET only. Authentication,
   permissions, certificate and reachability failures are reported separately.
4. **Network Interfaces & WAN:** discover metadata, search interfaces and explicitly
   confirm the Internet interfaces. Reported roles are suggestions to review;
   unknowns remain unknown. VLAN IDs/parents are exact device metadata. A configured
   interface's enabled state does not establish physical or Internet health.
5. **Monitoring & Syslog:** select supported collectors/views, native service ports,
   syslog transport/format/port, SLA name, interval, accounting timezone and expected
   serial. Accounting requires syslog; VLAN reports require accounting. WAN/SLA/
   interface controls share the existing exporter; it stops only if all three are
   disabled. Saved Grafana panels are preserved. General timezone/date format are
   management display preferences; saved native dashboards keep their own settings.
6. **Storage & Retention:** configure Loki logs, Prometheus metrics, accounting
   ledger/summaries and the database ceiling. Actual mounted-volume sizes are
   measured when accessible. A growth projection needs two positive-growth
   observations at least five minutes apart; otherwise the UI explicitly shows
   **Insufficient data for storage estimation**. Estimates are partial planning
   evidence, affected by compaction/compression/traffic, not a disk guarantee.
7. **Review & Apply:** Save Configuration creates a draft. Review Configuration
   Changes generates and validates a private candidate and checks published-port
   conflicts. Approve the review and choose Apply & Start FortiLens. Progress,
   individual service state and diagnostics update in the page.

The initial connection is never treated as successful until an actual test
returns success. Deployment may succeed while FortiGate collection is unavailable;
Diagnostics then shows missing evidence. A selected new WAN/source must have a
matching saved discovery. An unchanged existing connection does not require a
working FortiGate to edit general settings. Readiness does not certify usage totals.

## Settings after setup

The setup introduction disappears after successful deployment. Settings remains
available for General, FortiGate Connection, Network Interfaces & WAN, Monitoring
& Syslog, Storage & Retention, Authentication and Diagnostics. Normal changes do
not need terminal access. Save a draft, review and explicitly apply it. Changes
require collector/service recreation and can create collection gaps; review the
listed files and warnings. Concurrent editors receive a revision conflict rather
than silently overwriting each other. External file edits invalidate a plan.

Tokens and custom CAs are write-only. Blank inputs keep the stored values unless
an explicit replacement action is selected. A general settings save never
regenerates an API token, Grafana password or reporting password. Disabling TLS
verification requires explicit acknowledgement. Use Authentication to change the
management password; all management sessions are revoked.

Disabling a collector stops its service and preserves its definition/data. Core
Grafana/Prometheus/Loki stay available for historical data. Disabling accounting
stops the worker; its report queries are unavailable until re-enabled. A previously
installed gateway remains available. Disabling a view does not rewrite existing
Grafana panels or delete old data.

VDOM, accounting timezone and expected source ID are locked once a worker/source
contract exists. Changing them requires a separately designed migration. Retention
and database-ceiling reductions are rejected in the web UI to avoid unexpected
deletion. A host operator must independently back up data, inspect actual oldest
records, estimate the impact and perform a reviewed policy reduction. Configuration
rollback cannot recover deleted historical data. Increasing retention cannot
restore records already absent or expired, and does not resize a disk.

## FortiGate administrator tasks

The web portal performs no FortiGate writes. Its API account needs read access to
system/status and system/interface in the selected VDOM, and existing exporter
permissions for the metrics/SLA endpoints you use. Review Trusted Hosts, routing,
HTTPS port and the server's private CA. Select the real Performance SLA name;
SD-WAN may select different egress paths for different destinations/policies.

An authorized firewall administrator must configure the syslog destination,
protocol/format and forward-traffic logging. The receiver defaults to TCP1514 /
RFC5424, binds the monitoring host's configured syslog address, and should be
restricted to the intended sender at the network/host boundary. Matching a device
ID does not authenticate a syslog sender. Review [accounting limits](ACCOUNTING.md)
and synchronize both clocks. No received logs means unknown usage, not zero.

## Existing installations: explicit adoption

First identify the actual Compose project (`docker compose ls` / container project
labels), actual Compose file, site configuration, mounts and current credentials.
Take normal application-data backups using your existing operational procedures.
Do not guess a project name: changing it changes named-volume identity.

```bash
sudo ./install.sh --adopt /absolute/existing/fortilens \
  --project exact-existing-project --compose-file docker-compose.yml \
  --dashboard-uid actual-existing-dashboard-uid
```

Supported site configuration is `site.json` or `.fortilens/site.json` inside this
registered root. No init/render is run over the existing installation. Registration
imports settings, reads active retention, and leaves existing files untouched
until an approved Apply. Images, data mounts, dashboards, alert configuration and
service secrets are preserved. Existing exporter/syslog/Prometheus/Loki config
mounts must map unambiguously inside the registered root. Unrecognized/custom
mounts or port syntax fail closed and require a host-side review. External secrets,
remote Docker engines, host networking and arbitrary Compose layouts are not
automatically migrated. Reuse one installed management portal per host.

An existing unified gateway is accessed through a server-side authenticated
read-only proxy. Its reporting credential stays in the helper; the browser never
receives it. Management authorization permits this bounded reporting/Viewer path,
not Grafana administration. Native admin access retains its own sign-in. An older
deployment without this gateway keeps separate native Grafana/report authentication;
adoption does not silently reconfigure Grafana auth. A configured older installation
therefore opens Settings directly without rerunning the initial setup wizard.

## Diagnostics and recovery

Diagnostics calls the existing Python `doctor.run` logic through a restricted
service adapter. It distinguishes service availability, exporter scrape/probe
evidence, recent metric samples, logs, accounting lag and storage. Direct FortiGate
connection tests are explicit, cached briefly, and tied to the saved connection.
Failed collection never becomes a WAN outage. Settings, sign-in and health remain
available when Grafana, Loki or Docker is unavailable; helper unavailability is
reported without taking down sign-in.

After a failed Apply, inspect its failed step. Previous configuration is restored
when safe, and **Restore Previous Configuration** can recreate services from that
configuration. Deployment progress survives reload/helper restart. Rollback refuses
files edited externally. It never removes volumes or runs database migrations;
newly created files are retained for recovery. A first-install rollback stops only
that registered stack and keeps its data. Existing CLI upgrade/rollback remains
separate; never run it concurrently with a web deployment.

Emergency access requires a host administrator. Stop the web service before local
account recovery, run as the installed web user so file ownership is preserved:

```bash
sudo systemctl stop fortilens-web
sudo -u fortilens /opt/fortilens/venv/bin/python -m management.recovery \
  --data /var/lib/fortilens/web
sudo systemctl start fortilens-web
```

Run the recovery command from `/opt/fortilens/src` (or set `PYTHONPATH` to that
directory). It prompts privately for a new password, revokes all sessions and
does not touch monitoring secrets/data. Before an admin exists, add
`--rotate-bootstrap` instead; the new token is stored in the private token file.
This cannot reopen setup after an administrator exists. There is no HTTP reset
endpoint. Do not delete the auth DB to reset a password.

Host diagnostics: `systemctl status fortilens-web fortilens-helper` and
`journalctl -u fortilens-web -u fortilens-helper`. Request bodies and upstream
error bodies are not logged. Private configuration backups/jobs/audit are under
`/var/lib/fortilens/control`; auth/audit/session state is under `web`. Protect them
as secrets. The helper key and fixed registry are under `/etc/fortilens`.

To change access settings/certificates or update management source, stop both
management units, preserve these private directories and registry, and review the
new source/dependencies/unit changes with a host administrator. Do not remove or
reinitialize the stack. Certificate renewal updates `/etc/fortilens/tls.crt` and
`tls.key` with the existing root/fortilens ownership and restarts only the web unit.
Do not rerun a different source checkout as an automatic upgrader.

See [management architecture and compatibility](MANAGEMENT_ARCHITECTURE.md) and
[management verification](MANAGEMENT_VERIFICATION.md).
