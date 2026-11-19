# Alerts and reports

FortiLens can send a message when something goes wrong, and a summary or report
on a schedule. It is off until an administrator sets up a channel in
**Settings → Alerts & Reports** and applies it. Nothing is changed on the
FortiGate.

The paused Grafana rule candidates of [Optional Grafana alerts](OPTIONAL_ALERTS.md)
are separate and unchanged.

## Channels

- **Telegram** carries every alert and the daily summary. Create a bot with
  BotFather, add it to the chat or group that should receive alerts, then enter
  the bot token and the chat ID. A group ID usually starts with `-100`.
- **Email** carries the critical alerts (marked below) and the weekly and
  monthly reports. It needs a mail server that accepts the user and password
  given, a From address and up to 20 recipients. STARTTLS on port 587 and TLS on
  port 465 are supported.

Without email, the reports come to Telegram as text; without Telegram, the daily
summary comes by email.

The bot token and the mail password are write-only, like the FortiGate API
token. They are saved with the draft, written on Apply to
`secrets/notify_credentials` in the installation, mounted into the accounting
service only, and never shown again. **Send test message** uses the saved draft,
so it works before Apply. It reports a fixed reason when delivery fails, such as
a refused token or an unreachable mail server, never the server's own text.

## Alerts sent right away

| Alert | Sends when | Critical |
|---|---|---|
| Line down | No health check reports the line alive for 2 minutes. | Yes |
| Internet slow on a line | For 10 minutes: delay at least 3 times the line's usual (its 7-day median on that check) and 150 ms or more, or packet loss of 20% or more. | |
| Line carries little traffic | For 15 minutes in working hours: download at 25% of what the line usually carries at this hour or less, while its sessions stay at 70% of usual or more. The usual is the median at the same hour on past working days. | |
| One device fills a line | One device uses half of a line's upload or download or more for 20 minutes. Needs line speeds; uses the FortiGate's live estimates, which can lag on a FortiGate that offloads sessions. | |
| Logs stopped arriving | No FortiGate log reached Loki for 15 minutes. | Yes |
| FortiGate unreachable | The FortiGate API or exporter has not answered for 5 minutes. This is not reported as a line outage. | Yes |
| Accounting behind | Usage processing trails the clock by more than 30 minutes. | |
| Storage nearly full | Less than 10% of the data disk free, or the accounting database above 90% of its limit. | Yes |
| SD-WAN rules changed | A rule was added, removed, reordered, or now uses other lines. Sent once per change. | |

A line is judged by the health check chosen in Settings when it covers the
line, otherwise by its best check, as on the Internet links page. When the
lines' speeds are set, a slow-line message says when the line is full, for
example "Upload 5.0 of 5 Mbps".

Each alert sends one message when it starts, a reminder every 2 hours while it
lasts, and one when it ends, after 2 minutes back to normal. Missing evidence,
such as a failed collection, neither opens nor closes an alert and is never read
as down or as zero traffic. Trouble that returns after more than 3 minutes
without evidence starts its waiting time over.

## Listed in the daily summary only

These are recorded, shown on the Alerts page, and counted in the next daily
summary. They are never sent on their own, because they are frequent: on the
test installation they would have sent about 44 messages in one week.

| Alert | Recorded when |
|---|---|
| Line losing packets | Loss at or above the warning in Monitoring (5% by default) for 20 minutes. |
| Line nearly full | Download or upload at 90% of the line's speed or more for 20 minutes. |
| Health checks disagree | A line's checks give opposite answers for an hour: a check problem, not an outage. |
| New device on the network | A device recorded for the first time. Listed only once the records reach back before the period, so a new installation does not list every device. |

## Working hours

Outside the working hours (default Sunday to Thursday, 08:00 to 18:00, in the
accounting timezone), only critical alerts are sent. The others are recorded on
the Alerts page as held and counted in the next daily summary.

## Reports

| Report | When | Covers |
|---|---|---|
| Daily summary | Working days at 08:00 | Each day since the last summary (after a weekend, the whole weekend): trouble per line and service, the alerts sent, usage, top 5 devices and new devices. |
| Weekly report | Sunday at 08:00 | The last 7 whole days: per line average loss, incidents of 20 minutes or longer, busy-time use against its speed and average sessions; usage, top 10 devices, segments, applications and new devices. |
| Monthly report | The 1st at 08:00, off by default | The last month: the weekly content plus a **provider report per line**: the share of observed time it worked, average loss and delay, and every outage (2 minutes or longer) and degraded period (5% loss or more for 20 minutes or longer) with its start, length, worst loss and delay. Send it to the line's provider as evidence. |

Times and days follow the accounting timezone. A report is sent once, within an
hour of its time; if the service was down for that hour it is skipped, not sent
late. **Preview** shows the text that would be sent now; **Send a sample now**
sends it once with the saved draft's channel. Usage figures are recorded usage:
they count only sessions the FortiGate logs.

## The Alerts page

**System → Alerts** lists what is wrong now and the last 100 messages, including
held and summary-only alerts and each report, with the delivery on each channel.
The state is kept in `alerts.json` beside the accounting database, so a restart
neither repeats nor loses an open alert or a sent report.
