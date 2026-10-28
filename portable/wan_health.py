"""WAN observation math. Missing collection never becomes an Internet outage."""
import math

KNOWN = {"UP", "DOWN", "ERROR", "DISABLED", "UNKNOWN"}


def finite(value):
    try:
        return float(value) if value is not None and math.isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def current_state(rows, now, freshness, collection_ok):
    if not collection_ok:
        return "NO DATA"
    recent = [r for r in rows if r.get('state', '').upper() in KNOWN and r.get('value') == 1
              and 0 <= now-r.get('timestamp', -math.inf) <= freshness]
    latest = max((r['timestamp'] for r in recent), default=None)
    active = {r['state'].upper() for r in recent if r['timestamp'] == latest}
    return next(iter(active)) if len(active) == 1 else "UNKNOWN" if active else "NO DATA"


def route_evidence(payload, confirmed_wans, destination):
    """A successful route lookup is destination-specific, not all-flow SD-WAN proof."""
    result = payload.get("results") if isinstance(payload, dict) and payload.get("status") == "success" else None
    valid = isinstance(result, dict) and result.get("success") is True and result.get("interface") in confirmed_wans
    return {"status": "OBSERVED" if valid else "UNKNOWN", "interface": result["interface"] if valid else None,
            "destination": destination, "scope": "IPv4 route lookup for this destination only; policy/SD-WAN per-flow choices may differ."}


def summarize(samples, start, end, *, max_hold=120, latency_ms=150, packet_loss_pct=5):
    if not start < end or max_hold <= 0:
        raise ValueError("A positive observation window and hold interval are required")
    by_stamp = {}
    for sample in samples:
        stamp = finite(sample.get("timestamp"))
        if stamp is None or stamp > end or stamp+max_hold <= start:
            continue
        current = dict(sample)
        current["state"] = current.get("state", "UNKNOWN") if current.get("collection_ok", True) else "NO DATA"
        if current["state"] not in KNOWN | {"NO DATA"}:
            current["state"] = "UNKNOWN"
        if stamp in by_stamp and by_stamp[stamp]["state"] != current["state"]:
            current["state"] = "UNKNOWN"
        by_stamp[stamp] = current
    points = sorted(by_stamp.items())
    segments, up, down, observed = [], 0., 0., 0.
    for i, (stamp, sample) in enumerate(points):
        a, b = max(start, stamp), min(end, stamp+max_hold, points[i+1][0] if i+1 < len(points) else end)
        if a >= b:
            continue
        state = sample["state"]
        if state == "NO DATA":
            continue
        observed += b-a
        up += b-a if state == "UP" else 0
        down += b-a if state == "DOWN" else 0
        latency, loss = finite(sample.get("latency_ms")), finite(sample.get("packet_loss_pct"))
        reasons = (["HIGH_LATENCY"] if latency is not None and latency >= latency_ms else []) + (["HIGH_PACKET_LOSS"] if loss is not None and loss >= packet_loss_pct else [])
        segments.append({"start": a, "end": b, "state": state, "degraded": state == "UP" and bool(reasons), "reasons": reasons})
    # Return continuous runs instead of thousands of per-scrape rectangles.
    # Unknown gaps remain separate; no interval is extended through a gap.
    compact=[]
    for segment in segments:
        if compact and compact[-1]['end']==segment['start'] and all(compact[-1][key]==segment[key] for key in ('state','degraded','reasons')):
            compact[-1]['end']=segment['end']
        else:
            compact.append(dict(segment))
    segments=compact
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
            item["observed_seconds"] = item["end"]-item["start"]
        return result
    return {"status": "OBSERVED_ONLY" if observed else "UNAVAILABLE", "window_seconds": end-start,
            "up_observed_seconds": up, "down_observed_seconds": down, "unknown_or_unobserved_seconds": end-start-up-down,
            "availability_percent": 100*up/(up+down) if up+down else None,
            "valid_state_coverage_percent": 100*(up+down)/(end-start),
            "sample_coverage_percent": 100*observed/(end-start), "outages": runs(lambda s: s["state"] == "DOWN"),
            "segments": segments,
            "degradation": runs(lambda s: s["degraded"]),
            "note": "Estimates from bounded observed sample intervals. Missing intervals are unknown; a gap does not prove outage or recovery."}
