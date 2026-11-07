# Website usage

**Websites** shows which websites used the Internet, and which devices used each
one. It is **off by default**. An administrator switches it on in
**Settings → Monitoring → Website Usage per Device**, then applies the change.

Per-device website history is personal data in many places. Before you switch
it on, tell the people on the network and follow your local law and company
policy.

## What is kept

| Kept | Not kept |
|---|---|
| The registrable site name, such as `whatsapp.com` or `example.co.uk` | Full hostnames below the site, URLs, paths, query strings, page titles |
| Upload and download bytes per day, device, segment and site | Times of individual visits |
| — | IP-literal "hostnames" and single-label names |

Website rows are kept for **90 days**, or for the summary retention if that is
shorter. Links between sessions and sites last only as long as the raw ledger,
three days by default.

## Where the website comes from

FortiGate traffic logs carry bytes but not the hostname. The hostname is in
**Application Control** (`subtype="app-ctrl"`) and **Web Filter**
(`subtype="webfilter"`) logs, which share the same session key as the
traffic logs: device, VDOM, session ID and 5-tuple.

FortiLens keeps the first website seen for each traffic session. Every byte that
session contributes to accounting is then also added to that website. Website
totals are therefore a subset of the recorded device totals: they never add
bytes, and a session counted once is never counted under two websites.

## Coverage

Only sessions that FortiGate logged in Application Control or Web Filter have a
website. The page shows what share of recorded Internet usage has a known
website, so a low share is visible instead of being read as zero.

On the site where FortiLens was checked, about 30% of bytes had a website in a
one-hour sample. To raise the share, in FortiGate:

- set the Application Control sensor on the Internet policies to log the
  categories you care about, for example **Monitor** rather than Allow; and
- use SSL certificate inspection (or deeper inspection) so that encrypted
  sessions have a hostname.

FortiLens never changes FortiGate settings.

## Periods

Website totals are kept per day. **Today**, **Week**, **Month**, **Year** and
custom ranges that start and end at midnight are supported. The day in progress
counts as "so far today". **Last 5 minutes** is not available for websites.

Usage before websites were switched on has no website. After switching it off,
nothing new is recorded and existing rows expire after 90 days.
