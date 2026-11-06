# FortiOS compatibility

FortiLens has been checked on a real FortiGate running **FortiOS 7.2.14**. It
should work on other versions, but none of them has been checked on a real
device yet. This page explains what each part depends on and how FortiLens
shows a version that answers differently.

## What each part depends on

| Part | Source | Sensitivity to the FortiOS version |
|---|---|---|
| Recorded usage, reports, applications | Traffic logs over syslog (`srcip`, `srcmac`, `sentbyte`, `rcvdbyte`, `app`, `appid`, …) | Low. These fields have been in FortiOS traffic logs for many releases. |
| Interface and VLAN inventory | Read-only REST API | Medium. Field names can differ between versions. |
| Live usage | `monitor/fortiview/statistics` and `monitor/system/interface` | Highest. FortiView changes between releases. |

## How a different version is handled

The FortiOS version and build are read from the FortiGate's own API replies.
**Diagnostics** shows them with one of these levels:

| Level | Meaning |
|---|---|
| Tested | This exact version has been checked on a real device. |
| Same branch | Another patch release of the same branch has been checked, for example 7.2.9 when 7.2.14 has. |
| Untested | No release of this branch has been checked. Diagnostics shows a warning. |
| Unknown | The version could not be read. The check shows the reason, such as `http_401` or `timeout`. |

Live usage reads each part separately, so a missing part does not stop the others:

- **FortiView unavailable** (for example HTTP 404). Link totals still come from
  WAN interface counters. The page and Diagnostics say why device rows are missing.
- **Fields missing.** If FortiView rows lack `dstintf`, `srcaddr`, `rx_bandwidth`
  or `tx_bandwidth`, the missing names are listed instead of being shown as zero traffic.
- **Sort ignored.** FortiOS returns rows in session order, without an error,
  when it does not support a sort. FortiLens checks the order itself, sorts the
  rows, and warns that on a busy site the listed devices may not be the busiest.
- **Application statistics or interface counters unavailable.** The rest of the
  page keeps working and says which part is missing.
- **No Application Control IDs in logs.** Diagnostics warns that Live usage will
  show application IDs without names.

Live usage reports an error only when neither FortiView nor the interface
counters can be read.

To limit load, Diagnostics reads the version and checks Live usage at most once
an hour. After a failed read it waits five minutes before trying again. Both
reads use the existing read-only API token.

## Reporting a version

If you run FortiLens on another FortiOS version, open a **FortiOS compatibility**
issue with:

- the version and build from Diagnostics;
- the `fortios_version` and `live_usage_api` check results;
- what looked wrong.

Remove serial numbers, addresses and hostnames before posting.
