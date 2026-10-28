# Device usage: choose the accounting path

FortiLens 0.2 provides an optional persistent received-log engine and protected reporting portal. Use it for directional device usage, fixed day/week/month periods, VLAN totals and retained history. Read the [accounting contract](ACCOUNTING_ENGINE.md), [installation guide](../portable/README.md), and [safe upgrade procedure](UPGRADE.md).

| Path | Purpose | Limits |
|---|---|---|
| Protected reports / SQLite | Stateful byte accounting, identity handling, replay idempotency, classifications and history. | Received evidence only; pending counters, weak identities, old metadata, missing traffic and cross-boundary intervals remain explicit. |
| Existing Grafana / Loki tables | Legacy raw-log diagnostics and comparisons. | Original MAC/IP/name grouping and closing cumulative fallback; no stateful deduplication. Absolute Grafana ranges override relative periods. |
| Offline usage-audit | Sanitized sample comparison and repeat/identity review. | A sample is not a complete ledger. No live collection or deployment. |

Portal periods use an explicit accounting timezone, **[start,end)** boundaries, rolling 168 hours for the week and calendar boundaries for day/month. Fixed selections use the server's current time independently of Grafana. GB means decimal 1,000,000,000 bytes; API totals retain integer bytes and the UI rounds display units.

MAC identifies an observed endpoint, not an authenticated person. Without usable MAC evidence, session/observation identities stay unverified; an IP or hostname alone never merges devices permanently. Router/proxy MACs and forged source fields require site-specific review.

Source-wide classifications distinguish Internet, internal, unresolved, unsupported and unscoped records. They cover the source/period independently of device/search/VLAN filters. Malformed input and too-late rejections are separate diagnostic counters. Unknown traffic coverage never becomes a green 100% badge.

Legacy Loki defaults retain 60 days, allow 32-day queries and budget 3 minutes for execution, 210 seconds for delivery and 240 seconds in the datasource. These controls do not restore missing logs or guarantee scan speed. Persistent summaries have separate configurable retention.

Validate a controlled traffic window against independent byte totals, then review source identity, VLAN metadata, logging/clocks, duplicate/late delivery and missing periods. Compare Internet-only logs to a compatible reference: physical WAN counters may include unrelated traffic. Full-month or per-packet accuracy cannot be claimed from synthetic tests or an empty successful query.
