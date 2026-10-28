"""Conservative received-log accounting with an idempotent SQLite event ledger.

Raw logs remain in Loki. This module stores only allowlisted accounting fields.
Traffic completeness and exact packet-time allocation are never inferred.
"""
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from interfaces import map_source_interface

FIELDS = set("devid vd logid type subtype sessionid srcip dstip srcport dstport proto srcintf dstintf srcintfrole dstintfrole mastersrcmac srcmac srcname sentdelta rcvddelta sentbyte rcvdbyte duration durationdelta eventtime idseq".split())
UINT64 = 2**64 - 1
NS = 1000000000


def tz(name):
    if name == "UTC":
        return timezone.utc
    if name == "browser":
        raise ValueError("Accounting requires an explicit timezone")
    try:
        return ZoneInfo(name)
    except (KeyError, ValueError):
        raise ValueError("Accounting timezone unavailable; install IANA data or choose UTC") from None


def period_bounds(period, at, zone, *, start=None, end=None):
    at_ns = timestamp_ns(at)
    local = datetime.fromtimestamp(at_ns//NS, tz(zone))
    if period == "today":
        first = timestamp_ns(local.replace(hour=0, minute=0, second=0, microsecond=0).isoformat())
    elif period == "week":
        first = at_ns-168*3600*NS
    elif period == "recent":
        first = at_ns-300*NS
    elif period == "month":
        first = timestamp_ns(local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat())
    elif period == "custom":
        first, at_ns = timestamp_ns(start), timestamp_ns(end)
    else:
        raise ValueError("Unsupported accounting period")
    if first >= at_ns:
        raise ValueError("Report start must precede its end")
    return first, at_ns


def number(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,20}", value):
        return None
    value = int(value)
    return value if value <= UINT64 else None


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def timestamp_ns(value):
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise ValueError("An explicit-offset timestamp or integer nanoseconds is required")
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Timestamps require an explicit UTC offset")
    return int(stamp.timestamp()) * NS + stamp.microsecond * 1000


def iso(ns):
    return datetime.fromtimestamp(ns // NS, timezone.utc).isoformat()


def parse_line(line):
    if not isinstance(line, str) or len(line.encode()) > 65536:
        raise ValueError("Log line exceeds the normalized input budget")
    fields, pos = {}, 0
    while pos < len(line):
        if line[pos].isspace():
            pos += 1
            continue
        match = re.match(r"([A-Za-z_][A-Za-z0-9_.-]*)=", line[pos:])
        if not match:
            end = pos
            while end < len(line) and not line[end].isspace():
                end += 1
            token = line[pos:end]
            # RFC5424 prefixes and standalone logfmt keys contain no quotes or
            # equals sign. Malformed key/value pairs must not be skipped.
            if '"' in token or "=" in token:
                raise ValueError("Malformed logfmt field")
            pos = end
            continue
        key = match.group(1)
        pos += match.end()
        if pos < len(line) and line[pos] == '"':
            start, pos = pos, pos+1
            while pos < len(line):
                if line[pos] == "\\":
                    pos += 2
                    continue
                if line[pos] == '"':
                    break
                pos += 1
            if pos >= len(line):
                raise ValueError("Unterminated logfmt value")
            value = json.loads(line[start:pos+1])
            pos += 1
            if pos < len(line) and not line[pos].isspace():
                raise ValueError("Malformed quoted logfmt value")
        else:
            start = pos
            while pos < len(line) and not line[pos].isspace():
                pos += 1
            value = line[start:pos]
            if '"' in value or "=" in value:
                raise ValueError("Malformed unquoted logfmt value")
        if key in fields:
            raise ValueError("Duplicate log keys require review")
        fields[key] = value
    return {k: v for k, v in fields.items() if k in FIELDS}


def normalize(record, source_id):
    stamp = timestamp_ns(record.get("timestamp_ns", record.get("timestamp")))
    if stamp < 0 or stamp >= 2**63:
        raise ValueError("Timestamp outside the supported SQLite nanosecond range")
    raw = record.get("fields")
    fields = parse_line(record["line"]) if raw is None else raw
    if not isinstance(fields, dict) or not all(isinstance(k, str) and isinstance(v, str) and len(v) <= 4096 for k, v in fields.items()):
        raise ValueError("Normalized fields must be bounded strings")
    fields = {k: v for k, v in fields.items() if k in FIELDS}
    eventtime = fields.get("eventtime", "")
    event_ns = number(eventtime)
    if event_ns is not None and len(eventtime) <= 10:
        event_ns *= NS
    strong = event_ns is not None and 0 <= event_ns < 2**63
    event_ns = event_ns if strong else stamp
    uid = digest([source_id, event_ns, fields])
    session = [source_id, fields.get("devid", ""), fields.get("vd", ""), fields.get("sessionid", ""),
               *[fields.get(k, "") for k in ("srcintf", "srcip", "srcport", "dstip", "dstport", "proto")]]
    if not fields.get("sessionid"):
        session.append(uid)
    if fields.get("type") != "traffic" or fields.get("subtype") != "forward" or fields.get("logid") not in {"0000000013", "0000000020"}:
        session.append(uid)
    return {"uid": uid, "delivery_uid": digest([source_id, stamp, fields]), "source": source_id,
            "event_ns": event_ns, "record_ns": stamp, "fields": fields,
            "session": digest(session), "strong_event_time": strong}


def identity(event, generation):
    f = event["fields"]
    mac = next((f[k].lower() for k in ("mastersrcmac", "srcmac") if re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", f.get(k, "")) and f[k].lower() not in {"00:00:00:00:00:00", "ff:ff:ff:ff:ff:ff"} and not int(f[k][:2],16)&1), "")
    try:
        ip = str(ipaddress.ip_address(f.get("srcip", "")))
    except ValueError:
        ip = ""
    kind = "MAC" if mac else "IP_SESSION" if ip and f.get("sessionid") and number(f.get("duration")) is not None else "OBSERVATION"
    scope = [event["source"], f.get("devid", ""), f.get("vd", "")]
    key = scope + (["mac", mac] if mac else ["session", event["session"], generation] if kind == "IP_SESSION" else ["observation", event["uid"]])
    return {"device_id": digest(key), "identity_kind": kind, "mac": mac, "ip": ip,
            "hostname": f.get("srcname", ""), "source_interface": f.get("srcintf", ""), "vdom": f.get("vd", "")}


def direction(fields, inventory, config):
    """Outbound internal-origin sessions only; response bytes are downloads."""
    vdom, src, dst = (fields.get(k, "") for k in ("vd", "srcintf", "dstintf"))
    source = map_source_interface(inventory, vdom, src)
    target = map_source_interface(inventory, vdom, dst)
    wan = set(config.get("confirmed_wan_interfaces", []))
    src_role, dst_role = fields.get("srcintfrole"), fields.get("dstintfrole")
    if src in wan or src_role == "wan" or source["role"] == "wan":
        return "EXTERNAL_OR_TRANSIT", source
    if dst_role in {"lan", "dmz"} or target["role"] in {"lan", "dmz"}:
        return "INTERNAL", source
    if source["status"] == "AMBIGUOUS" or target["status"] == "AMBIGUOUS":
        return "UNRESOLVED_DIRECTION", source
    internal = src_role in {"lan", "dmz"} or source["role"] in {"lan", "dmz"}
    external = dst in wan or dst_role == "wan" or target["role"] == "wan"
    return ("INTERNET" if internal and external else "UNRESOLVED_DIRECTION"), source


def derive_session(events, inventory, config):
    zone = tz(config["accounting_timezone"])
    previous, ended, generation, previous_time, previous_duration = {}, False, None, None, None
    previous_start, previous_mac = None, None
    results = []
    for event in sorted(events, key=lambda e: (e["event_ns"], e["uid"])):
        f, flags = event["fields"], []
        stamp = event["event_ns"]
        cumulative = {k: number(f.get(k)) for k in ("sentbyte", "rcvdbyte")}
        duration = number(f.get("duration"))
        inferred_start = stamp//NS-duration if duration is not None else None
        current_mac = identity(event, event['uid'])['mac']
        mac_changed = bool(previous_mac and current_mac and previous_mac != current_mac)
        reset = ended or mac_changed or (duration is not None and previous_duration is not None and duration < previous_duration)
        # Missing-MAC endpoint identities cannot be carried across inconsistent
        # session-start evidence just because an IP/session number was reused.
        if not current_mac and previous_start is not None and inferred_start != previous_start:
            reset = True
        reset = reset or any(cumulative[k] is not None and previous.get(k) is not None and cumulative[k] < previous[k] for k in cumulative)
        if generation is None or reset:
            generation, previous, previous_time = event.get("generation_seed", event["uid"]) if not reset else event["uid"], {}, None
        if reset:
            flags.append("session_restart_or_counter_reset")
        if mac_changed:
            flags.append('session_endpoint_changed')
        endpoint = identity(event, generation)
        evidence = event.get("network_evidence")
        cls, vlan = (evidence["classification"], evidence["vlan"]) if evidence else direction(f, inventory, config)
        if evidence:
            flags.extend(evidence.get("flags", []))
        if f.get("vd") != config.get("vdom") or (config.get("expected_devid") and f.get("devid") != config["expected_devid"]):
            cls = "UNSCOPED_SOURCE"
        if f.get("type") != "traffic" or f.get("subtype") != "forward" or f.get("logid") not in {"0000000013", "0000000020"}:
            cls = "UNSUPPORTED_RECORD"
        if not f.get("devid"):
            flags.append("source_device_id_missing")
        if not config.get("expected_devid"):
            flags.append("source_binding_not_confirmed")
        if not event["strong_event_time"]:
            flags.append("event_identity_uses_loki_timestamp")
        if abs(stamp-event["record_ns"]) > 300*NS:
            flags.append("source_timestamp_skew")
        if endpoint["identity_kind"] != "MAC":
            flags.append("physical_device_identity_unverified")
        if vlan["status"] not in {"MAPPED", "NOT_VLAN"}:
            flags.append("vlan_mapping_" + vlan["status"].lower())
        interval = number(f.get("durationdelta"))
        interval_start = stamp-interval*NS if interval is not None else previous_time
        if interval_start is None and duration is not None:
            interval_start = stamp-duration*NS
        if interval_start is None:
            flags.append("contribution_interval_unknown")
        elif datetime.fromtimestamp(max(0, interval_start)//NS, zone).date() != datetime.fromtimestamp(stamp//NS, zone).date():
            flags.append("calendar_boundary_allocation_uncertain")
        amounts, pending = {}, {}
        for label, delta, counter in (("upload", "sentdelta", "sentbyte"), ("download", "rcvddelta", "rcvdbyte")):
            value, waiting = 0, 0
            raw, total, prior = f.get(delta, ""), cumulative[counter], previous.get(counter)
            if raw != "":
                value = number(raw)
                if value is None:
                    value = 0
                    flags.append(label + "_invalid_delta")
                elif total is not None and prior is not None:
                    difference = total-prior
                    if value > difference:
                        waiting, value = value, 0
                        flags.append(label + "_delta_exceeds_counter_change")
                    elif value < difference:
                        flags.append(label + "_unobserved_counter_bytes")
            elif f.get("logid") == "0000000013" and total is not None:
                if prior is not None:
                    value = total-prior
                    flags.append(label + "_baseline_fallback")
                elif not mac_changed and previous_time is None and duration is not None and interval_start is not None and datetime.fromtimestamp(max(0, interval_start)//NS, zone).date() == datetime.fromtimestamp(stamp//NS, zone).date():
                    value = total
                    flags.append(label + "_end_only_session")
                else:
                    waiting = total
                    flags.append(label + "_cumulative_without_safe_baseline")
            else:
                flags.append(label + "_contribution_missing")
            amounts[label], pending[label] = (value, waiting) if cls == "INTERNET" else (0, 0)
            if total is not None:
                previous[counter] = total
        if cls != "INTERNET":
            flags.append(cls.lower())
        if event.get("anchor"):
            cls = "BASELINE_ANCHOR"
        fact = {"uid": event["uid"], "generation_seed": generation, "event_ns": stamp, "day": datetime.fromtimestamp(stamp//NS, zone).date().isoformat(),
                **endpoint, "vlan": vlan, "wan": f.get("dstintf", ""), "classification": cls,
                "upload_bytes": amounts["upload"], "download_bytes": amounts["download"],
                "pending_upload_bytes": pending["upload"], "pending_download_bytes": pending["download"],
                "flags": sorted(set(flags))}
        results.append(fact)
        previous_time, previous_duration, previous_start, previous_mac, ended = stamp, duration, inferred_start, current_mac, f.get("logid") == "0000000013"
    return results


class Store:
    def __init__(self, path, config, inventory=None):
        if config["accounting_timezone"] == "browser":
            raise ValueError("Accounting requires an explicit IANA timezone")
        tz(config["accounting_timezone"])
        self.config, self.inventory = dict(config), inventory or {"interfaces": []}
        self.db = sqlite3.connect(str(path), timeout=20, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA cache_size=-2048;
        CREATE TABLE IF NOT EXISTS events(uid TEXT PRIMARY KEY, session TEXT NOT NULL, stamp INTEGER NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_session ON events(session,stamp);
        CREATE TABLE IF NOT EXISTS deliveries(uid TEXT PRIMARY KEY,event_uid TEXT NOT NULL,stamp INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS facts(uid TEXT PRIMARY KEY,session TEXT NOT NULL,stamp INTEGER NOT NULL,payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS facts_session ON facts(session);
        CREATE INDEX IF NOT EXISTS facts_stamp ON facts(stamp);
        CREATE TABLE IF NOT EXISTS daily(day TEXT NOT NULL,device TEXT NOT NULL,vlan TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(day,device,vlan));
        CREATE TABLE IF NOT EXISTS quality(day TEXT NOT NULL,classification TEXT NOT NULL,records INTEGER NOT NULL,first_stamp INTEGER NOT NULL,last_stamp INTEGER NOT NULL,PRIMARY KEY(day,classification));
        CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        contract = {k: config.get(k) for k in ("accounting_timezone", "source_id", "vdom", "expected_devid")}
        existing = self.state("contract")
        if existing is not None and existing != contract:
            self.db.close()
            raise ValueError("Accounting identity/timezone contract changed; use an explicitly rebuilt database")
        with self.db:
            self.set_state("contract", contract)

    def close(self):
        self.db.close()

    def state(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key, value):
        self.db.execute("INSERT INTO state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def _rollup(self, fact, sign):
        if fact['classification']=='BASELINE_ANCHOR':
            return
        self.db.execute('''INSERT INTO quality VALUES(?,?,?,?,?) ON CONFLICT(day,classification)
            DO UPDATE SET records=records+excluded.records,first_stamp=MIN(first_stamp,excluded.first_stamp),last_stamp=MAX(last_stamp,excluded.last_stamp)''',
            (fact['day'],fact['classification'],sign,fact['event_ns'],fact['event_ns']))
        self.db.execute('DELETE FROM quality WHERE day=? AND classification=? AND records<=0',(fact['day'],fact['classification']))
        if fact["classification"] != "INTERNET":
            return
        key = (fact["day"], fact["device_id"], fact["vlan"]["key"])
        old = self.db.execute("SELECT payload FROM daily WHERE day=? AND device=? AND vlan=?", key).fetchone()
        value = json.loads(old[0]) if old else {**fact, "records": 0, "upload_bytes": 0, "download_bytes": 0,
            "pending_upload_bytes": 0, "pending_download_bytes": 0, "flag_counts": {}, "first_event_ns": fact["event_ns"]}
        for column in ("upload_bytes", "download_bytes", "pending_upload_bytes", "pending_download_bytes"):
            value[column] += sign*fact[column]
        value["records"] += sign
        if sign > 0:
            value["first_event_ns"] = min(value.get("first_event_ns", fact["event_ns"]), fact["event_ns"])
        for flag in fact["flags"]:
            value["flag_counts"][flag] = value["flag_counts"].get(flag, 0)+sign
        value["flag_counts"] = {k: v for k, v in value["flag_counts"].items() if v}
        if sign > 0 and fact["event_ns"] >= value["event_ns"]:
            for column in ("event_ns", "hostname", "ip", "mac", "source_interface", "identity_kind"):
                value[column] = fact[column]
        if value["records"] <= 0:
            self.db.execute("DELETE FROM daily WHERE day=? AND device=? AND vlan=?", key)
        else:
            self.db.execute("INSERT OR REPLACE INTO daily VALUES(?,?,?,?)", (*key, json.dumps(value)))

    def ingest(self, records, *, checkpoint=None):
        if len(records) > 30000:
            raise ValueError("Batch exceeds bounded ingestion size")
        normalized, rejected = [], []
        for record in records:
            try:
                normalized.append(normalize(record, self.config.get("source_id", "dedicated-listener")))
            except (ValueError, TypeError, KeyError):
                rejected.append(digest([self.config.get("source_id"), record]))
        affected, inserted, repeated = set(), 0, 0
        with self.db:
            for uid in rejected:
                new = self.db.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?,?)", (uid, "invalid", checkpoint or 0)).rowcount
                if new:
                    self.set_state("parser_rejections", self.state("parser_rejections", 0)+1)
            for event in normalized:
                new_delivery = self.db.execute("INSERT OR IGNORE INTO deliveries VALUES(?,?,?)", (event["delivery_uid"], event["uid"], event["record_ns"])).rowcount
                if event["event_ns"] < self.state("ledger_floor_ns", 0):
                    if new_delivery:
                        self.set_state("late_rejections", self.state("late_rejections", 0)+1)
                    continue
                cls, vlan = direction(event["fields"], self.inventory, self.config)
                evidence_flags = []
                collected = self.inventory.get("collected_at")
                if collected and event["event_ns"] < timestamp_ns(collected):
                    evidence_flags.append("historical_vlan_mapping_not_verified")
                    vlan = {**vlan, "status": "UNKNOWN", "vlan_id": None, "key": "unknown:"+vlan["interface"]}
                    # A current inventory cannot establish historical direction.
                    # Contemporaneous roles in the record may still establish it.
                    cls, _ = direction(event['fields'], {'interfaces': []}, {**self.config, 'confirmed_wan_interfaces': []})
                    if cls == 'UNRESOLVED_DIRECTION':
                        evidence_flags.append('historical_direction_not_verified')
                event["network_evidence"] = {"classification": cls, "vlan": vlan, "flags": evidence_flags}
                new_event = self.db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?)", (event["uid"], event["session"], event["event_ns"], json.dumps(event))).rowcount
                inserted += new_event
                repeated += bool(new_delivery and not new_event)
                if new_event:
                    affected.add(event["session"])
            for session in affected:
                rows = self.db.execute("SELECT payload FROM events WHERE session=? ORDER BY stamp,uid LIMIT 10001", (session,)).fetchall()
                if len(rows) > 10000:
                    raise ValueError("Session exceeds safe recomputation size; checkpoint not advanced")
                for old in self.db.execute("SELECT payload FROM facts WHERE session=?", (session,)).fetchall():
                    self._rollup(json.loads(old[0]), -1)
                self.db.execute("DELETE FROM facts WHERE session=?", (session,))
                for fact in derive_session([json.loads(x[0]) for x in rows], self.inventory, self.config):
                    self.db.execute("INSERT INTO facts VALUES(?,?,?,?)", (fact["uid"], session, fact["event_ns"], json.dumps(fact)))
                    self._rollup(fact, 1)
            self.set_state("replay_candidates", self.state("replay_candidates", 0)+repeated)
            if checkpoint is not None:
                self.set_state("checkpoint_ns", checkpoint)
        return {"new_events": inserted, "new_replay_candidates": repeated, "rejected_input_records": len(rejected), "affected_sessions": len(affected)}

    def prune(self, now, ledger_days=8, summary_days=400):
        if not 8 <= ledger_days <= 365 or not ledger_days <= summary_days <= 3650:
            raise ValueError("Ledger retention must cover the rolling week; summary retention must cover the ledger")
        floor = timestamp_ns(now)-ledger_days*86400*NS
        with self.db:
            sessions = self.db.execute("SELECT DISTINCT session FROM events WHERE stamp<?", (floor,)).fetchall()
            for session in sessions:
                last = self.db.execute("SELECT uid,payload FROM events WHERE session=? AND stamp<? ORDER BY stamp DESC,uid DESC LIMIT 1", (session[0], floor)).fetchone()
                event = json.loads(last[1])
                # Keep one baseline for an open session, without contributing its
                # bytes to a retained daily summary a second time.
                keep = event["fields"].get("logid") == "0000000020"
                self.db.execute("DELETE FROM events WHERE session=? AND stamp<? AND uid!=?", (session[0], floor, last[0] if keep else ""))
                if keep:
                    fact = self.db.execute("SELECT payload FROM facts WHERE uid=?", (last[0],)).fetchone()
                    event.update(anchor=True, generation_seed=json.loads(fact[0])["generation_seed"] if fact else event.get("generation_seed", event["uid"]))
                    self.db.execute("UPDATE events SET payload=? WHERE uid=?", (json.dumps(event), last[0]))
            self.db.execute("DELETE FROM facts WHERE stamp<?", (floor,))
            self.db.execute("DELETE FROM deliveries WHERE stamp<?", (floor,))
            cutoff = datetime.fromtimestamp((timestamp_ns(now)-summary_days*86400*NS)//NS, tz(self.config["accounting_timezone"])).date().isoformat()
            self.db.execute("DELETE FROM daily WHERE day<?", (cutoff,))
            self.db.execute('DELETE FROM quality WHERE day<?',(cutoff,))
            self.set_state("ledger_floor_ns", max(floor, self.state("ledger_floor_ns", 0)))
            self.set_state("summary_floor_day", cutoff)

    def range_rows(self, start, end):
        zone = tz(self.config["accounting_timezone"])
        start_local = datetime.fromtimestamp(start//NS, zone)
        end_local = datetime.fromtimestamp(end//NS, zone)
        first_full = start_local.date() + (timedelta(days=1) if (start_local.hour or start_local.minute or start_local.second or start % NS) else timedelta())
        last_full_end = end_local.date()
        rows = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM daily WHERE day>=? AND day<?", (first_full.isoformat(), last_full_end.isoformat()))]
        middle_start = timestamp_ns(datetime.combine(first_full, datetime.min.time(), zone).isoformat())
        middle_end = timestamp_ns(datetime.combine(last_full_end, datetime.min.time(), zone).isoformat())
        edges = [(start, min(end, middle_start)), (max(start, middle_end, middle_start), end)]
        for a, b in edges:
            if a < b:
                if a < self.state("ledger_floor_ns", 0):
                    raise ValueError("Partial-day detail expired; request whole calendar days or increase ledger retention before collection")
                day = datetime.fromtimestamp(a//NS, zone).date().isoformat()
                candidates = [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM daily WHERE day=?", (day,))]
                included = set()
                partial = False
                for value in candidates:
                    first, last = value.get("first_event_ns", -1), value["event_ns"]
                    if a <= first and last < b:
                        rows.append(value)
                        included.add((value["device_id"], value["vlan"]["key"]))
                    elif last >= a and first < b:
                        partial = True
                if partial:
                    for item in self.db.execute("SELECT payload FROM facts WHERE stamp>=? AND stamp<?", (a, b)):
                        value = json.loads(item[0])
                        if value["classification"] == "INTERNET" and (value["device_id"], value["vlan"]["key"]) not in included:
                            rows.append({**value, "records": 1, "flag_counts": {k: 1 for k in value["flags"]}})
        return rows

    def report(self, start, end, *, vlan=None, device=None, search=""):
        start, end = timestamp_ns(start), timestamp_ns(end)
        if start >= end:
            raise ValueError("Report start must precede its end")
        grouped, flags = {}, defaultdict(int)
        for row in self.range_rows(start, end):
            if vlan and row["vlan"]["key"] != vlan or device and row["device_id"] != device:
                continue
            if search and search.casefold() not in " ".join(str(row.get(k, "")) for k in ("ip", "mac", "hostname", "device_id")).casefold():
                continue
            key = row["device_id"], row["vlan"]["key"]
            if key not in grouped:
                grouped[key] = {**row, "upload_bytes": 0, "download_bytes": 0, "pending_upload_bytes": 0, "pending_download_bytes": 0, "records": 0, "flag_counts": {}}
            target = grouped[key]
            if row["event_ns"] > target["event_ns"]:
                for column in ("event_ns", "hostname", "ip", "mac", "source_interface"):
                    target[column] = row[column]
            for column in ("upload_bytes", "download_bytes", "pending_upload_bytes", "pending_download_bytes", "records"):
                target[column] += row[column]
            for flag, count in row["flag_counts"].items():
                flags[flag] += count
                target["flag_counts"][flag] = target["flag_counts"].get(flag, 0)+count
        rows = sorted(grouped.values(), key=lambda x: x["upload_bytes"]+x["download_bytes"], reverse=True)
        totals = {column: sum(x[column] for x in rows) if rows else None for column in ("upload_bytes", "download_bytes", "pending_upload_bytes", "pending_download_bytes")}
        vlans, devices = {}, {}
        for entry in rows:
            for grouping, key in ((vlans, entry["vlan"]["key"]), (devices, entry["device_id"])):
                if key not in grouping:
                    grouping[key] = {**entry, "upload_bytes": 0, "download_bytes": 0,
                        "pending_upload_bytes": 0, "pending_download_bytes": 0, "records": 0,
                        "device_ids": set(), "vlans": {}, "source_interfaces": set()}
                target = grouping[key]
                for column in ("upload_bytes", "download_bytes", "pending_upload_bytes", "pending_download_bytes", "records"):
                    target[column] += entry[column]
                target["device_ids"].add(entry["device_id"])
                target["vlans"][entry["vlan"]["key"]] = entry["vlan"]
                target["source_interfaces"].add(entry["source_interface"])
                if entry["event_ns"] > target["event_ns"]:
                    for column in ("event_ns", "hostname", "ip", "mac"):
                        target[column] = entry[column]
        for grouping in (vlans, devices):
            for entry in grouping.values():
                entry["observed_endpoint_identities"] = len(entry.pop("device_ids"))
                entry["source_interfaces"] = sorted(entry["source_interfaces"])
                entry["vlans"] = list(entry["vlans"].values())
                entry["total_bytes"] = entry["upload_bytes"]+entry["download_bytes"]
        return {"start_utc": iso(start), "end_utc": iso(end), "boundary": "[start,end)", "timezone": self.config["accounting_timezone"],
            'source_record_classifications': self.classifications(start,end),
            "rows": rows, "devices": sorted(devices.values(), key=lambda x: x["total_bytes"], reverse=True),
            "vlans": sorted(vlans.values(), key=lambda x: x["total_bytes"], reverse=True),
            "totals": totals, "observed_endpoint_identities": len({x["device_id"] for x in rows}),
            "unverified_endpoint_identities": len({x["device_id"] for x in rows if x["identity_kind"] != "MAC"}),
            "flags": dict(flags), "traffic_coverage": "UNKNOWN", "status": "RECORDED_ONLY" if rows else "UNAVAILABLE",
            "note": "Received-log accounting, not certified billing. Bytes follow event timestamps; cross-boundary intervals and missing source traffic remain uncertain."}

    def classifications(self,start,end):
        """Source-wide received counts, explicitly independent of device filters."""
        zone=tz(self.config['accounting_timezone'])
        first=datetime.fromtimestamp(start//NS,zone).date().isoformat()
        last=datetime.fromtimestamp((end-1)//NS,zone).date().isoformat()
        counts=defaultdict(int)
        for row in self.db.execute('SELECT day,classification,records,first_stamp,last_stamp FROM quality WHERE day>=? AND day<=?',(first,last)):
            if start<=row[3] and row[4]<end:
                counts[row[1]]+=row[2]
            else:
                boundary=timestamp_ns(datetime.fromisoformat(row[0]).replace(tzinfo=zone).isoformat())
                stop=timestamp_ns((datetime.fromisoformat(row[0])+timedelta(days=1)).replace(tzinfo=zone).isoformat())
                # Expired partial-day selections were already refused by the
                # main report path. Count retained facts at the exact edges.
                count=self.db.execute("SELECT COUNT(*) FROM facts WHERE stamp>=? AND stamp<? AND json_extract(payload,'$.classification')=?",
                    (max(start,boundary),min(end,stop),row[1])).fetchone()[0]
                counts[row[1]]+=count
        return dict(counts)

    def history(self, device, *, months=False, vlan=None, limit=400):
        if not re.fullmatch(r"[a-f0-9]{64}", device) or not 1 <= limit <= 3650:
            raise ValueError("Invalid device history selection")
        rows = self.db.execute("SELECT day,payload FROM daily WHERE device=? ORDER BY day DESC LIMIT 10000", (device,)).fetchall()
        values = {}
        for day, payload in rows:
            row = json.loads(payload)
            if vlan and row["vlan"]["key"] != vlan:
                continue
            key = day[:7] if months else day
            if key not in values:
                values[key] = {"period": key, "upload_bytes": 0, "download_bytes": 0, "pending_upload_bytes": 0, "pending_download_bytes": 0,
                               "traffic_coverage": "UNKNOWN", "observed_days": set()}
            for column in ("upload_bytes", "download_bytes", "pending_upload_bytes", "pending_download_bytes"):
                values[key][column] += row[column]
            values[key]["observed_days"].add(day)
        result = []
        for key in sorted(values)[-limit:]:
            value = values[key]
            value["observed_days"] = len(value["observed_days"])
            result.append(value)
        return result

    def diagnostics(self):
        row = self.db.execute("SELECT MIN(stamp),MAX(stamp) FROM facts WHERE stamp>=?", (self.state("ledger_floor_ns", 0),)).fetchone()
        delivery = self.db.execute("SELECT MAX(stamp) FROM deliveries").fetchone()[0]
        return {"checkpoint_ns": self.state("checkpoint_ns"), "last_received_event_ns": row[1], "first_retained_event_ns": row[0],
                "last_delivery_ns": delivery,
                "replay_candidates": self.state("replay_candidates", 0), "late_rejections": self.state("late_rejections", 0),
                "parser_rejections": self.state("parser_rejections", 0),
                "ledger_floor_ns": self.state("ledger_floor_ns"), "summary_floor_day": self.state("summary_floor_day"),
                "traffic_coverage": "UNKNOWN", "source_identity_bound": bool(self.config.get("expected_devid")),
                "inventory_available": bool(self.inventory.get("interfaces"))}
