"""WAN observation math. Missing collection never becomes an Internet outage."""

import math

KNOWN = {"UP", "DOWN", "ERROR", "DISABLED", "UNKNOWN"}


def finite(value):
    try:
        return float(value) if value is not None and math.isfinite(float(value)) else None
    except (ValueError, TypeError, OverflowError):
        return None


def current_state(rows, now, freshness, collection_ok):
    if not collection_ok:
        return "NO DATA"
    recent = [
        r
        for r in rows
        if r.get("state", "").upper() in KNOWN
        and r.get("value") == 1
        and 0 <= now - r.get("timestamp", -math.inf) <= freshness
    ]
    latest = max((r["timestamp"] for r in recent), default=None)
    active = {r["state"].upper() for r in recent if r["timestamp"] == latest}
    return next(iter(active)) if len(active) == 1 else "UNKNOWN" if active else "NO DATA"


def route_evidence(payload, confirmed_wans, destination):
    """A successful route lookup is destination-specific, not all-flow SD-WAN proof."""
    result = (
        payload.get("results")
        if isinstance(payload, dict) and payload.get("status") == "success"
        else None
    )
    valid = (
        isinstance(result, dict)
        and result.get("success") is True
        and result.get("interface") in confirmed_wans
    )
    return {
        "status": "OBSERVED" if valid else "UNKNOWN",
        "interface": result["interface"] if valid else None,
        "destination": destination,
        "scope": "IPv4 route lookup for this destination only; policy/SD-WAN per-flow choices may differ.",
    }


def summarize(samples, start, end, *, max_hold=120, latency_ms=150, packet_loss_pct=5):
    if not start < end or max_hold <= 0:
        raise ValueError("A positive observation window and hold interval are required")
    by_stamp = {}
    for sample in samples:
        stamp = finite(sample.get("timestamp"))
        if stamp is None or stamp > end or stamp + max_hold <= start:
            continue
        current = dict(sample)
        current["state"] = (
            current.get("state", "UNKNOWN") if current.get("collection_ok", True) else "NO DATA"
        )
        if current["state"] not in KNOWN | {"NO DATA"}:
            current["state"] = "UNKNOWN"
        if stamp in by_stamp and by_stamp[stamp]["state"] != current["state"]:
            current["state"] = "UNKNOWN"
        by_stamp[stamp] = current
    points = sorted(by_stamp.items())
    segments, up, down, observed = [], 0.0, 0.0, 0.0
    for i, (stamp, sample) in enumerate(points):
        a, b = (
            max(start, stamp),
            min(end, stamp + max_hold, points[i + 1][0] if i + 1 < len(points) else end),
        )
        if a >= b:
            continue
        state = sample["state"]
        if state == "NO DATA":
            continue
        observed += b - a
        up += b - a if state == "UP" else 0
        down += b - a if state == "DOWN" else 0
        latency, loss = finite(sample.get("latency_ms")), finite(sample.get("packet_loss_pct"))
        reasons = (["HIGH_LATENCY"] if latency is not None and latency >= latency_ms else []) + (
            ["HIGH_PACKET_LOSS"] if loss is not None and loss >= packet_loss_pct else []
        )
        segments.append(
            {
                "start": a,
                "end": b,
                "state": state,
                "degraded": state == "UP" and bool(reasons),
                "reasons": reasons,
            }
        )
    # Return continuous runs instead of thousands of per-scrape rectangles.
    # Unknown gaps remain separate; no interval is extended through a gap.
    compact = []
    for segment in segments:
        if (
            compact
            and compact[-1]["end"] == segment["start"]
            and all(compact[-1][key] == segment[key] for key in ("state", "degraded", "reasons"))
        ):
            compact[-1]["end"] = segment["end"]
        else:
            compact.append(dict(segment))
    segments = compact

    def runs(predicate):
        result = []
        for s in segments:
            if not predicate(s):
                continue
            if result and result[-1]["end"] == s["start"]:
                result[-1]["end"] = s["end"]
                result[-1]["reasons"] = sorted(set(result[-1]["reasons"]) | set(s["reasons"]))
            else:
                result.append({"start": s["start"], "end": s["end"], "reasons": list(s["reasons"])})
        for item in result:
            following = next((s for s in segments if s["start"] == item["end"]), None)
            item["end_censored"] = following is None or following["state"] not in {"UP", "DOWN"}
            item["observed_seconds"] = item["end"] - item["start"]
        return result

    return {
        "status": "OBSERVED_ONLY" if observed else "UNAVAILABLE",
        "window_seconds": end - start,
        "up_observed_seconds": up,
        "down_observed_seconds": down,
        "unknown_or_unobserved_seconds": end - start - up - down,
        "availability_percent": 100 * up / (up + down) if up + down else None,
        "valid_state_coverage_percent": 100 * (up + down) / (end - start),
        "sample_coverage_percent": 100 * observed / (end - start),
        "outages": runs(lambda s: s["state"] == "DOWN"),
        "segments": segments,
        "degradation": runs(lambda s: s["degraded"]),
        "note": "Estimates from bounded observed sample intervals. Missing intervals are unknown; a gap does not prove outage or recovery.",
    }


