# Optional native FortiGate UDP adapter

Use this operator-managed Ubuntu adapter when a FortiGate already sends native
`<PRI>date=... time=...` logfmt messages over UDP and changing its sender is outside
the approved scope. It wraps the native body in an RFC5424 envelope for the existing
Alloy receiver. It does not change the firewall or accounting algorithm.

This is an optional host configuration example, not an automatically installed
FortiLens feature. Test the installed rsyslog binary, the pinned Alloy image and
the actual sender before using it. The example alone is not a compatibility or
traffic-completeness claim.

## Choose the correct path

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

FortiLens Settings continue to describe the **Alloy leg**: TCP, port 1514,
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

## Prerequisites and review

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

Review [the rsyslog configuration](../examples/rsyslog/fortilens-syslog.conf) and
[the systemd unit](../examples/rsyslog/fortilens-syslog.service). Replace both
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

## Isolated validation before installation

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

## Validation record

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

## Operator installation

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

## Live acceptance and rollback

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
