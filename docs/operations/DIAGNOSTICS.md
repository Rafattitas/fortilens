# Diagnostics reference

Every check the console's **Diagnostics** page and `python3 setup.py doctor` can
show: what it looks at, what each result means and what to do. All checks are
read-only. Credentials, raw log lines and upstream error bodies are never shown.

For symptoms rather than check names, see [Troubleshooting](TROUBLESHOOTING.md).

## Reading a result

| Status | Shown as | Meaning |
|---|---|---|
| `PASS` | Ready | The check found what it looked for. It never certifies complete traffic capture. |
| `INFO` | Note | A deliberate choice or a fact worth knowing. It does not lower the overall status. |
| `WARN` | Needs review | Something may reduce accuracy or coverage. |
| `FAIL` | Problem | Something required is not working. |
| `UNKNOWN` | Unavailable | The evidence could not be read. This is not a failure and not a zero. |

The overall status is the worst result present: `FAIL`, then `WARN`, then
`UNKNOWN`, otherwise `PASS`. Which checks appear depends on what is enabled:
accounting checks need the accounting service, and native receiver checks need
the native FortiGate UDP profile.

## Services

| Check | What it looks at | When it is not `PASS` |
|---|---|---|
| `service:<name>` | The container state of each service of this installation. | `FAIL`: missing, stopped, restarting or unhealthy; review that service's logs. `WARN`: running but still starting. `UNKNOWN`: Docker could not be read, which does not prove the service is stopped. |
| `grafana` | Grafana's health endpoint and database. | `FAIL`: Grafana answered but its database is not ready, or it rejected the request. `UNKNOWN`: it could not be reached. |
| `prometheus`, `loki` | Each data source, with a small query. | `FAIL`: the data source did not confirm connectivity. `UNKNOWN`: Grafana must be readable first. |

## Collection from the FortiGate

| Check | What it looks at | When it is not `PASS` |
|---|---|---|
| `fortigate_scrape` | Whether Prometheus recently scraped the exporter. | `FAIL`: the latest scrape failed. The cause is not established by this check. |
| `fortigate_api` | The exporter's own FortiGate probes. | `FAIL`: a probe failed; review the collector's errors and the probe policy. `UNKNOWN`: an older success does not cover a newer failed scrape. |
| `sla_metrics` | Health-check samples for the chosen SLA and links. | `WARN`: no SLA is selected, or some chosen links have no samples. A missing link is not classified as down. `UNKNOWN`: no recent samples. |
| `interface_metrics` | Interface samples for the chosen links. | `WARN`: some chosen interfaces have no samples; review names, VDOM and read permissions. |
| `recent_logs` | Log entries Loki received in the last five minutes. | `UNKNOWN`: none were seen. There may be no traffic; review the syslog destination, port, protocol and format. |
| `clock_offset` | FortiGate log times against this server's clock. | `WARN` above one minute, `FAIL` above five. The message says which clock is ahead and by how much. Synchronize both with NTP: `timedatectl` should show the system clock as synchronized, and `sudo chronyc makestep` corrects a large difference at once. `INFO`: logs keep the FortiGate's timestamps, so the difference cannot be measured. `UNKNOWN`: no recent log carried an event time. |
| `metric_query` | Appears only when the metric query itself failed. | The metric checks above are then `UNKNOWN`. |

## FortiGate API

| Check | What it looks at | When it is not `PASS` |
|---|---|---|
| `direct_api` | The last **Test Connection** from Settings, and when it ran. A success stays valid until the connection settings change. | `FAIL`: a test in the last five minutes failed; the message says why (for example authentication rejected or access denied). `INFO`: no test has run yet, or the connection settings changed since the last success. |
| `fortigate_tls` | Whether the FortiGate's certificate is verified. | `WARN`: verification is disabled, so a successful API call does not prove the server's identity. |
| `exporter_probe_policy` | Which optional exporter probes are left out. | `INFO`: the standard optional omissions. `WARN`: custom exclusions. `UNKNOWN`: the policy could not be read. |
| `fortios_version` | The FortiOS version against the versions checked on a real device. | `WARN`: this version has not been checked; reports from logs are expected to work. `UNKNOWN`: the version could not be read. See [FortiOS compatibility](COMPATIBILITY.md). |
| `live_usage_api` | Which FortiGate API parts Live usage received in the expected shape. | `WARN`: a part is unavailable or incomplete. The rest of Live usage keeps working and recorded reports are not affected. |
| `application_ids` | Whether received logs carry Application Control IDs. | `WARN`: none seen yet, so Live usage shows IDs without names. Enable Application Control on the logging policies. |

## Accounting

| Check | What it looks at | When it is not `PASS` |
|---|---|---|
| `accounting_api` | The reporting service's own diagnostics request. | `FAIL`: it rejected or failed the request. `UNKNOWN`: its diagnostics are unavailable. |
| `accounting_poll` | Whether the engine recently processed a poll. | `WARN`: no recent successful poll; review the engine's reachability and storage. |
| `accounting_delivery` | When a usage log last arrived. | `WARN`: the latest delivery is old or future-dated; check `clock_offset`. `UNKNOWN`: no delivery has been observed. Neither means zero usage. |
| `accounting_lag` | How far processing is behind the present. | `WARN`: catching up, for example during the first backfill. |
| `accounting_metadata` | Whether the FortiGate's interface inventory is available. | `WARN`: not established; review read permissions and VDOM. Segments are never inferred from subnets. |
| `accounting_inventory_permissions` | Appears when the inventory API rejected the request. | `FAIL`: review the API account and its VDOM scope. |
| `accounting_source` | Whether usage is bound to one FortiGate serial number. | `WARN`: unbound, so logs from another FortiGate sent to this listener would be counted too. Bind the serial in Settings. Leave it unbound for an HA cluster whose members log with different serials. |
| `accounting_rejections` | Records rejected as malformed or too late. | `WARN`: some were rejected. The count is historical, not proof of a current fault. |
| `accounting_delivery_timestamps` | Whether Loki stores the time of receipt. | `WARN`: not confirmed, so late records may be missed. |

## Native FortiGate UDP receiver

Shown only with the native UDP profile. See [Native FortiGate UDP reception](SYSLOG_NATIVE_UDP.md).

| Check | What it looks at | When it is not `PASS` |
|---|---|---|
| `native_receiver` | The UDP listener, the forwarding worker and the connection to Alloy. | `FAIL`: one of them is not ready. `UNKNOWN`: its diagnostics could not be read. |
| `native_handoff` | When datagrams were last handed to Alloy. | `WARN`: the last handoff is old or future-dated. `UNKNOWN`: none observed; review the sender address, UDP destination and format. |
| `native_source_filter` | Appears when datagrams from an unexpected source were rejected. | `WARN`: review the sender path, including Docker's UDP proxy or NAT. Do not widen the filter to a shared gateway. |
| `native_queue_loss` | Appears when the receiver's queue was full. | `WARN`: a cumulative count of dropped datagrams; review the input rate and collector availability. |
| `native_input_rejections` | Appears when received input was rejected. | `WARN`: a cumulative count; review the sender's format. |

## Running the checks from the command line

```bash
python3 setup.py doctor --json --output /path/to/installation
```

On a web-managed installation the console's Diagnostics page is the reference.
There, `direct_api` reports the last Test Connection. On the command line it is a
live read of `system/status` and runs only with `--api-check`.
