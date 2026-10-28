# Troubleshooting

Run `python3 setup.py doctor --json` from the package directory, with `--output /path/to/installation` when needed. It is read-only. Credentials, raw logs and upstream error bodies are excluded. Review the specific check and suggested action; a successful check does not certify end-to-end traffic completeness.

| Symptom | Interpretation and next check |
|---|---|
| FortiGate probe fails | Review API reachability, TLS, trusted hosts, permissions and VDOM scope. Failure alone does not prove an invalid token. `doctor --api-check` adds one explicit GET to `system/status`. |
| API 401 / 403 | Authentication or read access was rejected. Check the existing API account and scope. A reporting/Grafana login error is a separate authentication boundary. |
| WAN shows NO DATA | Collection is missing/stale or SLA evidence is absent. This is not a DOWN state. Review the configured SLA and original sample timestamps. |
| Route UNKNOWN | The read-only IPv4 route lookup is unsupported, denied or did not identify a confirmed interface. An UP link or traffic does not prove an active route for every SD-WAN flow. |
| Reports cannot sign in | Use `admin` and the local `secrets/usage_admin_password`, not the Grafana password. Keep the service on loopback or use an authenticated HTTPS reverse proxy. |
| Empty report | No eligible received Internet-accounting evidence for that scope/time range. It is not proof of zero traffic. Check source binding, VDOM, roles, clocks, logging and receipt timestamps. |
| VLAN UNKNOWN | Missing/conflicting metadata, unsupported interface type, or inventory collected after the historical event. Review exact VDOM/interface/parent/VLAN-ID; never infer it from an IP subnet. |
| Many IP_SESSION / OBSERVATION rows | MAC evidence is unavailable. These are unverified session/observation identities, not one permanent device per IP or a verified user count. |
| Pending counter candidates | No safe cumulative baseline or period allocation exists. Do not add pending values to assigned totals; candidates can overlap. |
| Processing lag / initial backfill | The engine scans small bounded windows. Wait for catch-up and inspect current error/storage. Repeated overlaps/restarts are idempotent when event identity is available. |
| `accounting_storage_limit` | Ingestion stops with visible lag instead of silently deleting data. Increase `accounting_max_bytes` and disk capacity after sizing; retain/recover the SQLite volume. Do not delete it to clear the error. |
| Loki paging/record budget | A dense timestamp/window exceeded a bound. Reduce `accounting_step_seconds`; preserve the checkpoint and review source rate/retention. An unsplittable timestamp requires review, not acceptance of a partial batch. |
| Partial-day detail expired | Request whole retained calendar days or increase ledger retention before collection. Whole-day/month summaries may outlive raw detail; expired slices are not filled with zero. |
| Old-source timestamp late arrivals | Newly enabled accounting uses receipt timestamps in Loki. Existing installations need the reviewed migration. Old history is not rewritten. Arrivals beyond ledger retention are counted as rejected. |
| Legacy Grafana month query timeout | Use the protected summary-based reports for fixed-period answers. Legacy Loki scans still have volume-dependent costs; timeouts and missing intervals do not mean zero usage. |
| Prometheus metric row cap | `fortilens_accounting_metrics_truncated_rows` exposes truncation. The reporting API/portal is the full report; don't use a truncated metric table as an all-device total. |

SQLite keeps normalized identity/usage metadata and allowlisted event fields; raw logs remain in Loki. Protect both stores and backups. Defaults target a single writer on a small Linux host; measure actual log rate, disk growth and memory before production sizing. Never change accounting timezone/source identity in an existing database: use a separately reviewed rebuild and retain the original.

For isolated development QA, `python3 tests/preview_reports.py` serves a labelled synthetic preview on loopback port 18767. It is not a sample production deployment and is excluded from the standalone bundle. Automated Docker acceptance uses `python3 tests/integration_stack.py` on Linux with disposable project-owned resources only.
