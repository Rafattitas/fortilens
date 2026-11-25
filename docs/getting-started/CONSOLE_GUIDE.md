# Console guide

What each page of the FortiLens console shows, where its numbers come from and
how far to trust them. To install or configure, see [Web Setup](WEB_SETUP.md).
For what each check means, see the [Diagnostics reference](../operations/DIAGNOSTICS.md).

## Which page answers which question

| Question | Page |
|---|---|
| Is everything collecting right now? | Overview, then Health |
| Who is using the Internet this minute? | Live usage |
| Who used the most in the last five minutes, today, this week or this month? | Devices, with the period you need |
| Which network segment uses the most? | Segments |
| Which applications or websites? | Applications, Websites |
| Is an Internet link down or degraded? | Internet links |
| Firewall CPU, memory, sessions and interface traffic over time | Firewall dashboard |
| Why does a number look low or is missing? | Data quality |
| Why is a page unavailable or slow? | Health |

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

The last log time here is the newest record accounting has processed. When
accounting trails by more than 15 minutes but the logs it processed run up to
that point, the page says accounting is behind, not that logs stopped. Usage
reads low until it catches up. A backlog that keeps growing at busy hours
usually means accounting needs more CPU (Settings → Storage & data retention →
Accounting Processing CPU). Health shows when the newest log reached Loki.

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
- `live_usage_api` in Health says which FortiGate API parts responded.

### Internet links

The page opens with a diagnosis of the chosen period (last hour, 24 hours or 7
days), then shows the current state under **Right now**.

- **What is going on** states the problems found, in plain sentences, each with a
  link to its evidence: a line that carries most sessions and loses more packets
  when busy, a line that loses packets even with little traffic, lines that
  often lose packets at the same time (look at what they share: router, subnet,
  provider or the FortiGate), health checks that disagree about a line, a
  line that the configured health check does not cover, a line that is nearly
  full at busy times, an SD-WAN rule that sends everyone else to one line
  without ever moving them, and rules that never apply as written.
- **Lines in this period** compares the lines: which health check judges each,
  its share of sessions with their average and peak count over the period (the
  count right now is under Live usage), average rates, average loss, time spent losing 5% or
  more (the loss warning in Settings), down time, how full it is (when its
  speed is set in Settings → Network Interfaces) and whether its health checks
  agree. "Busy" is the use in the busiest 5% of intervals.
- **When it happened** shows each line over the period: working, losing
  packets, down, or no evidence. A purple stripe under a line marks intervals
  where its health checks disagreed. Point at an interval to see its time and
  session count.
- **Incidents** lists trouble that lasted 20 minutes or longer. Selecting one
  shows how long it lasted, the worst loss, the sessions on the line, whether
  the other lines were affected at the same time, and the devices whose recorded
  traffic left through that line then, largest first. Device detail comes from
  the raw records, which are kept for the ledger retention set in Settings
  (3 days by default); a long incident may show only its first part.
- **Does load cause the loss?** groups each line's loss by how many sessions it
  carried. Loss that rises only at high load points at capacity; loss at low load
  points at the line or its router.
- **Who goes where** lists the FortiGate's SD-WAN rules in order, read-only:
  who each rule covers, its lines and mode, and what keeps it from applying: a
  rule below one that already takes all traffic never matches, a source listed
  in an earlier rule stays with that rule, and a rule in manual mode never moves
  traffic when its line loses packets. If the rules cannot be read, the rest of
  the page still works.

A line is judged by the health check chosen in Settings when that check covers
it, otherwise by its other health check, and the page says so. A line is down
only when no health check reports it alive: when one check calls it dead and
another calls it alive, the page reports a check problem instead of an outage.
Intervals without samples are no evidence, never up or down.

Right now, one row per Internet link:

- **Internet state** comes from the SD-WAN health check (SLA) chosen in Settings.
  A link that is not a member of that health check has no SLA evidence.
- **Physical link** is the interface state. A link can be physically up while its
  Internet state is down.
- **Sessions now** is the FortiGate's own count of open sessions on the line,
  read every minute. Live usage shows the same count next to each line. A
  session is one connection; one device can hold hundreds.
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

### Data quality

Follows a record from received to processed to classified and counts what was
excluded and why. Use it when a figure looks low.

## System

- **Alerts** shows what is wrong now and every alert FortiLens sent, with its
  delivery on Telegram and email. Alerts are set up in Settings → Alerts &
  Reports; see [Alerts](../operations/ALERTS.md).
- **Health** lists the monitoring services, storage use and whether the saved
  configuration is applied, then runs read-only checks and says what to do
  about each finding. See the [checks reference](../operations/DIAGNOSTICS.md).
  It replaces the former Status and Diagnostics pages; their old links open it.
- **Settings** changes the installation. **Save draft** keeps a change as a
  revision without changing anything that runs. Until it is applied, a bar
  above every section names the waiting changes and offers **Review and
  apply**, which lists each change with its running and new value, the services
  that restart, and asks for approval before **Apply changes**.

## When someone says the Internet is slow

1. **Internet links.** Is a link down, or is its latency or loss far above its
   usual value?
2. **Live usage.** Is a link's total close to its capacity?
3. **Devices → Recent 5 minutes.** Who used the most? This comes from logs, so
   it includes sessions the live estimate misses.
4. If the device that complained is not listed, its traffic may not pass a
   logging policy, or the problem is before the FortiGate (Wi-Fi, cable, the
   device itself).
5. If pages are slow or unavailable, open **Health** and check
   `clock_offset` first.
