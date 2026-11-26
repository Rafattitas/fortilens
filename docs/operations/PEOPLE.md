# Usage by person

**People** shows who used the Internet: recorded usage grouped by the person
signed in on each device, and the devices each person used. Elsewhere in the
console, a device shows the last person signed in on it beside its name.

FortiLens does not authenticate anyone. The names come from the FortiGate, and
only when the FortiGate knows them. A FortiGate that does not learn sign-ins
sends no names, and the page says so.

Per-person usage is personal data in many places. Tell the people on the network
and follow your local law and company policy.

## Where the name comes from

When the FortiGate learns who signed in at an address, it writes that account in
the `user` field of its traffic logs. When it learned the sign-in passively and
no policy asked for it, the field is `unauthuser`. FortiLens reads either field
from the logs it already receives. Nothing else on the FortiGate changes.

The usual way for the FortiGate to learn sign-ins without asking anyone to type
anything is FSSO with Active Directory:

- **Poll Active Directory Server** (agentless). The FortiGate reads sign-in
  events from the domain controller's Security log. Create an LDAP server under
  **User & Authentication → LDAP Servers**, then a **Poll Active Directory
  Server** connector under **Security Fabric → External Connectors** that uses it.
  The polling account needs to read the Security log (the built-in **Event Log
  Readers** group); it does not need to be a domain administrator. Enter the
  account name in the form the connector accepts on your FortiOS version.
- **FSSO Collector Agent**. A Fortinet service on a Windows server collects sign-ins
  and sends them to the FortiGate.

No firewall policy has to use the user groups for the names to appear in the
logs. To check that the FortiGate knows people, run
`diagnose debug authd fsso list` on the FortiGate CLI.

Only domain sign-ins are learned. Phones, printers, guests and computers signed in
with a local account have no name. A FortiGate that only polls sign-ins does not
see sign-outs, so a name stays on an address until someone else signs in there or
the sign-in expires.

## What is kept

| Kept | Not kept |
|---|---|
| Upload and download bytes per day, device, segment and person | Times of individual connections |
| The account name, in lower case, as the FortiGate logged it | Passwords, groups, or anything read from Active Directory |
| The last person named on each device, and when | — |

Names are compared without regard to case, so one account the FortiGate logs in
capitals and in lower case is one person. Person rows are kept as long as the
daily usage summaries (`accounting_summary_days`).

## How the bytes are counted

The name is a label on each traffic record, like an application. It never
changes how a device is identified or how its bytes are counted. Every byte a
named record contributes to accounting is also added to that person. Person
totals are therefore the named part of the recorded device totals: they never add
bytes, and a device used by two people is split by record, not guessed.

The page shows what share of recorded Internet usage names a person, so devices
nobody signed in on are visible instead of being missed.

## Periods

Person totals are kept per day. **Today**, **Last 7 days**, **This month**,
**This year** and custom ranges that start and end at midnight are supported. The
day in progress counts as "so far today". **Recent 5 minutes** is not available
for people.

Usage before the FortiGate first named someone has no name. Records received
before this version have no name either.
