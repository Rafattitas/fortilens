# Console guide

What each page of the FortiLens console shows, where its numbers come from and
how far to trust them. To install or configure, see [Web Setup](WEB_SETUP.md).
For what each check means, see the [Diagnostics reference](../operations/DIAGNOSTICS.md).

## Which page answers which question

| Question | Page |
|---|---|
| Is everything collecting right now? | Overview, then Diagnostics |
| Who is using the Internet this minute? | Live usage |
| Who used the most in the last five minutes, today, this week or this month? | Devices, with the period you need |
| Which network segment uses the most? | Segments |
| Which applications or websites? | Applications, Websites |
| Is an Internet link down or degraded? | Internet links |
| Firewall CPU, memory, sessions and interface traffic over time | Firewall dashboard |
| Why does a number look low or is missing? | Data quality |
| Why is a page unavailable or slow? | Diagnostics |

## Two kinds of numbers

The console shows two different things and labels them apart.

- **Live estimates** (the *Right now* group) come from the FortiGate's realtime
  session statistics and interface counters. They show what is happening now and
  are not stored.
- **Recorded usage** (the *Recorded usage* group) comes from the traffic logs the
  FortiGate sends. FortiLens keeps daily totals per device and the raw records of
  the last few days. This is the more complete source, but a long session reports
  its bytes every couple of minutes, so it trails the present slightly.

FortiLens only counts sessions the FortiGate logs. Traffic through a firewall
policy that does not log all sessions is missing from recorded usage. An empty
table means no evidence was received; it is never shown as zero usage.

## Overview

One screen for "is anything wrong?": the state of the Internet links, when logs
last arrived, how far accounting is behind, and how many services run. Each item
links to the page with the detail.

## Right now

### Live usage

Who is using the Internet this minute. It refreshes every 10 seconds.

- **Link totals** come from the WAN interface counters. They include all traffic
  on the link, also traffic no device owns, such as FortiGate updates and probes.
- **Device, application and segment figures** are the FortiGate's realtime
  estimates for up to 500 sources. They are estimates, not recorded totals.
- Under each total, the page shows how much of it comes from the listed devices.

Limits to know:

- The FortiGate computes the per-device estimates itself. On a FortiGate that
  offloads sessions to a network processor, they can lag and can miss a busy
  device. This was observed on FortiOS 7.2.14 with an NP6XLite processor. When
  the listed devices explain little of the link total, use Devices with
  *Recent 5 minutes* instead.
- `live_usage_api` in Diagnostics says which FortiGate API parts responded.

### Internet links

One row per Internet link:

- **Internet state** comes from the SD-WAN health check (SLA) chosen in Settings.
  A link that is not a member of that health check has no SLA evidence.
- **Physical link** is the interface state. A link can be physically up while its
  Internet state is down.
- **Latency, jitter and loss** are the health check's own measurements. A health
  check that uses an HTTP probe measures a web server's response, which is
  normally far higher than a ping. Compare a link with its own usual value.
- **Observed up** is the share of observed time the health check reported up.
  Time without samples is not counted as up or down.

`NO DATA` means samples are missing or stale. It does not mean the link is down.

### Firewall dashboard

The native Grafana dashboard, embedded: collection state, firewall CPU, memory
and sessions, quality per link, bandwidth and interface traffic, availability,
30 days of performance history and the monitoring host's resources. Selectors at
the top choose the FortiGate, VDOM, health check and links.

## Recorded usage

All pages in this group share the period buttons: **Today**, **Last 7 days**
(today plus the six days before), **This month**, **This year**, **Recent 5
minutes** and **Custom range**. Days follow the accounting timezone shown on the
page.

Whole days are read from the daily totals and answer at once. *Recent 5 minutes*
and custom ranges that start or end inside a day read the raw records, which are
kept for three days by default (`accounting_ledger_days`). A long range inside a
day can be refused with a request to choose whole days.

### Devices

Upload and download per device, largest first. Search by IP address, MAC address
or hostname, or filter by segment. Selecting a device opens its daily and monthly
history and the identity evidence behind its name.

A device is identified by its MAC address when the FortiGate reports one. Names
come from the FortiGate and are not authenticated user accounts. An IP or name
change can split a device into several rows.

### Segments

Usage per VLAN or interface, from the FortiGate's own interface inventory.
Segments are never guessed from an IP subnet. "VLAN not verified" rows are records
received before FortiLens confirmed the interface mapping; they belong to the
interface shown. VLANs the FortiGate reports but that sent no records are listed
so a missing segment is visible.

### Applications

Usage by application and category, and the devices behind each one. It needs
Application Control on the logging policies. Traffic the FortiGate only saw as a
protocol (for example SSL or QUIC) is listed as such rather than as an
application. Records from before labels were kept are shown apart as untracked.

### Websites

Usage by website and the devices that visited it. It is off until enabled in
Settings, and totals are kept per day. See [Website usage](../operations/WEBSITES.md)
for what is stored and for how long.

### Reports

A summary for the chosen period: the largest consumers and the usage per segment,
ready to read or share.

## System

- **Status** lists the monitoring services, storage use and whether the saved
  configuration is applied.
- **Data quality** follows a record from received to processed to classified and
  counts what was excluded and why. Use it when a figure looks low.
- **Diagnostics** runs read-only checks and says what to do about each finding.
  See the [Diagnostics reference](../operations/DIAGNOSTICS.md).
- **Settings** changes the installation. Changes are saved as a revision,
  reviewed, then applied.

## When someone says the Internet is slow

1. **Internet links.** Is a link down, or is its latency or loss far above its
   usual value?
2. **Live usage.** Is a link's total close to its capacity?
3. **Devices → Recent 5 minutes.** Who used the most? This comes from logs, so
   it includes sessions the live estimate misses.
4. If the device that complained is not listed, its traffic may not pass a
   logging policy, or the problem is before the FortiGate (Wi-Fi, cable, the
   device itself).
5. If pages are slow or unavailable, open **Diagnostics** and check
   `clock_offset` first.
