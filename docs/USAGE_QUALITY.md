# Usage quality and offline sample auditing

The dashboard now explains report periods and adds three received-record diagnostic tables: Today, Last 7 Days, and This Month. The diagnostics use the same relative panel periods as the matching usage tables. An absolute Grafana time range overrides relative periods. The dashboard timezone defines calendar boundaries.

The diagnostic row is **collapsed by default**. Expand it when investigating a report; monthly scans can be expensive. It is not an alert or a background collector.

## What the tables tell you

Each table counts received records by classification: both delta fields, end-record cumulative fallback, missing master MAC, invalid/missing byte contributions, non-WAN role, unsupported event/format, non-forward traffic, missing VDOM, and parsing errors.

The selected VDOM applies to well-formed scoped records. Missing-VDOM and parser-error records are explicitly **unscoped** and can include other devices/VDOMs whose identity cannot be established. Other known VDOMs are excluded. FortiGate/SLA/WAN dropdown selections do not filter accounting.

Diagnostics use strict logfmt parsing to flag malformed input. The existing byte formula remains unchanged and uses selective permissive parsing, so a malformed record might be accepted by the byte query while flagged by the diagnostic. These counts are not an independent certification of byte totals. Count categories that are absent are not filled with invented zeros; no results means unavailable evidence.

Received records cannot establish complete traffic coverage. Silence might mean quiet traffic, failed delivery, or expired data. No coverage percentage or green completeness badge is inferred. The tables do not remove duplicates, restore missing logs, merge device identities, or redistribute cross-boundary sessions.

## Existing installations

New `init` installations include the new panels. For an existing installation:

```bash
python3 setup.py prepare-usage-quality
```

This creates a uniquely named local `grafana/dashboard.usage-quality-*.json` candidate from the saved generated dashboard. It preserves the source and never writes to running Grafana. Export/review your live edits before importing the candidate. `start` continues to preserve an existing dashboard. The same limitation applies to saved interface-selection candidates; use your latest reviewed dashboard as the basis for a manual merge when necessary.

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

Today and month start at the selected local calendar boundary. Week is the rolling previous168 hours. The sample audit explicitly uses `(start, end]`, matching Loki range aggregation: exact-start records are excluded and exact-end records included. Calendar boundaries can still have record-time attribution limits; an end-only session may include earlier traffic. The audit never invents a packet-time distribution.

The report includes classification counts, sample recorded bytes when eligible input exists, first/last observed scoped record, exact-repeat candidates, and the number of MACs with multiple IP/name combinations. It does not emit raw lines, credentials, MACs, IPs, or names. Its byte reference excludes malformed/oversized signed64-bit contributions; it is not a byte-for-byte validator of every malformed Loki integer conversion.

Replay candidates match normalized whitelisted fields and normalized timestamps; they are not proven duplicate traffic. Replays with different record timestamps may go undetected. No records are silently removed from the reported sample total. Multiple IP/name combinations are flagged rather than merged into an unverified person.

The report always retains `coverage=UNKNOWN`. Exit code2 means unavailable evidence or needs review; code1 means invalid input/tool failure. There is deliberately no coverage-certified exit code0. File input is bounded to32 MiB and100000 records. An explicit UTC or installed IANA timezone is required; `browser` cannot be resolved outside the browser. Linux normally supplies IANA data; Windows configuration-only hosts may need UTC.

## Verification

Regression cases cover explicit zero delta, mixed-direction/end-only fallback, missing/invalid fields, replay candidates, IP/name changes, VDOM scope, no-data behavior, exact range edges, rolling week/calendar month, leap years, timezone boundaries, and crossing sessions. Existing measurement expressions are preserved.

A separate Linux Docker lab using the pinned Loki3.7.0/Grafana13.2.2 images classified14 synthetic records, matched all expected buckets, and matched the existing usage query to an independent1410-byte reference. The30-template dashboard imported through APIv2 and its quality query executed through Grafana. Real full-period reconciliation remains pending; these fixture checks do not prove a complete live week/month.

References: [Loki parsing and label stages](https://grafana.com/docs/loki/latest/query/log_queries/), [Grafana panel time overrides](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/query-transform-data/).
