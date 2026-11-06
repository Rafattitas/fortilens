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
from accounting_storage import detect_storage_format, open_ledger

FIELDS = set("devid vd logid type subtype sessionid srcip dstip srcport dstport proto srcintf dstintf srcintfrole dstintfrole mastersrcmac srcmac srcname sentdelta rcvddelta sentbyte rcvdbyte duration durationdelta eventtime idseq".split())
# Descriptive labels kept beside a record. They never enter event or delivery
# identities, so a record re-read across an upgrade keeps its identity and is
# never counted twice. They never change byte arithmetic either.
META_FIELDS = frozenset("app appcat apprisk dstcountry osname srchwvendor devtype".split())
APP_SCHEMA = """CREATE TABLE IF NOT EXISTS app_daily(day TEXT NOT NULL,device TEXT NOT NULL,vlan TEXT NOT NULL,
app TEXT NOT NULL,category TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(day,device,vlan,app,category))"""
# Latest FortiGate device-detection labels per endpoint identity. Descriptive
# only: they never merge identities or change any byte value.
PROFILE_SCHEMA = "CREATE TABLE IF NOT EXISTS device_profile(device TEXT PRIMARY KEY,payload TEXT NOT NULL)"
APP_INDEXES = ("CREATE INDEX IF NOT EXISTS app_daily_app ON app_daily(app,category,day)",
               "CREATE INDEX IF NOT EXISTS app_daily_category ON app_daily(category,day)")
PROFILE_LABELS = {"osname": "os", "srchwvendor": "vendor", "devtype": "type"}
# FortiGate reports these when it saw only the protocol, not the application.
GENERIC_APP = re.compile(r"(?:QUIC|SSL|SSL_TLSv1\.[0-3]|TLS|HTTPS?\.BROWSER)", re.IGNORECASE)
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
        # Today plus the six previous whole calendar days. Whole days come from
        # retained daily summaries, so the week never depends on old raw records.
        first = timestamp_ns(datetime.combine(local.date()-timedelta(days=6), datetime.min.time(), tz(zone)).isoformat())
    elif period == "recent":
        first = at_ns-300*NS
    elif period == "month":
        first = timestamp_ns(local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat())
    elif period == "year":
        first = timestamp_ns(local.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).isoformat())
    elif period == "custom":
        first, at_ns = timestamp_ns(start), timestamp_ns(end)
    else:
        raise ValueError("Unsupported accounting period")
    if first >= at_ns:
        raise ValueError("Report start must precede its end")
    return first, at_ns


def unsupported(fields):
    """Records outside forward-traffic accounting never contribute bytes."""
    return (fields.get("type") != "traffic" or fields.get("subtype") != "forward"
            or fields.get("logid") not in {"0000000013", "0000000020"})


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
    return {k: v for k, v in fields.items() if k in FIELDS or k in META_FIELDS}


def normalize(record, source_id):
    stamp = timestamp_ns(record.get("timestamp_ns", record.get("timestamp")))
    if stamp < 0 or stamp >= 2**63:
        raise ValueError("Timestamp outside the supported SQLite nanosecond range")
    raw = record.get("fields")
    fields = parse_line(record["line"]) if raw is None else raw
    if not isinstance(fields, dict) or not all(isinstance(k, str) and isinstance(v, str) and len(v) <= 4096 for k, v in fields.items()):
        raise ValueError("Normalized fields must be bounded strings")
    meta = {k: v for k, v in fields.items() if k in META_FIELDS and v}
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
    if unsupported(fields):
        session.append(uid)
    event = {"uid": uid, "delivery_uid": digest([source_id, stamp, fields]), "source": source_id,
             "event_ns": event_ns, "record_ns": stamp, "fields": fields,
             "session": digest(session), "strong_event_time": strong}
    if meta and not unsupported(fields):
        event["meta"] = meta
    return event


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


def event_order(event):
    f=event['fields']
    return (event['event_ns'],f.get('logid')=='0000000013',number(f.get('duration')) or 0,
            (number(f.get('sentbyte')) or 0)+(number(f.get('rcvdbyte')) or 0),event['uid'])


