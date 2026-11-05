# Native FortiGate UDP reception

Current source supports a managed `fortigate` syslog profile for a FortiGate
already sending native `<PRI>date=... time=...` logfmt datagrams. Select this
profile explicitly when the sender's actual format is native/default. The
existing default remains TCP 1514 / RFC5424; existing installations are not
converted automatically. FortiLens does not change the firewall's sender.

## Managed native UDP profile

### Source-preserving Linux host reception

On rootful Linux Docker Engine, select **Native UDP reception → Linux host
(preserve sender address)** when Docker's published UDP path replaces the
sender with a shared gateway. This is an explicit alternative to the existing
bridge profile, not an automatic migration. No rsyslog package, host unit,
Docker daemon change or firewall-policy change is required.

```json
{
  "fortigate_host": "192.0.2.1",
  "syslog_protocol": "udp",
  "syslog_format": "fortigate",
  "syslog_network": "host",
  "syslog_port": 514
}
```

Use the actual FortiGate IPv4 address, not the example address or a Docker gateway.
Host mode requires a literal IPv4 source because the container no longer has
Compose DNS. The exact same source filter and native message validation apply.
Docker Desktop and rootless Docker are unsupported for this profile and are
rejected by managed port validation and portable `start`.

The receiver alone uses `network_mode: host`; other services retain their
existing network isolation. Docker does not publish/NAT its UDP socket. The
configured host port is bound directly, using the existing `syslog_bind`
address. For low ports, a bind-only bootstrap starts as root with only
`NET_BIND_SERVICE`, `SETUID` and `SETGID`. After binding UDP and private HTTP,
it permanently drops supplementary groups and UID/GID to 10001 before starting
any receiving, forwarding or HTTP threads. Startup fails unless effective,
permitted, inheritable and ambient capabilities are all zero and
`no-new-privileges` is active. It has no
raw-socket capability, Docker socket, API token or persistent data volume.
Host networking nevertheless shares the host network namespace: select it
deliberately after reviewing this reduced network isolation.

For UDP port **P**, Alloy is published only at **127.0.0.1:P/TCP**, and receiver
health/metrics bind only **127.0.0.1:(P+1)/TCP**. Both TCP ports must be free;
P cannot be 65535. For UDP 514 these are TCP 514 and TCP 515. Management checks
these bindings before applying and accesses health through the fixed registered
receiver identity. Existing service readiness, recent handoff, Loki and accounting
checks remain separate. The bridge-only Prometheus receiver scrape is omitted
in host mode because its health port is private to the host; counters remain
available through local `/metrics` and health diagnostics. No LAN metrics port
is opened to work around that restriction.

Fresh installations render this service through the normal setup/apply path.
Existing managed installations can review/apply a transition between bridge,
host and RFC profiles without changing Compose project, volumes or dashboards.
Custom receiver definitions and unexpected handoff bindings require review.
Configuration rollback uses the existing candidate recovery path. A separately
installed rsyslog listener must be coordinated by its operator before applying;
FortiLens fails on the port conflict rather than stopping an unrelated service.

