# Stateful received-log accounting

The new engine complements Loki; it does not replace the raw store or certify unseen traffic. It keeps an allowlisted SQLite event ledger, session baselines and transactional per-day/device/VLAN summaries. Whole-day historical reports read summaries rather than repeatedly parsing raw logs. This is an optional migration, not an automatic reset of existing Grafana/Loki data.

## Accounting contract

- Outbound internal-origin forward traffic only. Explicit log/interface roles and confirmed Internet interfaces establish direction; internal/inter-VLAN traffic is excluded. Unknown direction remains visible evidence, not guessed Internet usage. For these sessions sent bytes are upload, and received bytes are download.
- Valid directional deltas take priority, including zero. A malformed delta never falls back to a cumulative counter. A closing counter may contribute only its increase from a known baseline, or an end-only session whose supplied duration is contained within one local day. Unsafe end-only counters remain pending; they are not added to the assigned total.
- A persistent device identity requires a usable MAC. `srcmac` is available when `mastersrcmac` is absent. IP/session identities require compatible session-start evidence and remain explicitly unverified. Otherwise each observation has its own identity. IP and hostname alone never establish a persistent physical device.
- Each exact VDOM/source-interface key maps through the existing normalized interface inventory. Missing/conflicting metadata is unknown; IP subnets and interface names never imply VLAN membership. Metadata collected after a historical record cannot establish that record's historical VLAN. Evidence is frozen per event so later inventory refreshes cannot rewrite earlier mappings silently.
- Event IDs include source scope, allowlisted event fields and FortiGate event time where available. A repeated transport delivery cannot add bytes twice. Missing stable event time weakens replay evidence and is flagged. Different indistinguishable real events cannot be perfectly separated without source event identity.
- Late records inside the retained ledger recompute affected sessions and replace prior contributions transactionally. Checkpoints commit with ingestion. Older-than-ledger arrivals are explicit rejections; they cannot recount retained summaries. Retention preserves an open-session baseline without contributing its bytes again.
- Calendar periods use an explicit accounting timezone. Engine intervals are **[start,end)**, with the exact start included and exact end excluded; the legacy Loki tables use their documented different range convention. Week means rolling 168 hours. A timezone/source identity change requires an explicit database rebuild, preventing mixed historical contracts.

## Report reads during collection

File-backed installations use a separate read-only SQLite connection and a short
WAL snapshot for each report. Devices, VLAN aggregates, processing checkpoints
and device histories in one response come from the same committed snapshot.
Readers do not take the ingestion lock or expose unfinished transactions. The
writer retains its existing atomic ingestion/replay/late-arrival behavior.
Legacy databases keep their schema; an explicit verified offline copy can select
the [optional compact representation](COMPACT_ACCOUNTING.md). In-memory fixtures
retain their single-connection compatibility path.

Repeated identical requests can reuse up to four immutable report results of at
most 2 MiB each. Keys include the committed database version, exact period bounds,
filters and history selection; any committed database change invalidates reuse,
including pruning or metadata updates without checkpoint advancement. Runtime
failure/lag evidence is refreshed independently. Existing indexes provide separate
first/last fact lookups; unchanged diagnostics avoid repeated ledger scans.
The last-delivery timestamp is also maintained as optional derived state in the
same ingestion/pruning transaction. The writer rebuilds it from retained delivery
rows on startup, and legacy read-only snapshots fall back to the original query.
This removes a full delivery-ledger scan from first reports after each commit;
it adds no table/index and changes no byte contributions or delivery rows.

The managed console may reuse a report for up to five seconds while navigating
between Device usage and VLAN analytics. This cache lives only in its current
authenticated parent page, is bounded to two 2 MiB entries, and revalidates the
session before a hit. Refresh and filter/period changes request new data. Logout,
revocation and calendar rollover invalidate reuse; displayed bounds remain the
original report bounds. No browser storage or shared proxy cache is introduced.
These optimizations do not establish traffic completeness or eliminate the need
to size the collector for its ingestion rate.

## Evidence limits

Assigned bytes follow source event timestamps. A delta interval crossing midnight cannot be allocated to exact packet times from a single record; it is flagged. Missing/invalid contributions, source binding, counter discontinuities, pending counters, mapping uncertainty and weak identities are visible alongside totals. Pending values must never be added to assigned totals as if independently verified traffic.

No received records means unavailable evidence, not zero usage. A received explicit zero remains zero. Processed query coverage, record presence and complete source traffic are different concepts. `traffic_coverage` stays `UNKNOWN`; a successful poll does not prove all FortiGate traffic was logged and delivered.

Raw ledger retention defaults to 3 days (minimum 1). Week, month and year reports read whole days from daily summaries, so they do not depend on old raw records. Full calendar-day summaries can outlive event retention; expired partial-day custom detail is rejected instead of silently returning an incomplete zero. Summary retention is configurable separately. SQLite uses WAL, full synchronous transactions and a small page cache; storage capacity and late-arrival policy still require sizing for the site's log rate.

References: [Fortinet stats fields](https://docs.fortinet.com/document/fortigate/7.2.6/fortios-log-message-reference/20/20-log-id-traffic-stat), [Fortinet forward-session end fields](https://docs.fortinet.com/document/fortigate/7.6.4/fortios-log-message-reference/13/13-log-id-traffic-end-forward), [Fortinet long-lived session intervals](https://docs.fortinet.com/document/fortigate/7.6.6/administration-guide/285753/log-fields-for-long-live-sessions).