# Diagnosis over a period: which line is in trouble, when, and whether load explains it.
# Interval states: g working, w losing packets, d down, n no evidence.
LOAD_EDGES = (100, 500, 1000, 2000, 5000)


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def _at(values, i):
    return values[i] if values and i < len(values) else None


def _hours(minutes):
    return f"{minutes / 60:.1f} hours" if minutes >= 90 else f"{round(minutes)} minutes"


def _pct(value):
    return f"{value:.2f}%" if value < 1 else f"{value:.1f}%"


def _primary(checks, preferred):
    def samples(name):
        return sum(1 for v in checks[name].get("loss_avg", []) if v is not None)

    if preferred in checks and samples(preferred):
        return preferred, False
    ranked = sorted((n for n in checks if samples(n)), key=lambda n: (-samples(n), n))
    return (ranked[0], preferred is not None) if ranked else (None, False)


def _states(checks, primary, buckets, loss_ratio):
    """Interval states plus the loss series they were judged by.

    When the primary check calls the line dead but another check sees it alive, the
    primary's 100% loss is about the check, not the line: use the best alive check's loss.
    """
    states, disagree = [], []
    loss = {"loss_avg": [], "loss_max": [], "loss_bad": []}
    others = [checks[n] for n in sorted(checks) if n != primary]
    p = checks.get(primary, {})
    for i in range(buckets):
        loss_max, down_max = _at(p.get("loss_max"), i), _at(p.get("down_max"), i)
        down_avg, up_avg = _at(p.get("down_avg"), i), _at(p.get("up_avg"), i)
        says_down, says_up = (down_avg or 0) >= 0.5, (up_avg or 0) >= 0.5
        alive = [o for o in others if (_at(o.get("up_avg"), i) or 0) >= 0.5]
        other_down = any((_at(o.get("down_avg"), i) or 0) >= 0.5 for o in others)
        split = (says_down and bool(alive)) or (says_up and other_down)
        disagree.append(split)
        source = p
        if says_down and alive:
            source = min(alive, key=lambda o: _at(o.get("loss_max"), i) or 0)
            loss_max = _at(source.get("loss_max"), i)
        for field in loss:
            loss[field].append(_at(source.get(field), i))
        if loss_max is None and down_max is None and up_avg is None:
            states.append("n")
        elif down_max and not alive:
            states.append("d")
        elif loss_max is not None and loss_max >= loss_ratio:
            states.append("w")
        else:
            states.append("g")
    return "".join(states), disagree, loss


def _runs(flags):
    runs, begin = [], None
    for i, on in enumerate(list(flags) + [False]):
        if on and begin is None:
            begin = i
        elif not on and begin is not None:
            runs.append((begin, i))
            begin = None
    return runs


