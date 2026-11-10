"""Offline sample audit of received usage records, not a collector."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FIELDS = "type, subtype, logid, vd, dstintfrole, mastersrcmac, srcip, srcname, sentdelta, rcvddelta, sentbyte, rcvdbyte"
CATEGORIES = {
    "included_delta": "Included: both delta fields",
    "included_cumulative_fallback": "Included: end-record cumulative fallback",
    "excluded_missing_mac": "Excluded: missing master MAC",
    "excluded_invalid_bytes": "Excluded: invalid or missing byte contribution",
    "excluded_role": "Excluded: destination role is not WAN",
    "excluded_event": "Excluded: unsupported event or log ID format",
    "excluded_type": "Excluded: not forward traffic",
    "unscoped_missing_vdom": "Unscoped: missing VDOM",
    "unscoped_parse_error": "Unscoped: parse error; VDOM may be unknown",
}


def parse_time(value):
    if not isinstance(value, str):
        raise ValueError("Timestamps must be ISO8601 strings with an explicit UTC offset")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Invalid ISO8601 timestamp; input contents suppressed") from None
    if stamp.tzinfo is None:
        raise ValueError("Timestamps require an explicit UTC offset")
    return stamp.astimezone(timezone.utc)


def window(period, zone, at):
    if zone == "browser":
        raise ValueError("Choose an explicit timezone; browser timezone is not known to an offline tool")
    try:
        tz = timezone.utc if zone == "UTC" else ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("Timezone unavailable; use UTC or an installed IANA timezone on Linux") from None
    end = parse_time(at)
    local_end = end.astimezone(tz)
    if period == "week":
        start = end - timedelta(hours=168)
    elif period in ("today", "month"):
        start = local_end.replace(hour=0, minute=0, second=0, microsecond=0,
                                  **({"day": 1} if period == "month" else {})).astimezone(timezone.utc)
    else:
        raise ValueError("Period must be today, week, or month")
    return start, end, {"period": period, "timezone": zone,
        "start_local": start.astimezone(tz).isoformat(), "end_local": local_end.isoformat(),
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "boundary": "start exclusive, end inclusive; matches Loki range aggregation",
        "duration_seconds": (end - start).total_seconds()}


def contribution(fields):
    if fields.get("parse_error"):
        return "unscoped_parse_error", None
    if not fields.get("vd"):
        return "unscoped_missing_vdom", None
    if fields.get("type") != "traffic" or fields.get("subtype") != "forward":
        return "excluded_type", None
    if fields.get("logid") not in ("0000000013", "0000000020"):
        return "excluded_event", None
    if fields.get("dstintfrole") != "wan":
        return "excluded_role", None
    if not fields.get("mastersrcmac"):
        return "excluded_missing_mac", None
    values, fallback = [], False
    for delta, cumulative in (("sentdelta", "sentbyte"), ("rcvddelta", "rcvdbyte")):
        value = fields.get(delta, "")
        if value == "" and fields["logid"] == "0000000013":
            value, fallback = fields.get(cumulative, ""), True
        if not re.fullmatch(r"[0-9]+", value):
            return "excluded_invalid_bytes", None
        # Loki's integer add operates on signed 64-bit values. Do not silently
        # claim that Python's arbitrary precision is a matching dashboard sum.
        if len(value) > 19 or int(value) > 2**63 - 1:
            return "excluded_invalid_bytes", None
        values.append(int(value))
    if sum(values) > 2**63 - 1:
        return "excluded_invalid_bytes", None
    return "included_cumulative_fallback" if fallback else "included_delta", sum(values)


def audit(records, *, period, zone, at, vdom):
    start, end, period_info = window(period, zone, at)
    if not isinstance(vdom, str) or not vdom or len(vdom) > 250 or any(ord(c) < 32 for c in vdom):
        raise ValueError("Invalid VDOM")
    if not isinstance(records, list) or len(records) > 100000:
        raise ValueError("Input must contain at most 100000 normalized records")
    categories, hashes, identities = Counter(), set(), defaultdict(set)
    observed = []
    recorded = duplicate_bytes = replay_candidates = outside = overflow = 0
    for record in records:
        if not isinstance(record, dict) or set(record) != {"timestamp", "fields"}:
            raise ValueError("Each record requires only timestamp and fields; input contents suppressed")
        stamp = parse_time(record["timestamp"])
        fields = record["fields"]
        if not isinstance(fields, dict) or len(fields) > 64 or not all(
                isinstance(k, str) and isinstance(v, str) and len(k) <= 100 and len(v) <= 4096
                for k, v in fields.items()):
            raise ValueError("Fields must be bounded string values; input contents suppressed")
        if not start < stamp <= end:
            outside += 1
            continue
        if fields.get("vd") not in (vdom, None, "") and not fields.get("parse_error"):
            continue
        # Whitelist before hashing. The sample audit intentionally never emits
        # source identities, field contents, or raw lines.
        selected = {k: fields[k] for k in FIELDS.replace(" ", "").split(",") if k in fields}
        selected.update({k: fields[k] for k in ("sessionid", "eventtime", "devid", "parse_error") if k in fields})
        digest = hashlib.sha256(json.dumps([stamp.isoformat(), selected], sort_keys=True).encode()).digest()
        repeated = digest in hashes
        hashes.add(digest)
        replay_candidates += repeated
        category, amount = contribution(fields)
        categories[category] += 1
        if category == "excluded_invalid_bytes":
            chosen = [fields.get(delta, "") or (fields.get(cumulative, "") if fields.get("logid") == "0000000013" else "")
                      for delta, cumulative in (("sentdelta", "sentbyte"), ("rcvddelta", "rcvdbyte"))]
            if all(re.fullmatch(r"[0-9]+", value) for value in chosen):
                overflow += any(len(value) > 19 for value in chosen) or sum(int(value) for value in chosen) > 2**63 - 1
        if fields.get("vd") == vdom:
            observed.append(stamp)
        if amount is not None:
            recorded += amount
            if repeated:
                duplicate_bytes += amount
            mac = fields["mastersrcmac"]
            identities[mac].add((fields.get("srcip", ""), fields.get("srcname", "")))
    warnings = ["Sample audit only: completeness of the supplied records and upstream traffic coverage are unknown.",
        "Silence cannot distinguish quiet traffic from failed delivery; no coverage percentage is inferred.",
        "Bytes are assigned at record timestamps. Cross-boundary sessions and delta intervals are not redistributed.",
        "Replay candidates use matching normalized fields and timestamps; this is not proof of duplicate network traffic.",
        "The dashboard is not deduplicated or regrouped by this audit. Device identity is not an authenticated user."]
    if any(k.startswith("unscoped_") for k in categories):
        warnings.append("Unscoped records cannot be attributed reliably to the selected VDOM.")
    split_devices = sum(len(groups) > 1 for groups in identities.values())
    if split_devices:
        warnings.append("At least one MAC has multiple IP/name combinations; the dashboard can show split rows.")
    if replay_candidates:
        warnings.append("Replay candidates may inflate recorded totals; review the source before removing any record.")
    if overflow:
        warnings.append("Oversized byte fields require review; the offline reference excludes them.")
    return {"schema_version": 1, "scope": "offline sample only", "status": "NEEDS_REVIEW" if categories else "UNAVAILABLE",
        "period": period_info, "vdom": vdom, "input_records": len(records), "outside_window": outside,
        "categories": dict(sorted(categories.items())), "sample_recorded_bytes": recorded if any(k.startswith("included_") for k in categories) else None,
        "replay_candidate_records": replay_candidates, "replay_candidate_bytes": duplicate_bytes,
        "macs_with_multiple_ip_name_combinations": split_devices,
        "first_observed_record_utc": min(observed).isoformat() if observed else None,
        "last_observed_record_utc": max(observed).isoformat() if observed else None,
        "coverage": "UNKNOWN", "warnings": warnings}


def audit_file(path, **options):
    path = Path(path)
    if path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Audit input exceeds 32 MiB")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError):
        raise ValueError("Invalid audit JSON; input contents suppressed") from None
    if not isinstance(data, dict) or set(data) != {"records"}:
        raise ValueError("Audit JSON requires only a records array")
    return audit(data["records"], **options)


def print_report(report, as_json=False):
    if as_json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        print("FortiLens usage sample audit — " + report["status"])
        p = report["period"]
        print(f"Period: {p['period']} / {p['timezone']}")
        print(f"Window: ({p['start_local']}, {p['end_local']}]")
        for key, count in report["categories"].items():
            print(f"{CATEGORIES[key]}: {count}")
        print("Sample recorded bytes: " + str(report["sample_recorded_bytes"]))
        print("Replay candidate records: " + str(report["replay_candidate_records"]))
        print("MACs with multiple IP/name combinations: " + str(report["macs_with_multiple_ip_name_combinations"]))
        for warning in report["warnings"]:
            print("Review: " + warning)
    # No PASS code: a sample cannot establish complete weekly/monthly coverage.
    return 2
