# Legacy Loki diagnostics and offline sample auditing

For persistent device/VLAN totals, use the [optional stateful engine](ACCOUNTING_ENGINE.md) and protected reports. Received-log evidence is on the console's **Data quality** page. This page documents the offline sample audit that remains from the legacy Grafana/Loki path; its formulas are not the engine's accounting contract.

## Retired Grafana panels

Releases up to v0.5.0 generated seven Grafana panels that read raw syslog from Loki for a whole period every time they were shown: recorded device usage for Today, Last 7 Days and This Month (panel IDs 1, 41 and 2), their help text (42) and received-record counts for the same three periods (43 to 45). On a real installation the weekly and monthly panels took 30 to 55 seconds each, and 134 to 170 seconds when opened together. The console gateway closes a Grafana request after 30 seconds, so they showed errors, and they slow down further every day of a month. The console's Reports and Data quality pages show the same evidence from the accounting store, so these panels are no longer generated.

A saved dashboard is otherwise never overwritten. On the first configuration apply after the upgrade, FortiLens removes these panels from the running dashboard, but only those that still match what it generated (ID, title and query). Rows they leave empty go with them, and the header text, if it is still the one FortiLens wrote, stops pointing at the removed sections. Every other panel, row, selector, variable and permission stays as it is, and a retired ID whose panel was edited is kept and reported. The dashboard as it was is saved first to a private `grafana/dashboard.before-retired-panels-*.json`, which can be imported in Grafana to restore it. The portable `start` and `import-dashboard` commands do the same for an existing dashboard. A dashboard that no longer exists is not re-created.

`prepare-dashboard-style` drops the retired panels from its local candidate too. The `prepare-usage-quality` command, which added the received-record panels to a saved dashboard, is removed.

## Offline audit of a normalized sample

This optional command reads a local file only. It requires no installation, Grafana credentials, FortiGate access, extra service, or new collector. From `portable/` in a source checkout:

```bash
python3 setup.py usage-audit --input ../examples/usage-audit.sample.json \
  --period month --timezone UTC --vdom root --at 2026-10-28T12:00:00Z
```

Add `--json` for machine-readable output. The example contains synthetic documentation addresses and device values. For your own sample, prepare JSON locally in this format:

```json
{
  "records": [
    {
      "timestamp": "2026-10-28T11:00:00Z",
      "fields": {
        "type": "traffic", "subtype": "forward", "logid": "0000000020",
        "vd": "root", "dstintfrole": "wan", "mastersrcmac": "02:00:00:00:00:01",
        "srcip": "192.0.2.20", "srcname": "demo-device",
        "sentdelta": "100", "rcvddelta": "200"
      }
    }
  ]
}
```

This is a normalized-record format, not a raw Loki export/parser. Byte and identity fields must be strings. Use Loki record timestamps, not a substituted device/session timestamp. Preserve nanosecond precision upstream when reviewing collisions: Python ISO parsing normalizes to microseconds, so replay candidates can include coincident events at finer precision. Optional `sessionid`, `eventtime`, and `devid` help distinguish otherwise identical candidates. If normalization detected a parser failure, set a nonempty `parse_error` string. Never publish client samples or secrets.

Today and month start at the selected local calendar boundary. Week is the rolling previous 168 hours. The sample audit explicitly uses `(start, end]`, matching Loki range aggregation: exact-start records are excluded and exact-end records included. Calendar boundaries can still have record-time attribution limits; an end-only session may include earlier traffic. The audit never invents a packet-time distribution.

The report includes classification counts, sample recorded bytes when eligible input exists, first/last observed scoped record, exact-repeat candidates, and the number of MACs with multiple IP/name combinations. It does not emit raw lines, credentials, MACs, IPs, or names. Its byte reference excludes malformed/oversized signed 64-bit contributions; it is not a byte-for-byte validator of every malformed Loki integer conversion.

Replay candidates match normalized whitelisted fields and normalized timestamps; they are not proven duplicate traffic. Replays with different record timestamps may go undetected. No records are silently removed from the reported sample total. Multiple IP/name combinations are flagged rather than merged into an unverified person.

The report always retains `coverage=UNKNOWN`. Exit code 2 means unavailable evidence or needs review; code 1 means invalid input/tool failure. There is deliberately no coverage-certified exit code 0. File input is bounded to 32 MiB and 100000 records. An explicit UTC or installed IANA timezone is required; `browser` cannot be resolved outside the browser. Linux normally supplies IANA data; Windows configuration-only hosts may need UTC.

## Verification

Regression cases cover explicit zero delta, mixed-direction/end-only fallback, missing/invalid fields, replay candidates, IP/name changes, VDOM scope, no-data behavior, exact range edges, rolling week/calendar month, leap years, timezone boundaries, and crossing sessions.

Retirement is covered by tests on a dashboard shaped like the v0.5.0 one: only matching panels are removed, edited ones and operator panels stay, emptied rows go, the update carries Grafana's `resourceVersion`, a second apply changes nothing and a missing dashboard is not re-created. It was also checked against the pinned Grafana image on the test server.

References: [Loki parsing and label stages](https://grafana.com/docs/loki/latest/query/log_queries/).
