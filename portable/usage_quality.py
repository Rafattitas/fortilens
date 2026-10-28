"""Received-record diagnostics and an offline sample audit, not a collector."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

FIELDS = "type, subtype, logid, vd, dstintfrole, mastersrcmac, srcip, srcname, sentdelta, rcvddelta, sentbyte, rcvdbyte"
PERIODS = (("today", "now/d", "Today"), ("week", "7d", "Last 7 Days"), ("month", "now/M", "This Month"))
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

GUIDE = """### Reporting periods and evidence
- **Today:** start of the day. **Last 7 Days:** rolling seven days. **This Month:** start of the calendar month.
- Period boundaries use the dashboard timezone. An absolute time range overrides relative panel periods; inspect each panel's displayed range.
- Totals are **recorded device bytes**, grouped by MAC/IP/name. IP or name changes can split rows. These are not authenticated-user accounts.
- Expand **Usage Data Quality** to review received records and exclusion reasons. Counts describe the received logs, not complete traffic coverage. Missing categories are not proof of zero events.
- Cumulative fallback and delta intervals can cross a reporting boundary. Replayed logs can inflate totals. Neither issue is repaired by displaying a successful query.
- VDOM filters usage; FortiGate, SLA and WAN selectors do not. Missing VDOM/parser-error counts are explicitly unscoped.
"""


def enhance_dashboard(source):
    result = deepcopy(source)
    spec, elements = result["spec"], result["spec"]["elements"]
    if "panel-42" in elements:
        if elements["panel-42"]["spec"].get("title") == "Reporting Periods & Evidence" and all(
                "panel-" + str(i) in elements for i in (43, 44, 45)):
            return result
        raise ValueError("Panel ID42 is already used; preserve the existing dashboard")
    if any("panel-" + str(i) in elements for i in (43, 44, 45)):
        raise ValueError("Usage-quality panel IDs are already used")
    rows = spec["layout"]["spec"]["rows"]
    usage_rows = [r for r in rows if any(i["spec"].get("element", {}).get("name") == "panel-1"
                  for i in r["spec"]["layout"]["spec"].get("items", []))]
    if len(usage_rows) != 1:
        raise ValueError("Expected one device-usage row")
    def item(pid, x, y, width, height):
        return {"kind": "GridLayoutItem", "spec": {"x": x, "y": y, "width": width, "height": height,
                "element": {"kind": "ElementReference", "name": "panel-" + str(pid)}}}
    elements["panel-42"] = {"kind": "Panel", "spec": {"id": 42, "title": "Reporting Periods & Evidence",
        "description": "How periods, identity and received-log evidence affect usage totals.", "links": [],
        "data": {"kind": "QueryGroup", "spec": {"queries": [], "transformations": [], "queryOptions": {}}},
        "vizConfig": {"kind": "VizConfig", "group": "text", "version": "13.2.2", "spec": {
            "options": {"mode": "markdown", "content": GUIDE}, "fieldConfig": {"defaults": {}, "overrides": []}}}}}
    usage_items = usage_rows[0]["spec"]["layout"]["spec"]["items"]
    for existing in usage_items:
        existing["spec"]["y"] += 7
    usage_items.insert(0, item(42, 0, 0, 24, 7))
    quality_items = []
    for pid, (_, relative, title) in zip((43, 44, 45), PERIODS):
        panel = deepcopy(elements["panel-1"])
        p = panel["spec"]
        p.update(id=pid, title="Received Usage Records — " + title,
            description="Counts of received records, including exclusion reasons. Same relative period as the matching usage table. "
            "Selected VDOM plus explicitly unscoped missing-VDOM/parser-error records. Other known VDOMs excluded. "
            "Strict parsing flags malformed input; the unchanged usage formula may tolerate such input. "
            "Not traffic completeness, deduplication, or WAN availability. No results means unavailable evidence. "
            "Large scans can be expensive; this diagnostic row is collapsed by default.")
        p["data"]["spec"]["queries"][0]["spec"]["query"]["spec"]["expr"] = quality_query()
        p["data"]["spec"]["queryOptions"] = {"timeFrom": relative}
        p["data"]["spec"]["transformations"] = [{"kind": "Transformation", "group": "organize", "spec": {"options": {
            "excludeByName": {"Time": True}, "indexByName": {"usage_quality": 0, "Value #A": 1},
            "renameByName": {"usage_quality": "Record classification", "Value #A": "Received records"}}}}]
        p["vizConfig"]["spec"]["options"] = {"cellHeight": "md", "showHeader": True,
            "sortBy": [{"desc": True, "displayName": "Received records"}]}
        p["vizConfig"]["spec"]["fieldConfig"] = {"defaults": {"unit": "short", "decimals": 0, "mappings": [
            {"type": "value", "options": {k: {"text": text} for k, text in CATEGORIES.items()}}]}, "overrides": []}
        elements["panel-" + str(pid)] = panel
        quality_items.append(item(pid, 0, (pid - 43) * 9, 24, 9))
    rows.insert(rows.index(usage_rows[0]) + 1, {"kind": "RowsLayoutRow", "spec": {
        "title": "Usage Data Quality — Expand to inspect received records", "collapse": True,
        "hideHeader": False, "fillScreen": False, "layout": {"kind": "GridLayout", "spec": {"items": quality_items}}}})
    return result


def quality_query():
    # Existing byte selection for well-formed records, including zero and the
    # quoted logid gate. Diagnostics deliberately use stricter parsing.
    # Classification happens before clearing parser errors;
    # otherwise malformed input would disappear from the diagnostics.
    # Literal containment replaces the two-log-ID regexp. Nonempty strings
    # trimmed by the ASCII digit set are empty iff they contain only digits.
    # Local template variables avoid two intermediate label-format stages and
    # defer directional fallback until the record passes the cheap scope gates.
    category = (r'''{{if .__error__}}unscoped_parse_error{{else if not .vd}}unscoped_missing_vdom{{else if not (and (eq .type "traffic") (eq .subtype "forward"))}}excluded_type'''
        r'''{{else if or (not (or (eq .logid "0000000013") (eq .logid "0000000020"))) (not (or (contains "logid=\"0000000013\"" (__line__)) (contains "logid=\"0000000020\"" (__line__))))}}excluded_event'''
        r'''{{else if ne .dstintfrole "wan"}}excluded_role{{else if not .mastersrcmac}}excluded_missing_mac{{else}}'''
        r'''{{$tx := .sentdelta}}{{$rx := .rcvddelta}}{{if eq .logid "0000000013"}}{{if not $tx}}{{$tx = .sentbyte}}{{end}}{{if not $rx}}{{$rx = .rcvdbyte}}{{end}}{{end}}'''
        r'''{{if or (not $tx) (not $rx) (ne (trimAll "0123456789" $tx) "") (ne (trimAll "0123456789" $rx) "")}}excluded_invalid_bytes'''
        r'''{{else if or (not .sentdelta) (not .rcvddelta)}}included_cumulative_fallback{{else}}included_delta{{end}}{{end}}''')
    return ("sum by (usage_quality) (count_over_time(\n"
        '  {job="fortigate-syslog"}\n  | logfmt --strict\n'
        '  | vd="${vdom}" or vd="" or __error__!=""\n'
        '  | label_format usage_quality=`' + category + '`\n'
        '  | drop __error__, __error_details__\n'
        '  | keep usage_quality\n  [$__range]\n))')


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