The cause is a transport boundary: once NAT/proxying has replaced the UDP source,
the receiver cannot recover or authenticate the original sender. Docker's
[host-network documentation](https://docs.docker.com/engine/network/drivers/host/)
explains why host mode bypasses published port mappings. This profile avoids
that boundary instead of allowing a shared gateway. It cannot correct upstream
NAT, a different FortiGate source interface, spoofing or missing historical logs.
Acceptance still requires natural sender evidence on the intended installation.

### Existing bridge reception

In **Monitoring & Syslog**, choose UDP and the `fortigate` native format, then
set the host port to the actual destination used by the sender. Native format
requires UDP; selecting it does not silently change the configured port. For
example, a sender using UDP 514 needs host port 514 after checking that this
binding is free. Do not displace another listener.

The corresponding portable configuration fields are:

```json
{
  "syslog_protocol": "udp",
  "syslog_format": "fortigate",
  "syslog_port": 514
}
```

This is a fragment to merge into a reviewed configuration for a **new**
installation, not a complete configuration or an upgrade command. Do not run
`init` over an existing stack. For an existing registered installation, save a
draft, inspect the candidate and port conflicts, then explicitly Apply. Keep
the exact Compose project, volumes, secrets, accounting source/timezone and
saved dashboards. Changes can create a collection gap.

```text
FortiGate native/default UDP, configured host port
    -> fortilens-syslog UDP 1514, IPv4 source filter
    -> alloy:1514 TCP, octet-counted RFC5424
    -> existing Alloy -> Loki -> existing accounting worker
```

The profile adds one fixed Compose service, `fortilens-syslog`, and an internal
Prometheus scrape. Alloy's syslog listener remains TCP/RFC5424 inside the stack;
its direct host syslog binding is replaced by the receiver's UDP binding. The
service runs as UID/GID 10001 with a read-only root filesystem, dropped
capabilities and a read-only site configuration. Its build context includes
only its standard-library Python receiver and pinned Dockerfile. It has no API
token, Docker socket or writable monitoring data mount. Internal health/metrics
port 8080 is not published to the host. The Ubuntu bootstrap still installs
management only; the receiver is built/started with an approved monitoring
configuration, not during bootstrap.

### Accepted input and limits

The adapter accepts a canonical priority prefix in 0–191 followed by
`date=YYYY-MM-DD time=HH:MM:SS` and the native body. It removes the priority
prefix and trailing CR/LF/NUL delimiters, then preserves the UTF-8 body without
reserializing fields. Embedded control characters, invalid UTF-8, other formats
and datagrams over 64,000 bytes are rejected. Source `eventtime`, byte counters,
quoted values and backslashes remain in the body. The new RFC5424 envelope uses
host receipt time; existing accounting event-time and late-data rules remain
unchanged. This adapter does not correct a FortiGate clock.

The source allowlist resolves only the configured `fortigate_host` to IPv4
addresses at receiver startup. IPv6-only sources are unsupported. If a NAT,
relay or another FortiGate interface produces a different observed source,
review the actual path before applying this profile; do not broaden the filter
or change the API host merely to bypass a rejection. A source address and raw
`devid` are **not sender authentication**; UDP can be spoofed. Existing accounting
source identity remains a separate contract.

Some Docker UDP publishing paths use a userland proxy or source NAT, so the
container sees a shared bridge gateway even when the host receives the correct
FortiGate address. Check bounded packet headers on both sides before acceptance.
The managed receiver cannot recover the original sender from those datagrams.
Do not add the shared gateway to its allowlist: that would also accept unrelated
forwarded senders. Use the Linux host profile above or the host rsyslog alternative below
when the host path preserves the original address. Keep its narrow source filter,
and configure FortiLens for the relay's RFC5424/TCP output. The helper does not
manage or independently verify that external host service.

The queue holds at most 256 messages in memory, with one forwarding worker and
bounded TCP reconnect backoff. Full queues drop input; a process restart can
lose queued records and UDP can lose packets before reception. A failed TCP
send can also have an uncertain delivery outcome. No raw log files or persistent
disk queue are created. This is best-effort collection, not lossless delivery or
a full-capture guarantee. Existing replay/accounting rules are not replaced.

### Diagnostics and acceptance

Management Diagnostics uses the fixed receiver health endpoint only for the
native profile. `native_receiver` requires the UDP listener, worker and Alloy
TCP connection to be ready. `native_handoff` separately reports a recent TCP
send, which does **not** establish parsing, Loki storage or accounting
eligibility. Without a health adapter, the standalone doctor reports actionable
unknown receiver evidence rather than inventing delivery. Internal metrics use
the `fortilens_native_syslog_` prefix and the `fortilens-syslog` Prometheus job.

Queue-drop and non-source validation-rejection counters are cumulative warnings;
source-filter rejections alone do not prove loss of authorized input. A healthy
receiver without received datagrams has unknown handoff evidence. `recent_logs`
and `accounting_delivery` remain separate observations, and successful polling
does not prove collection or complete usage.

Before live use, run the isolated cases in [QA](../development/QA.md): allowed/disallowed
senders, body preservation, boundaries, reconnect/backpressure, Alloy parsing,
Loki storage and independently known synthetic accounting totals. Never send
fixtures to a production listener. During an authorized live change, use bounded
natural-traffic observations and preserve private backups. If acceptance fails,
review and restore the previous registered configuration using the existing
configuration-only rollback; do not delete data or silently change the firewall.
Coordinate rollback with any separately installed host adapter to avoid binding
conflicts. Missing historical records cannot be reconstructed.

## Legacy operator-managed rsyslog alternative

Use this operator-managed Ubuntu adapter when a FortiGate already sends native
`<PRI>date=... time=...` logfmt messages over UDP and changing its sender is outside
the approved scope. It wraps the native body in an RFC5424 envelope for the existing
Alloy receiver. It does not change the firewall or accounting algorithm.

This remains a separate optional host configuration example. It is not installed
or controlled by the managed native profile. Test the installed rsyslog binary, the pinned Alloy image and
the actual sender before using it. The example alone is not a compatibility or
traffic-completeness claim.

### Choose the correct path

FortiGate's native/default format is not necessarily RFC3164. Selecting
`rfc3164` in Alloy does not establish that it can parse a native logfmt datagram.
First inspect protocol/format evidence without storing or publishing raw traffic.
Use the normal direct receiver when the sender already emits valid RFC5424.

This adapter keeps the two receiver legs distinct:

```text
FortiGate native/default UDP 514
    -> dedicated host rsyslog adapter
    -> 127.0.0.1 TCP 1514, RFC5424
    -> existing Alloy -> Loki -> existing accounting worker
```

For this legacy alternative, FortiLens Settings describe the **Alloy leg**: TCP, port 1514,
RFC5424. The external UDP 514 listener belongs to this separate host service.
Changing those management settings later requires reviewing the adapter's
forwarding target too. Do not silently change managed settings to UDP/RFC3164
while forwarding RFC5424/TCP into Alloy.

The management helper does not install, start, stop or monitor this host adapter.
Its Docker service checks cover Alloy and the existing stack. Check the adapter's
systemd status and journal separately when tracing a delivery gap; healthy
containers do not establish that the external UDP leg is working.

The adapter uses rsyslog's
[rawmsg-after-pri property](https://docs.rsyslog.com/doc/reference/properties/message-rawmsg-after-pri.html)
to retain the native body after removing its priority prefix. A custom envelope
uses the host receipt time. Do not substitute `%msg%` or an ordinary forwarding
template: a parser can interpret native fields as a syslog header or tag before
those properties are generated. The existing Alloy receipt-time policy remains
unchanged; FortiGate `eventtime` remains in the body for accounting.

This avoids enabling Alloy's experimental `raw` format. The
[Alloy v1.19.2 reference](https://github.com/grafana/alloy/blob/v1.19.2/docs/sources/reference/components/loki/loki.source.syslog.md)
marks that format experimental and requires an experimental stability flag.

### Prerequisites and review

- Ubuntu with rsyslog already installed, `/usr/sbin/rsyslogd`, a `syslog` user and
  group, and systemd ambient-capability support. This guide does not install
  packages, change the system logger or create user accounts.
- The installed AppArmor profile permits reading
  `/etc/rsyslog.d/fortilens/receiver.conf`, and the system logger includes only
  `/etc/rsyslog.d/*.conf`, without a recursive include that would load this nested
  file. Verify both actual configurations; do not disable AppArmor or install
  this example where the system logger would also load it.
- An existing registered FortiLens installation with a verified local TCP 1514
  Alloy listener using RFC5424 and the existing Loki/accounting pipeline.
- UDP 514 free on the intended host address. Inspect actual socket ownership;
  do not displace another service or add a competing Docker UDP binding.
- Verified sender and destination addresses. The examples use documentation-only
  `192.0.2.1` for the FortiGate and `192.0.2.10` for the Ubuntu host.
- Authorization to add this host service, plus a private configuration backup and
  a bounded verification/rollback window. Existing firewall/network controls
  remain the operator's responsibility; these files do not alter them.

Review [the rsyslog configuration](../../examples/rsyslog/fortilens-syslog.conf) and
[the systemd unit](../../examples/rsyslog/fortilens-syslog.service). Replace both
example IP addresses in a private copy. Keep the source filter as narrow as the
observed sender permits; do not broaden it to an entire subnet to hide a mismatch.

The dedicated process reads only its own configuration. It loads no system
rsyslog rules, opens no local log-input sockets, and defines no raw file output or
disk queue. Its main queue and forwarding queue each hold at most 1,000 queued
messages, plus worker/in-flight overhead. Full queues discard input. Undelivered
memory backlog can be lost on process termination; UDP can also lose packets before
the adapter receives them. This is bounded best-effort collection, not lossless
delivery. See [rsyslog queue behavior](https://docs.rsyslog.com/doc/concepts/queues.html).

The service runs as `syslog` with only `CAP_NET_BIND_SERVICE` for UDP 514. Its
configuration must remain root-owned and unwritable by that account. It shares
the host's existing `syslog` identity; it does not create a new security principal.
The foreground process uses `-iNONE` to disable PID-file creation under systemd;
it needs no persistent writable directory. It does not grant FortiLens web/helper
processes additional permissions.

### Isolated validation before installation

Run a disposable test with synthetic records and separate test bindings. Use
high ports for ordinary fixtures, then verify privileged-port capability in a
separate sandbox where the test listener cannot receive production traffic.
Use the installed rsyslog version and the actual pinned Alloy image. Do not send
synthetic traffic to production ports, Loki or accounting storage.

Verify all of the following:

1. `rsyslogd -N1 -f /etc/rsyslog.d/fortilens/test/receiver.conf` accepts the
   isolated test configuration under the installed AppArmor profile. Validate
   `-iNONE` support with the installed binary too.
2. A native datagram becomes one octet-counted RFC5424 message. After the
   envelope, its logfmt body retains `date`, `time`, `devid`, `eventtime`, byte
   counters, quoted values and backslashes. Test Unicode and control characters
   separately: rsyslog normally escapes control characters on reception.
3. The pinned Alloy image parses the forwarded message with no parsing error,
   and the existing accounting parser reads the body without a priority/header
   prefix being mistaken for a logfmt key.
4. A datagram from a disallowed source does not reach the forwarding target.
5. A temporarily unavailable test target does not create raw files or unbounded
   queues; restoring it resumes forwarding. Do not simulate this against live
   monitoring.
6. The unit passes `systemd-analyze verify` and starts under the intended
   nonroot user and sandbox. Root-only execution does not validate capabilities.

Rsyslog's default message limit is 8 KiB; the RFC5424 envelope adds bytes before
Alloy's own limit applies. Inspect maximum observed sizes and test their boundary
in isolation. Do not claim all records are preserved from a short sample.

### Legacy validation record

An isolated check on 2026-11-02 used installed rsyslog **8.2512.0** and the pinned
Alloy **1.19.2** image. The example configuration was used with test-address
substitutions, and the Alloy container ran with Docker `--network none`. A
dedicated transient systemd unit used the `syslog` user/group and the example's
capability and sandbox restrictions while AppArmor remained enforced.

The check established that the nonroot process could bind test UDP port 514,
wrap a synthetic native message, and deliver it to the isolated Alloy parser
without a parsing error. The parsed body retained the tested `date`, `eventtime`
and UTF-8 content. A separate disallowed loopback sender did not forward.

This validates the tested adapter/parser path and restricted service startup. It
does not establish live sender delivery, all message-size/escaping cases, queue
overload or crash durability, independent byte totals, traffic completeness, or
compatibility with every Ubuntu/FortiOS version. Repeat acceptance on the intended
host; production validation is a separate, private deployment record.

### Operator installation

The following commands assume the reviewed private copy has replaced both example
addresses and the prerequisite checks passed. Run from a reviewed source checkout;
do not overwrite existing adapter files without first backing them up privately.

```bash
sudo install -d -o root -g syslog -m 0750 /etc/rsyslog.d/fortilens
sudo install -o root -g syslog -m 0640 /private/reviewed/receiver.conf /etc/rsyslog.d/fortilens/receiver.conf
sudo install -o root -g root -m 0644 examples/rsyslog/fortilens-syslog.service /etc/systemd/system/fortilens-syslog.service
sudo /usr/sbin/rsyslogd -N1 -f /etc/rsyslog.d/fortilens/receiver.conf
sudo systemd-analyze verify /etc/systemd/system/fortilens-syslog.service
sudo systemctl daemon-reload
sudo systemctl enable --now fortilens-syslog.service
```

Do not reload or restart the existing `rsyslog.service`. This is a separate
foreground process and unit with no PID file. The nested configuration path is
intentional: it can remain within Ubuntu's rsyslog AppArmor read policy without
being loaded by the system logger's flat `*.conf` include. Confirm that behavior
on the actual host. After editing this adapter's configuration, validate it and
restart only `fortilens-syslog.service`; HUP is not a configuration reload.

### Live acceptance and rollback

Record the actual source revision and configuration hashes privately. Check
service/socket ownership, source filtering, Alloy entry/parser counters, recent
Loki aggregate counts and accounting diagnostics over a bounded natural-traffic
window. Preserve tokens, raw payloads and internal deployment details outside Git.

Distinguish each observation: UDP arrival, successful RFC5424 parsing, Loki
storage, engine delivery and eligible Internet usage are different claims.
Successful accounting polling alone does not establish received traffic.
Confirm that unrelated service identities, named volumes, dashboards, existing
credentials, database schema and source/timezone contract remain unchanged.

Source-IP filtering is not sender authentication: UDP source addresses can be
spoofed. The raw `devid` remains available, but the adapter does not establish an
accounting source binding or authorize changing one. The extra hop makes Alloy's
connection peer the relay rather than the FortiGate; do not treat that peer as a
new device identity. This does not resolve FortiGate API TLS trust limitations.

The adapter does not correct a FortiGate clock. Receipt-time Loki indexing can
discover newly delivered records while source `eventtime` still allocates usage
to a different reporting period or fails existing late-data limits. Missing
historical logs cannot be reconstructed, and traffic completeness remains unknown.

If acceptance fails, stop and disable only the new adapter:

```bash
sudo systemctl disable --now fortilens-syslog.service
```

Restore any prior adapter configuration from the private backup if one existed.
Retain failed evidence for review. Do not reset Loki/accounting, delete volumes,
modify the firewall, or change the system logger to make the check pass. No
FortiLens configuration or data rollback is needed when this adapter was the only
change. Rollback stops the newly added delivery path; it does not recover logs
that were never collected.