def derive_session(events, inventory, config):
    zone = tz(config["accounting_timezone"])
    previous, ended, generation, previous_time, previous_duration = {}, False, None, None, None
    previous_start, previous_mac = None, None
    results = []
    for event in sorted(events, key=event_order):
        f, flags = event["fields"], []
        stamp = event["event_ns"]
        cumulative = {k: number(f.get(k)) for k in ("sentbyte", "rcvdbyte")}
        duration = number(f.get("duration"))
        inferred_start = stamp//NS-duration if duration is not None else None
        current_mac = identity(event, event['uid'])['mac']
        mac_changed = bool(previous_mac and current_mac and previous_mac != current_mac)
        repeated_close=ended and f.get('logid')=='0000000013' and inferred_start is not None and inferred_start==previous_start
        reset = (ended and not repeated_close) or mac_changed or (duration is not None and previous_duration is not None and duration < previous_duration)
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
        if repeated_close:
            flags.append('repeated_close_observation')
        endpoint = identity(event, generation)
        evidence = event.get("network_evidence")
        cls, vlan = (evidence["classification"], evidence["vlan"]) if evidence else direction(f, inventory, config)
        if evidence:
            flags.extend(evidence.get("flags", []))
        if f.get("vd") != config.get("vdom") or (config.get("expected_devid") and f.get("devid") != config["expected_devid"]):
            cls = "UNSCOPED_SOURCE"
        if unsupported(f):
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
                elif repeated_close and not reset:
                    waiting,value=value,0
                    flags.append(label+'_post_close_delta_unverified')
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
    def __init__(self, path, config, inventory=None, *, storage_format=None):
        if config["accounting_timezone"] == "browser":
            raise ValueError("Accounting requires an explicit IANA timezone")
        tz(config["accounting_timezone"])
        self.config, self.inventory = dict(config), inventory or {"interfaces": []}
        self.path = None if str(path) == ':memory:' else Path(path).resolve()
        self.db = sqlite3.connect(str(path), timeout=20, check_same_thread=False)
        try:
            self.db.row_factory = sqlite3.Row
            selected_format = detect_storage_format(self.db, storage_format)
            self.db.executescript('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA journal_size_limit=134217728; PRAGMA cache_size=-2048; PRAGMA foreign_keys=ON;')
            self.ledger = open_ledger(self.db, storage_format=selected_format)
            self.storage_format = self.ledger.format
            contract = {k: config.get(k) for k in ("accounting_timezone", "source_id", "vdom", "expected_devid")}
            existing = self.state("contract")
            if existing is not None and existing != contract:
                raise ValueError("Accounting identity/timezone contract changed; use an explicitly rebuilt database")
            with self.db:
                self.db.execute(APP_SCHEMA)
                self.db.execute(PROFILE_SCHEMA)
                for statement in APP_INDEXES:
                    self.db.execute(statement)
                self.set_state("contract", contract)
                # Rebuild this optional derived observation once on writer open,
                # including after an older version or an offline DB restore.
                self.set_state('delivery_max_ns', self.ledger.max_delivery_stamp())
        except Exception:
            # Construction did not return a usable Store to its caller. Close
            # its connection, retaining the original failure if close also fails.
            try:self.db.close()
            except Exception:pass
            raise

    def close(self):
        self.db.close()

    @classmethod
    def open_readonly(cls, path, config, inventory=None, *, storage_format=None):
        """Read an existing format without initializing schema or writer state."""
        if str(path) == ':memory:':
            raise ValueError('Read-only storage requires an existing file-backed database')
        if config['accounting_timezone'] == 'browser':
            raise ValueError('Accounting requires an explicit IANA timezone')
        tz(config['accounting_timezone'])
        reader = object.__new__(cls)
        reader.config, reader.inventory = dict(config), inventory or {'interfaces': []}
        reader.path = Path(path).resolve()
        reader.db = sqlite3.connect(reader.path.as_uri()+'?mode=ro', uri=True, timeout=2,
                                    check_same_thread=False)
        try:
            reader.db.row_factory = sqlite3.Row
            reader.db.execute('PRAGMA query_only=ON')
            reader.db.execute('PRAGMA cache_size=-2048')
            reader.ledger = open_ledger(reader.db, storage_format=storage_format, initialize=False)
            reader.storage_format = reader.ledger.format
            contract = {key: config.get(key) for key in ('accounting_timezone', 'source_id', 'vdom', 'expected_devid')}
            existing = reader.state('contract')
            if existing is not None and existing != contract:
                raise ValueError('Accounting identity/timezone contract changed; use an explicitly rebuilt database')
        except Exception:
            reader.db.close()
            raise
        return reader

    def reader(self):
        """Open an independent, non-migrating reader of the existing WAL store.

        Never call the writer constructor here: reports cannot create tables,
        update the contract or write state. In-memory callers keep the serialized
        compatibility path because an ordinary :memory: DB cannot be reopened.
        """
        if self.path is None:
            return None
        return self.open_readonly(self.path, self.config, self.inventory,
                                  storage_format=self.storage_format)

    def state(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key, value):
        self.db.execute("INSERT INTO state VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, json.dumps(value)))

    def delivery_max(self):
        missing = object()
        maximum = self.state('delivery_max_ns', missing)
        return self.ledger.max_delivery_stamp() if maximum is missing else maximum

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

    def _app_rollup(self, fact, labels, sign):
        """Maintain day × device × VLAN × application totals beside `daily`.

        Labels come from the fact's own record. Bytes are exactly the fact's, so
        application totals add up to device totals for the days they cover.
        """
        if fact["classification"] != "INTERNET":
            return
        if sign > 0 and labels:
            self._remember_labels(fact, labels)
        key = (fact["day"], fact["device_id"], fact["vlan"]["key"], labels.get("app", ""), labels.get("appcat", ""))
        old = self.db.execute("SELECT payload FROM app_daily WHERE day=? AND device=? AND vlan=? AND app=? AND category=?", key).fetchone()
        value = json.loads(old[0]) if old else {"vlan": fact["vlan"], "records": 0, "upload_bytes": 0, "download_bytes": 0,
            "pending_upload_bytes": 0, "pending_download_bytes": 0, "risk": "", "event_ns": fact["event_ns"],
            "hostname": fact["hostname"], "ip": fact["ip"], "mac": fact["mac"], "identity_kind": fact["identity_kind"]}
        for column in ("upload_bytes", "download_bytes", "pending_upload_bytes", "pending_download_bytes"):
            value[column] += sign*fact[column]
        value["records"] += sign
        if sign > 0 and fact["event_ns"] >= value["event_ns"]:
            for column in ("event_ns", "hostname", "ip", "mac", "identity_kind"):
                value[column] = fact[column]
            value["risk"] = labels.get("apprisk", value["risk"])
        if value["records"] <= 0:
            self.db.execute("DELETE FROM app_daily WHERE day=? AND device=? AND vlan=? AND app=? AND category=?", key)
        else:
            self.db.execute("INSERT OR REPLACE INTO app_daily VALUES(?,?,?,?,?,?)", (*key, json.dumps(value)))

    def _remember_labels(self, fact, labels):
        """Record when labelled evidence starts and each endpoint's latest profile."""
        first = self.state("applications_first_ns")
        if first is None or fact["event_ns"] < first:
            self.set_state("applications_first_ns", fact["event_ns"])
        profile = {name: labels[key] for key, name in PROFILE_LABELS.items() if labels.get(key)}
        if not profile:
            return
        old = self.db.execute("SELECT payload FROM device_profile WHERE device=?", (fact["device_id"],)).fetchone()
        if old and json.loads(old[0])["event_ns"] > fact["event_ns"]:
            return
        self.db.execute("INSERT OR REPLACE INTO device_profile VALUES(?,?)",
                        (fact["device_id"], json.dumps({**profile, "event_ns": fact["event_ns"]})))

    def _has_table(self, name):
        # A reader of a file not yet opened by this version has no such table.
        return self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def _app_table(self):
        return self._has_table("app_daily")

    def _with_profiles(self, rows):
        found = self.profiles(row["device_id"] for row in rows)
        for row in rows:
            row["profile"] = found.get(row["device_id"])
        return rows

    def profiles(self, devices):
        """Latest FortiGate OS/vendor/type labels for the given endpoint identities."""
        if not devices or not self._has_table("device_profile"):
            return {}
        result, devices = {}, list(devices)
        for index in range(0, len(devices), 500):
            page = devices[index:index+500]
            for device, payload in self.db.execute(
                    "SELECT device,payload FROM device_profile WHERE device IN (" + ",".join("?"*len(page)) + ")", page):
                value = json.loads(payload)
                value.pop("event_ns", None)
                result[device] = value
        return result

    def app_history(self, app=None, category=None, *, device=None, vlan=None, months=False, limit=400):
        """Whole-day (or month) recorded bytes for one application or category."""
        if not self._app_table() or app is None and category is None:
            return []
        values = {}
        if app is not None:
            query, params = "app=?" + (" AND category=?" if category is not None else ""), (app,) + ((category,) if category is not None else ())
        else:
            query, params = "category=?", (category,)
        for day, row_device, row_app, row_category, payload in self.db.execute(
                "SELECT day,device,app,category,payload FROM app_daily WHERE " + query + " ORDER BY day", params):
            if app is not None and row_app != app or category is not None and row_category != category:
                continue
            if row_app == "" and row_category == "" or device and row_device != device:
                continue
            row = json.loads(payload)
            if vlan and row["vlan"]["key"] != vlan:
                continue
            key = day[:7] if months else day
            value = values.setdefault(key, {"period": key, "upload_bytes": 0, "download_bytes": 0,
                                            "pending_upload_bytes": 0, "pending_download_bytes": 0, "observed_days": set()})
            for column in ("upload_bytes", "download_bytes", "pending_upload_bytes", "pending_download_bytes"):
                value[column] += row[column]
            value["observed_days"].add(day)
        result = []
        for key in sorted(values)[-limit:]:
            value = values[key]
            value["observed_days"] = len(value["observed_days"])
            result.append(value)
        return result

    def _iter_app_rows(self, start, end):
        """Whole days from `app_daily`; partial edges from retained facts and labels."""
        zone = tz(self.config["accounting_timezone"])
        start_local = datetime.fromtimestamp(start//NS, zone)
        end_local = datetime.fromtimestamp(end//NS, zone)
        first_full = start_local.date() + (timedelta(days=1) if (start_local.hour or start_local.minute or start_local.second or start % NS) else timedelta())
        last_full_end = end_local.date()
        whole = self.db.execute(
            "SELECT day,device,app,category,payload FROM app_daily WHERE day>=? AND day<?",
            (first_full.isoformat(), last_full_end.isoformat())) if self._app_table() else ()
        for day, device, app, category, payload in whole:
            yield {**json.loads(payload), "day": day, "device_id": device, "app": app, "category": category}
        middle_start = timestamp_ns(datetime.combine(first_full, datetime.min.time(), zone).isoformat())
        middle_end = timestamp_ns(datetime.combine(last_full_end, datetime.min.time(), zone).isoformat())
        for a, b in ((start, min(end, middle_start)), (max(start, middle_end, middle_start), end)):
            if a < b:
                if a < self.state("ledger_floor_ns", 0):
                    raise ValueError("Partial-day detail expired; request whole calendar days or increase ledger retention before collection")
                for fact, labels in self.ledger.range_facts_labels(a, b):
                    if fact["classification"] == "INTERNET":
                        yield {**fact, "records": 1, "app": labels.get("app", ""), "category": labels.get("appcat", ""),
                               "risk": labels.get("apprisk", "")}

    def applications(self, start, end, *, app=None, category=None, device=None, vlan=None, history=False):
        """Recorded Internet bytes by application and category for [start,end).

        `app`/`category` select one application or category ("" = unidentified)
        and add its devices. Bytes recorded before labels were kept carry no
        labels at all; they are reported apart as `untracked`, never as
        unidentified, and `tracking_started_ns` says when labels begin.
        """
        start, end = timestamp_ns(start), timestamp_ns(end)
        if start >= end:
            raise ValueError("Report start must precede its end")
        amounts = ("upload_bytes", "download_bytes", "pending_upload_bytes", "pending_download_bytes")
        columns = (*amounts, "records")
        apps, categories, devices = {}, {}, {}
        untracked = {c: 0 for c in columns}

        def add(target, row):
            for column in columns:
                target[column] += row[column]

        for row in self._iter_app_rows(start, end):
            if vlan and row["vlan"]["key"] != vlan or device and row["device_id"] != device:
                continue
            if row["app"] == "" and row["category"] == "":
                add(untracked, row)
                continue
            key = (row["app"], row["category"])
            entry = apps.setdefault(key, {"app": row["app"] or None, "category": row["category"] or None, "risk": None,
                                          "generic": bool(GENERIC_APP.fullmatch(row["app"])), "event_ns": -1,
                                          "devices": set(), **{c: 0 for c in columns}})
            add(entry, row)
            entry["devices"].add(row["device_id"])
            if row["event_ns"] >= entry["event_ns"]:
                entry["event_ns"], entry["risk"] = row["event_ns"], row.get("risk") or entry["risk"]
            group = categories.setdefault(row["category"], {"category": row["category"] or None, "apps": set(),
                                                             "devices": set(), **{c: 0 for c in columns}})
            add(group, row)
            group["apps"].add(row["app"])
            group["devices"].add(row["device_id"])
            if (app is not None and row["app"] == app and (category is None or row["category"] == category)) or \
                    (app is None and category is not None and row["category"] == category):
                target = devices.setdefault((row["device_id"], row["vlan"]["key"]), {"device_id": row["device_id"], "vlan": row["vlan"],
                    "event_ns": -1, "hostname": "", "ip": "", "mac": "", "identity_kind": "", **{c: 0 for c in columns}})
                add(target, row)
                if row["event_ns"] >= target["event_ns"]:
                    for column in ("event_ns", "hostname", "ip", "mac", "identity_kind"):
                        target[column] = row[column]

        def finish(values, *sets):
            result = []
            for value in values:
                value = dict(value)
                for name in sets:
                    value[name] = len(value[name])
                value.pop("event_ns", None)
                value["total_bytes"] = value["upload_bytes"]+value["download_bytes"]
                result.append(value)
            return sorted(result, key=lambda x: (-x["total_bytes"], str(x.get("app") or x.get("category") or x.get("device_id"))))

        rows = finish(apps.values(), "devices")
        selected = finish(devices.values()) if (app is not None or category is not None) else []
        found = self.profiles(x["device_id"] for x in selected)
        for row in selected:
            row["profile"] = found.get(row["device_id"])
        total = sum(x["total_bytes"] for x in rows)
        unidentified = sum(x["total_bytes"] for x in rows if x["app"] is None)
        generic = sum(x["total_bytes"] for x in rows if x["generic"])
        named = [x for x in rows if x["app"] is not None and not x["generic"]]
        genai = next((c for c in finish(categories.values(), "apps", "devices") if c["category"] == "GenAI"), None)
        untracked_total = untracked["upload_bytes"]+untracked["download_bytes"]
        result = {"start_utc": iso(start), "end_utc": iso(end), "boundary": "[start,end)", "timezone": self.config["accounting_timezone"],
                  "applications": rows, "categories": finish(categories.values(), "apps", "devices"), "devices": selected,
                  "totals": {c: sum(x[c] for x in rows) if rows else None for c in amounts},
                  "summary": {"total_bytes": total if rows else None, "applications": len(rows),
                              "top_application": named[0]["app"] if named else None,
                              "identified_bytes": total-unidentified-generic if rows else None,
                              "unidentified_bytes": unidentified if rows else None, "generic_bytes": generic if rows else None,
                              "genai_bytes": genai["total_bytes"] if genai else 0, "genai_devices": genai["devices"] if genai else 0},
                  "unidentified_bytes": unidentified if rows else None,
                  "untracked": {**untracked, "total_bytes": untracked_total} if untracked["records"] else None,
                  "tracking_started_ns": self.state("applications_first_ns"),
                  "applications_since": self.db.execute("SELECT MIN(day) FROM app_daily WHERE category<>'' OR app<>''").fetchone()[0] if self._app_table() else None,
                  "traffic_coverage": "UNKNOWN", "status": "RECORDED_ONLY" if rows else "UNAVAILABLE",
                  "note": "Application labels come from FortiGate Application Control on each record. Records without an application label are unidentified; protocol-only labels such as QUIC or SSL are marked generic. Device names are not authenticated user accounts."}
        if history and (app is not None or category is not None):
            result["daily_history"] = self.app_history(app, category, device=device, vlan=vlan)
            result["monthly_history"] = self.app_history(app, category, device=device, vlan=vlan, months=True, limit=36)
        return result

    def ingest(self, records, *, checkpoint=None):
        if len(records) > 30000:
            raise ValueError("Batch exceeds bounded ingestion size")
        normalized, rejected = [], []
        for record in records:
            try:
                normalized.append(normalize(record, self.config.get("source_id", "dedicated-listener")))
            except (ValueError, TypeError, KeyError):
                rejected.append(digest([self.config.get("source_id"), record]))
        affected, inserted, repeated, singletons = set(), 0, 0, 0
        with self.db, self.ledger.write_unit():
            maximum = self.delivery_max()
            for uid in rejected:
                new = self.ledger.insert_delivery(uid, "invalid", checkpoint or 0)
                if new:
                    maximum = max(maximum, checkpoint or 0) if maximum is not None else checkpoint or 0
                    self.set_state("parser_rejections", self.state("parser_rejections", 0)+1)
            for event in normalized:
                new_delivery = self.ledger.insert_delivery(event["delivery_uid"], event["uid"], event["record_ns"])
                if new_delivery:
                    maximum = max(maximum, event['record_ns']) if maximum is not None else event['record_ns']
                if event["event_ns"] < self.state("ledger_floor_ns", 0):
                    if new_delivery:
                        self.set_state("late_rejections", self.state("late_rejections", 0)+1)
                    continue
                if self.ledger.supports_skipped and unsupported(event["fields"]):
                    # Normalization gives each unsupported record its own session,
                    # so its only effect is one source-wide classification count.
                    new_event = self.ledger.insert_skipped(event["uid"], event["event_ns"], "UNSUPPORTED_RECORD")
                    inserted += new_event
                    repeated += bool(new_delivery and not new_event)
                    if new_event:
                        singletons += 1
                        day = datetime.fromtimestamp(event["event_ns"]//NS, tz(self.config["accounting_timezone"])).date().isoformat()
                        self._rollup({"day": day, "classification": "UNSUPPORTED_RECORD", "event_ns": event["event_ns"]}, 1)
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
                new_event = self.ledger.insert_event(event)
                inserted += new_event
                repeated += bool(new_delivery and not new_event)
                if new_event:
                    affected.add(event["session"])
            # Sorted: a set's order varies between processes, and the first fact of a
            # daily row supplies its representative identifiers.
            for session in sorted(affected):
                rows = self.ledger.session_events(session, limit=10001)
                if len(rows) > 10000:
                    raise ValueError("Session exceeds safe recomputation size; checkpoint not advanced")
                labels = {row["uid"]: row.get("meta") or {} for row in rows}
                for old in self.ledger.session_facts(session):
                    self._rollup(old, -1)
                    self._app_rollup(old, labels.get(old["uid"], {}), -1)
                self.ledger.delete_session_facts(session)
                for fact in derive_session(rows, self.inventory, self.config):
                    self.ledger.insert_fact(fact, session)
                    self._rollup(fact, 1)
                    self._app_rollup(fact, labels.get(fact["uid"], {}), 1)
            self.set_state("replay_candidates", self.state("replay_candidates", 0)+repeated)
            self.set_state('delivery_max_ns', maximum)
            if checkpoint is not None:
                self.set_state("checkpoint_ns", checkpoint)
        return {"new_events": inserted, "new_replay_candidates": repeated, "rejected_input_records": len(rejected), "affected_sessions": len(affected)+singletons}

    def prune(self, now, ledger_days=3, summary_days=400):
        if not 1 <= ledger_days <= 365 or not ledger_days <= summary_days <= 3650:
            raise ValueError("Ledger retention must cover at least one day; summary retention must cover the ledger")
        floor = timestamp_ns(now)-ledger_days*86400*NS
        with self.db, self.ledger.write_unit():
            sessions = self.ledger.sessions_before(floor)
            for session in sessions:
                same_stamp = self.ledger.latest_session_events_before(session, floor)
                last = max(same_stamp,key=lambda row:event_order(row[1]))
                event = last[1]
                # Keep one baseline for an open session, without contributing its
                # bytes to a retained daily summary a second time.
                keep = event["fields"].get("logid") == "0000000020"
                self.ledger.delete_events_before(session, floor, last[0] if keep else "")
                if keep and not event.get("anchor"):
                    fact = self.ledger.event_fact(last[0])
                    event.update(anchor=True, generation_seed=fact["generation_seed"] if fact else event.get("generation_seed", event["uid"]))
                    self.ledger.update_event(event)
            self.ledger.delete_facts_before(floor)
            self.ledger.delete_deliveries_before(floor)
            if self.ledger.supports_skipped:
                self.ledger.delete_skipped_before(floor)
            maximum = self.delivery_max()
            if maximum is not None and maximum < floor:
                self.set_state('delivery_max_ns', None)
            cutoff = datetime.fromtimestamp((timestamp_ns(now)-summary_days*86400*NS)//NS, tz(self.config["accounting_timezone"])).date().isoformat()
            self.db.execute("DELETE FROM daily WHERE day<?", (cutoff,))
            self.db.execute("DELETE FROM app_daily WHERE day<?", (cutoff,))
            self.db.execute('DELETE FROM quality WHERE day<?',(cutoff,))
            self.set_state("ledger_floor_ns", max(floor, self.state("ledger_floor_ns", 0)))
            self.set_state("summary_floor_day", cutoff)

    def range_rows(self, start, end):
        """Return the historical list interface for direct callers."""
        return list(self._iter_range_rows(start, end))

    def _iter_range_rows(self, start, end):
        # A partial day can contain millions of facts. Reports consume each row
        # immediately rather than keeping a second complete ledger in memory.
        zone = tz(self.config["accounting_timezone"])
        start_local = datetime.fromtimestamp(start//NS, zone)
        end_local = datetime.fromtimestamp(end//NS, zone)
        first_full = start_local.date() + (timedelta(days=1) if (start_local.hour or start_local.minute or start_local.second or start % NS) else timedelta())
        last_full_end = end_local.date()
        for row in self.db.execute("SELECT payload FROM daily WHERE day>=? AND day<?", (first_full.isoformat(), last_full_end.isoformat())):
            yield json.loads(row[0])
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
                        yield value
                        included.add((value["device_id"], value["vlan"]["key"]))
                    elif last >= a and first < b:
                        partial = True
                if partial:
                    for value in self.ledger.range_facts(a, b, classification="INTERNET"):
                        if value["classification"] == "INTERNET" and (value["device_id"], value["vlan"]["key"]) not in included:
                            yield {**value, "records": 1, "flag_counts": {k: 1 for k in value["flags"]}}

    def report(self, start, end, *, vlan=None, device=None, search=""):
        start, end = timestamp_ns(start), timestamp_ns(end)
        if start >= end:
            raise ValueError("Report start must precede its end")
        grouped, flags = {}, defaultdict(int)
        for row in self._iter_range_rows(start, end):
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
            "rows": rows, "devices": self._with_profiles(sorted(devices.values(), key=lambda x: x["total_bytes"], reverse=True)),
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
                count=self.ledger.count_classification(max(start,boundary),min(end,stop),row[1])
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
        # Separate extrema let the existing facts_stamp index stop at its ends.
        floor = self.state('ledger_floor_ns', 0)
        first, last = self.ledger.fact_extrema(floor)
        delivery = self.delivery_max()
        return {"checkpoint_ns": self.state("checkpoint_ns"), "last_received_event_ns": last, "first_retained_event_ns": first,
                "last_delivery_ns": delivery,
                "replay_candidates": self.state("replay_candidates", 0), "late_rejections": self.state("late_rejections", 0),
                "parser_rejections": self.state("parser_rejections", 0),
                "ledger_floor_ns": self.state("ledger_floor_ns"), "summary_floor_day": self.state("summary_floor_day"),
                "traffic_coverage": "UNKNOWN", "source_identity_bound": bool(self.config.get("expected_devid")),
                "inventory_available": bool(self.inventory.get("interfaces"))}