def _load_buckets(sessions, loss):
    groups = {}
    for s, v in zip(sessions, loss):
        if s is None or v is None:
            continue
        k = sum(1 for edge in LOAD_EDGES if s >= edge)
        groups.setdefault(k, []).append(v)
    return [
        {
            "min": LOAD_EDGES[k - 1] if k else 0,
            "max": LOAD_EDGES[k] if k < len(LOAD_EDGES) else None,
            "loss_pct": 100 * sum(v) / len(v),
            "samples": len(v),
        }
        for k, v in sorted(groups.items())
    ]


def _load_effect(rows, minimum):
    """Loss in the lightest well-sampled load range against everything above it."""
    usable = [r for r in rows if r["samples"] >= minimum]
    if len(usable) < 2:
        return None
    low, high = usable[0], usable[1:]
    count = sum(r["samples"] for r in high)
    return {
        "below": low["max"],
        "low_loss_pct": low["loss_pct"],
        "high_loss_pct": sum(r["loss_pct"] * r["samples"] for r in high) / count,
    }


def insights(
    series,
    start,
    step,
    buckets,
    *,
    check=None,
    loss_pct=5,
    scrape_seconds=60,
    incident_seconds=1200,
    observed_at=None,
):
    """Diagnose each line over a period from bucketed health-check and session series.

    ``series`` maps an interface to ``{"checks": {name: {field: [per bucket]}},
    "sessions": [...], "peak_sessions": [...], "rx_bytes": [...], "tx_bytes": [...]}``
    (bytes per second).
    Loss values are ratios (0-1), state values are fractions of the bucket, ``loss_bad``
    counts scrape samples at or above ``loss_pct``. ``None`` is missing evidence.
    """
    if step <= 0 or buckets <= 0:
        raise ValueError("A positive step and bucket count are required")
    ratio = loss_pct / 100
    links, judged_by = [], {}
    for name in sorted(series):
        data = series[name]
        checks = data.get("checks", {})
        primary, fallback = _primary(checks, check)
        states, disagree, judged = _states(checks, primary, buckets, ratio)
        judged_by[name] = judged
        p = checks.get(primary, {})
        sessions = (list(data.get("sessions") or []) + [None] * buckets)[:buckets]
        loss_avg = judged["loss_avg"]
        down = sum(
            (_at(p.get("down_avg"), i) or 0) * step
            for i in range(buckets)
            if states[i] == "d" and not disagree[i]
        )
        rx, tx = _mean(data.get("rx_bytes") or []), _mean(data.get("tx_bytes") or [])
        avg_loss, latency = _mean(loss_avg), _mean(p.get("latency") or [])
        links.append(
            {
                "interface": name,
                "check": primary,
                "check_fallback": fallback,
                "checks": sorted(checks),
                "states": states,
                "disagree": "".join("p" if d else "." for d in disagree),
                "sessions": [None if s is None else round(s, 1) for s in sessions],
                "summary": {
                    "coverage_pct": 100 * sum(1 for s in states if s != "n") / buckets,
                    "avg_sessions": _mean(sessions),
                    "peak_sessions": max(
                        (v for v in data.get("peak_sessions") or [] if v is not None),
                        default=None,
                    ),
                    "avg_download_mbps": None if rx is None else rx * 8 / 1e6,
                    "avg_upload_mbps": None if tx is None else tx * 8 / 1e6,
                    "avg_loss_pct": None if avg_loss is None else avg_loss * 100,
                    "avg_latency_ms": None if latency is None else latency * 1000,
                    "loss_minutes": sum(v for v in judged["loss_bad"] if v is not None)
                    * scrape_seconds
                    / 60,
                    "down_minutes": down / 60,
                    "disagree_minutes": sum(disagree) * step / 60,
                },
                "loss_by_load": _load_buckets(sessions, loss_avg),
            }
        )
    total = sum(l["summary"]["avg_sessions"] or 0 for l in links)
    for l in links:
        share = l["summary"]["avg_sessions"]
        l["summary"]["session_share_pct"] = 100 * share / total if total and share else None

    by_name = {l["interface"]: l for l in links}
    incidents = []
    for l in links:
        for a, b in _runs(s in "wd" for s in l["states"]):
            if (b - a) * step < incident_seconds:
                continue
            peak = [v for v in judged_by[l["interface"]]["loss_max"][a:b] if v is not None]
            load = [v for v in l["sessions"][a:b] if v is not None]
            others = []
            for o in links:
                if o is l:
                    continue
                seen = [s for s in o["states"][a:b] if s != "n"]
                affected = sum(1 for s in seen if s in "wd")
                others.append(
                    {
                        "interface": o["interface"],
                        "affected_fraction": affected / len(seen) if seen else None,
                    }
                )
            incidents.append(
                {
                    "interface": l["interface"],
                    "start": start + a * step,
                    "end": start + b * step,
                    "state": "DOWN" if "d" in l["states"][a:b] else "LOSS",
                    "peak_loss_pct": max(peak) * 100 if peak else None,
                    "avg_sessions": _mean(load),
                    "others": others,
                }
            )
    incidents.sort(key=lambda x: (x["start"], x["interface"]))

    findings = []
    minimum = max(6, round(3600 / step))
    loaded = set()
    shares = [l["summary"]["avg_sessions"] for l in links if l["summary"]["avg_sessions"]]
    for l in sorted(links, key=lambda l: -(l["summary"]["session_share_pct"] or 0)):
        share = l["summary"]["session_share_pct"]
        if share is None or len(shares) < 2 or share < 40:
            continue
        rest = [
            o["summary"]["avg_sessions"]
            for o in links
            if o is not l and o["summary"]["avg_sessions"] is not None
        ]
        others_text = (
            f"The other lines carry {round(min(rest)):,}–{round(max(rest)):,} sessions on average."
            if len(rest) > 1
            else f"The other line carries {round(rest[0]):,} sessions on average."
        )
        effect = _load_effect(l["loss_by_load"], minimum)
        if (
            effect
            and effect["high_loss_pct"] >= 1
            and effect["high_loss_pct"] >= 3 * max(effect["low_loss_pct"], 0.1)
        ):
            loaded.add(l["interface"])
            findings.append(
                {
                    "kind": "load",
                    "severity": "warn",
                    "interfaces": [l["interface"]],
                    "title": f"{l['interface']} carries {round(share)}% of all sessions, and its loss rises with load",
                    "detail": f"Below {effect['below']:,} sessions {l['interface']} loses {_pct(effect['low_loss_pct'])} of packets; above that it loses {_pct(effect['high_loss_pct'])}. {others_text}",
                    "evidence": "load",
                }
            )
        elif share >= 60:
            findings.append(
                {
                    "kind": "share",
                    "severity": "info",
                    "interfaces": [l["interface"]],
                    "title": f"{l['interface']} carries {round(share)}% of all sessions",
                    "detail": f"Most users depend on this line. {others_text}",
                    "evidence": "lines",
                }
            )
    for l in links:
        minutes = l["summary"]["disagree_minutes"]
        if minutes < 60:
            continue
        p = series[l["interface"]].get("checks", {})
        flags = [i for i, c in enumerate(l["disagree"]) if c == "p"]
        dead = {
            n: sum(1 for i in flags if (_at(c.get("down_avg"), i) or 0) >= 0.5)
            for n, c in p.items()
        }
        alive = {
            n: sum(1 for i in flags if (_at(c.get("up_avg"), i) or 0) >= 0.5) for n, c in p.items()
        }
        said_dead = max(dead, key=lambda n: (dead[n], n))
        said_alive = max(
            (n for n in alive if n != said_dead),
            key=lambda n: (alive[n], n),
            default="another check",
        )
        findings.append(
            {
                "kind": "checks_disagree",
                "severity": "probe",
                "interfaces": [l["interface"]],
                "title": f"The health checks on {l['interface']} disagreed for {_hours(minutes)}",
                "detail": f"“{said_dead}” reported {l['interface']} dead while “{said_alive}” reported it alive. These intervals are not counted as down time. Check that health check's server and settings before relying on failover that uses it.",
                "evidence": "timeline",
            }
        )
    for l in links:
        s = l["summary"]
        rows = [r for r in l["loss_by_load"] if r["samples"] >= minimum]
        light = rows[0]["loss_pct"] if rows else None
        if (
            s["loss_minutes"] < 120
            or (s["avg_loss_pct"] or 0) < 1
            or (l["interface"] in loaded and (light is None or light < 1))
        ):
            continue
        findings.append(
            {
                "kind": "unstable",
                "severity": "warn",
                "interfaces": [l["interface"]],
                "title": f"{l['interface']} loses packets often, even with little traffic"
                if light is not None and light >= 1
                else f"{l['interface']} loses packets often",
                "detail": f"5% loss or more for {_hours(s['loss_minutes'])} in this period, with about {round(s['avg_sessions'] or 0):,} sessions on average."
                + (
                    " Load does not explain this; check the line or its router."
                    if light is not None and light >= 1
                    else ""
                ),
                "evidence": "lines",
            }
        )
    pairs = {}
    for i, x in enumerate(incidents):
        for y in incidents[i + 1 :]:
            if x["interface"] == y["interface"]:
                continue
            overlap = min(x["end"], y["end"]) - max(x["start"], y["start"])
            shorter = min(x["end"] - x["start"], y["end"] - y["start"])
            if overlap > 0 and overlap >= shorter / 2:
                key = tuple(sorted((x["interface"], y["interface"])))
                pairs[key] = pairs.get(key, 0) + 1
    # One finding for repeated overlaps; a single coincidence is not a pattern.
    repeated = sorted(
        ((pair, count) for pair, count in pairs.items() if count >= 3),
        key=lambda kv: (-kv[1], kv[0]),
    )
    if repeated:
        (a, b), count = repeated[0]
        also = "; ".join(f"{x} and {y}, {n} times" for (x, y), n in repeated[1:])
        findings.append(
            {
                "kind": "shared",
                "severity": "warn",
                "interfaces": sorted({name for pair, _ in repeated for name in pair}),
                "title": f"{a} and {b} often lose packets at the same time",
                "detail": f"{count} incidents on these two lines overlapped"
                + (f" (also {also})" if also else "")
                + ". When lines fail together, look at what they share: the same router, subnet or provider, or the FortiGate itself.",
                "evidence": "incidents",
            }
        )
    for l in links:
        if l["check"] is None:
            findings.append(
                {
                    "kind": "no_evidence",
                    "severity": "info",
                    "interfaces": [l["interface"]],
                    "title": f"No health-check evidence for {l['interface']}",
                    "detail": "No SD-WAN health check reported this line in this period. Its state is unknown, not down.",
                    "evidence": "lines",
                }
            )
        elif l["check_fallback"]:
            findings.append(
                {
                    "kind": "coverage",
                    "severity": "info",
                    "interfaces": [l["interface"]],
                    "title": f"{l['interface']} is not in the “{check}” health check",
                    "detail": f"This page judges {l['interface']} by “{l['check']}” instead. SD-WAN rules that use “{check}” cannot react to {l['interface']}’s quality.",
                    "evidence": "lines",
                }
            )
    if not any(f["severity"] in ("warn", "probe") for f in findings) and any(
        l["check"] for l in links
    ):
        findings.insert(
            0,
            {
                "kind": "clear",
                "severity": "good",
                "interfaces": [],
                "title": "No line problems stand out in this period",
                "detail": f"No line lost {loss_pct}% of packets or more for long, and no health checks disagreed for an hour or more.",
                "evidence": "timeline",
            },
        )
    order = {"warn": 0, "probe": 1, "info": 2, "good": 3}
    findings.sort(key=lambda f: order[f["severity"]])
    observed = any(l["check"] for l in links)
    return {
        "status": "OBSERVED_ONLY" if observed else "UNAVAILABLE",
        "start": start,
        "step": step,
        "buckets": buckets,
        "observed_at": observed_at if observed_at is not None else start + buckets * step,
        "check": check,
        "loss_threshold_pct": loss_pct,
        "incident_min_seconds": incident_seconds,
        "links": links,
        "incidents": incidents[-200:],
        "findings": findings,
        "note": "Each line is judged by the configured health check when that check covers it, otherwise by the check with the most samples. A line is down only when no other check reports it alive. Missing intervals are no evidence, never down or up.",
    }
