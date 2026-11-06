# Capacity and sizing

These figures are measurements, not guarantees. Size from your own record rate:
it depends on how many sessions each device opens and what the FortiGate logs.

## What limits accounting

The accounting service processes received forward-traffic records in one writer.
Its Compose limits are half a CPU core and 192 MiB of memory.

| Measurement | Result |
|---|---|
| Sustained processing, production limits (0.5 CPU, 192 MiB) | about 1,600 records per second |
| Same with a 1.0 CPU limit | about 2,700 records per second |
| Peak memory per 30,000-record read | under 192 MiB |
| Raw ledger size | about 730 bytes per record (compact storage) |
| Report for 2,000 devices | 0.03 s; Applications 0.13 s |

Measured with `tests/support/benchmark_scale.py --devices 2000 --sessions 3 --locals 3`
(half Internet records, half local or UTM records, like a real site) on an 8-core
Ubuntu VM, inside a container with the production limits. Summary rows are
updated in memory and written once per batch; `tests/support/compare_engines.py`
confirmed the stored results are identical to the earlier per-record writes on
510,000 real and 144,000 synthetic records (231,371 ledger facts and every
summary table).

Each read from Loki is bounded to 30,000 records. When a busy site logs more than
that in the normal 120-second window, the window halves until a read fits and
grows back when traffic drops. Earlier versions stopped processing at a full window
instead; at 120 + 30 seconds that ceiling was about 200 records per second.

## Estimating your rate

Diagnostics and the Evidence page show received record counts. As a reference,
one office site with about 110 endpoint identities received 2.8–3.9 million
records a day: an average of 33–45 records per second, about 0.35 per device,
with a measured busiest hour of 74 records per second (2.2 times the daily
average, 2 PM local time).

| Devices (0.35 records/s each, peak 2.2x) | Average | Busiest hour | At 0.5 CPU (about 1,600/s) |
|---|---|---|---|
| 100 | 35/s | ~75/s | Comfortable |
| 500 | 175/s | ~385/s | Comfortable |
| 1,000 | 350/s | ~770/s | Comfortable at peak |
| 2,000 | 700/s | ~1,540/s | Fits, with almost no headroom in the busiest hour |
| 3,000 | 1,050/s | ~2,300/s | Needs a 1.0 CPU limit (about 2,700/s) |

Processing that falls behind shows as processing lag in Diagnostics. Recorded
totals are still exact once processing catches up; they are later, not lost,
while the records remain in Loki.

For more than about 1,500 devices, give the accounting service a 1.0 CPU limit
and confirm with your own record rate before relying on near-real-time reports.

## Storage

Raw records are kept for the ledger retention (3 days by default); daily and
monthly totals have their own longer retention and are small.

| Records per day | 3-day raw ledger | 1-day raw ledger |
|---|---|---|
| 3 million (about 100 devices) | ~7 GB | ~2 GB |
| 30 million (about 1,000 devices) | ~65 GB | ~22 GB |
| 60 million (about 2,000 devices) | ~130 GB | ~45 GB |

At the configured database limit the service drops the oldest raw days instead
of stopping. Whole-day, weekly, monthly and yearly reports keep working from the
summaries; custom ranges that start or end inside an expired day are refused
rather than shown as zero. Loki keeps its own copy of the raw logs and needs its
own disk budget.

## Live usage

Live usage asks the FortiGate for two FortiView pages (by source and by
application, sorted by bandwidth, at most 500 rows each) and one interface
counter read, at most once every 5 seconds however many browsers are open.
Totals come from interface counters and include every session. On very large
session tables, check the FortiGate's CPU while the page is open.
