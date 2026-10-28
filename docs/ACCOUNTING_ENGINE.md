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

## Evidence limits

Assigned bytes follow source event timestamps. A delta interval crossing midnight cannot be allocated to exact packet times from a single record; it is flagged. Missing/invalid contributions, source binding, counter discontinuities, pending counters, mapping uncertainty and weak identities are visible alongside totals. Pending values must never be added to assigned totals as if independently verified traffic.

No received records means unavailable evidence, not zero usage. A received explicit zero remains zero. Processed query coverage, record presence and complete source traffic are different concepts. `traffic_coverage` stays `UNKNOWN`; a successful poll does not prove all FortiGate traffic was logged and delivered.

The default ledger must cover the rolling week. Full calendar-day summaries can outlive event retention; expired partial-day custom detail is rejected instead of silently returning an incomplete zero. Summary retention is configurable separately. SQLite uses WAL, full synchronous transactions and a small page cache; storage capacity and late-arrival policy still require sizing for the site's log rate.

References: [Fortinet stats fields](https://docs.fortinet.com/document/fortigate/7.2.6/fortios-log-message-reference/20/20-log-id-traffic-stat), [Fortinet forward-session end fields](https://docs.fortinet.com/document/fortigate/7.6.4/fortios-log-message-reference/13/13-log-id-traffic-end-forward), [Fortinet long-lived session intervals](https://docs.fortinet.com/document/fortigate/7.6.6/administration-guide/285753/log-fields-for-long-live-sessions).
