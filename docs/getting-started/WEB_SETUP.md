# Web Setup & Settings

FortiLens includes an independent administrative web application. It ships in
full archives from v0.3.0 and in current source; portable ZIPs do not include it.
Use a reviewed checkout or full archive containing `install.sh`; the
dependency-free portable CLI continues to work separately.

A full source archive prepared by the [release tooling](../development/RELEASING.md) contains
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
different access settings and unknown nonempty installation prefixes. Use
[`--upgrade`](#upgrade-to-a-new-version) for a new version; host access changes
require the reviewed manual procedure below.

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
   measured when accessible. Growth trends estimate **additional file-size growth
   over the next 24 hours**, not the storage required for the entire retention
   period. At least one hour and four eligible observations are required; status
   requests collect samples at most once every five minutes. The displayed window
   and observation timestamp remain unchanged between samples. No background
   sampler is implied: a long gap starts a new observation history.
   Accounting trends are withheld while processing is unhealthy or more than
   two polling intervals behind (with a minimum allowance of two minutes).
   Its applied ledger retention, summary retention and
   configured ceiling are shown separately; the mixed database is never
   extrapolated across the summary retention period. The ceiling is an ingestion
   guard, not an estimate or a guarantee that growth stops safely at that size.
   Compaction/shrinkage, volume or applied-retention changes, stale observations
   and clock rollback invalidate the corresponding trend. Unavailable trends
   remain explicit, without invented zero growth. Warnings compare additional
   growth with free space on the measured volume's filesystem. Estimates exclude
   OS files, backups and other storage, and do not guarantee future traffic or
   full-retention capacity.
   The accounting database ceiling is entered in GiB (for example, `9` or `9.5`),
   matching the storage measurements. Existing byte ceilings are preserved when
   the field is left unchanged; configuration and API values remain integer bytes.
7. **Review & Apply:** Save Configuration creates a draft. Review Configuration
   Changes generates and validates a private candidate and checks published-port
   conflicts. Approve the review and choose Apply & Start FortiLens. Progress,
   individual service state and diagnostics update in the page.

The initial connection is never treated as successful until an actual test
returns success. Deployment may succeed while FortiGate collection is unavailable;
Diagnostics then shows missing evidence. A selected new WAN/source must have a
matching saved discovery. An unchanged existing connection does not require a
working FortiGate to edit general settings. Readiness does not certify usage totals.

### Choose the source evidence explicitly

The existing default receiver remains TCP 1514 / RFC5424. If the sender emits
FortiGate native/default logfmt over UDP, select UDP and format `fortigate`, then
enter its actual destination port. This explicit profile adds the managed
`fortilens-syslog` adapter and retains internal RFC5424/TCP delivery to Alloy.
It does not change the sender or silently select UDP 514. Check port ownership
and the configured FortiGate host's IPv4 source before applying. Read the
[native UDP guide](../operations/SYSLOG_NATIVE_UDP.md) for format bounds, source filtering,
delivery limits and the separate legacy rsyslog alternative.

Review the actual SLA names in the selected VDOM before selecting an Internet
health check. Blank SLA remains readable and supported, but diagnostics asks
for selection rather than borrowing another check's samples. Names such as
`google`, `ping` or `ping1` can report different destination-specific states;
none is selected automatically or established as a public default. Applied
settings determine managed report scope, while saved Grafana dashboards remain
preserved. After changing SLA, explicitly Apply before assessing that scope.

Optional exporter exclusions are a deliberate capability decision, not an API
connectivity repair. The supported selections are `Log/Fortianalyzer/Status`,
`System/Interface/Transceivers` and `System/SensorInfo`. Excluding one means it is
not collected; diagnostics continues to show that omission even when selected
probes succeed. A null/unchanged policy preserves the existing exporter policy;
an explicit empty list removes only these three known names from exclusions.
Other custom include/exclude rules, other targets and the token remain intact;
those retained rules still govern collection. Review explicit selections in the
candidate before applying. Required device,
interface and Virtual WAN/SLA collection is not broadly disabled to hide failures.

FortiGate certificate trust remains the owner's decision. Keep verification
enabled with a trusted certificate or reviewed private CA where possible. An
explicitly acknowledged temporary TLS-off deployment remains a warning, not
authenticated server identity. Certificate pinning needs an independently trusted
reference and coordinated client support; this profile does not establish one.

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
protocol/format and forward-traffic logging. The receiver defaults to TCP 1514 /
RFC5424; native/default UDP uses the explicit profile above. Both bind the
configured host syslog address and need appropriate network/host restrictions.
The managed native IPv4 source filter and a matching device ID do not authenticate
a syslog sender. Review [accounting limits](../architecture/ACCOUNTING.md)
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

Native receiver readiness, recent TCP handoff, recent Loki logs and accounting
delivery are separate checks. No input remains unknown usage. Queue drops and
format/size rejects are cumulative evidence; source-filter rejects alone do not
prove loss of valid source traffic. A successful poll or receiver health check
does not establish complete collection.

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

To change access settings or certificates, stop both management units, preserve
these private directories and registry, and review the changes with a host
administrator. Do not remove or reinitialize the stack. Certificate renewal
updates `/etc/fortilens/tls.crt` and `tls.key` with the existing root/fortilens
ownership and restarts only the web unit.

## Upgrade to a new version

Download and verify the new release (or check out the reviewed source) in a new
directory, then run its installer with `--upgrade`:

```bash
cd fortilens-new
sudo ./install.sh --upgrade
```

The upgrade:

1. Refuses to start while a console deployment or an earlier upgrade is
   unfinished, or when less than 1 GiB is free.
2. Saves the current management source, `installation.json` and any service code
   it will replace under `/var/lib/fortilens/control/upgrades/<time>/`, and tags
   the current accounting and syslog images as `-pre-<time>`.
3. Replaces the allowlisted management source. It builds a new virtual
   environment only when `management/requirements.txt` changed. It then restarts
   both management units and waits for the web health check.
4. Copies new code into services that already exist (accounting and the native
   syslog receiver), rebuilds only those images, recreates the portal that shares
   the accounting image and waits until each one is healthy.

Settings, secrets, Grafana dashboards, Prometheus/Loki data and the accounting
database are not changed. No service is added. If any step fails, the previous
source, environment, service code and images are restored automatically and the
previous version is started again.

To return to the previous version after a completed upgrade:

```bash
sudo ./install.sh --rollback-upgrade
```

A newer version may have upgraded the accounting database format in place.
Rolling back restores code, not data. Check the release notes before rolling
back across a storage change.

See [management architecture and compatibility](../architecture/MANAGEMENT_ARCHITECTURE.md) and
[management verification](../development/MANAGEMENT_VERIFICATION.md).
