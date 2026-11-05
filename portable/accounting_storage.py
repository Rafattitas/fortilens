"""Versioned ledger storage; calculations remain in accounting.py.

The library default for a new database remains legacy JSON; the accounting
service selects compact storage for new files. Compact version 2 compresses
record details and keeps unsupported records as small counted markers. A
version 1 file is upgraded in place by a writer; its older rows stay readable.
"""

import json
import sqlite3
import zlib
from collections import OrderedDict
from contextlib import contextmanager
from copy import deepcopy
from functools import lru_cache

COMPACT_VERSION = 2
COMPACT_VERSIONS = (1, 2)
# Frozen preset dictionary for compressed compact records. Never edit it: stored
# version 2 rows can only be decoded with exactly these bytes.
ZDICT_V2 = (
    b'"0000000013","0000000020","0001000014","0000000011","traffic","forward","local",'
    b'"utm","app-ctrl","webfilter","event","system","lan","wan","dmz","undefined",'
    b'"root","internal","users","MAPPED","NOT_VLAN","UNKNOWN","AMBIGUOUS",'
    b'"MAC","IP_SESSION","OBSERVATION","dedicated-listener",'
    b'"contribution_interval_unknown","calendar_boundary_allocation_uncertain",'
    b'"source_binding_not_confirmed","source_device_id_missing","source_timestamp_skew",'
    b'"event_identity_uses_loki_timestamp","physical_device_identity_unverified",'
    b'"historical_vlan_mapping_not_verified","historical_direction_not_verified",'
    b'"session_restart_or_counter_reset","session_endpoint_changed","repeated_close_observation",'
    b'"upload_contribution_missing","download_contribution_missing","upload_baseline_fallback",'
    b'"download_baseline_fallback","upload_end_only_session","download_end_only_session",'
    b'"upload_unobserved_counter_bytes","download_unobserved_counter_bytes",'
    b'"unsupported_record","unscoped_source","unresolved_direction","external_or_transit",'
    b'"vlan_mapping_unknown","vlan_mapping_ambiguous",'
    b'"status":"NOT_VLAN","vlan_id":null,"parent":null,"key":"interface:'
    b'{"network_evidence":{"classification":"INTERNET","vlan":{"status":"MAPPED","vlan_id":'
    b',"interface":"","parent":"","role":"lan","key":"vlan:'
    b'},"flags":[]}}]'
)
PACKED_V2 = b"\x02"
MAX_RECORD_BYTES = 1024 * 1024
FIELD_NAMES = tuple(
    sorted(
        "devid vd logid type subtype sessionid srcip dstip srcport dstport proto srcintf dstintf srcintfrole dstintfrole mastersrcmac srcmac srcname sentdelta rcvddelta sentbyte rcvdbyte duration durationdelta eventtime idseq".split()
    )
)
ENDPOINT_FIELDS = (
    "device_id",
    "identity_kind",
    "mac",
    "ip",
    "hostname",
    "source_interface",
    "vdom",
)
AMOUNTS = ("upload_bytes", "download_bytes", "pending_upload_bytes", "pending_download_bytes")
FIELD_KEYS = frozenset(FIELD_NAMES)
FACT_FIELDS = ("generation_seed", "day", *ENDPOINT_FIELDS, "vlan", "wan", *AMOUNTS, "flags")
EVENT_PROPERTIES = frozenset(
    (
        "uid",
        "session",
        "event_ns",
        "delivery_uid",
        "source",
        "record_ns",
        "strong_event_time",
        "fields",
    )
)
FACT_PROPERTIES = frozenset(
    (
        "uid",
        "generation_seed",
        "event_ns",
        "day",
        *ENDPOINT_FIELDS,
        "vlan",
        "wan",
        "classification",
        *AMOUNTS,
        "flags",
    )
)


class UnsupportedStorageFormatError(ValueError):
    """The database cannot safely be interpreted by this implementation."""


COMMON_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily(day TEXT NOT NULL,device TEXT NOT NULL,vlan TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(day,device,vlan));
CREATE TABLE IF NOT EXISTS quality(day TEXT NOT NULL,classification TEXT NOT NULL,records INTEGER NOT NULL,first_stamp INTEGER NOT NULL,last_stamp INTEGER NOT NULL,PRIMARY KEY(day,classification));
CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""
LEGACY_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(uid TEXT PRIMARY KEY,session TEXT NOT NULL,stamp INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_session ON events(session,stamp);
CREATE TABLE IF NOT EXISTS deliveries(uid TEXT PRIMARY KEY,event_uid TEXT NOT NULL,stamp INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS deliveries_stamp ON deliveries(stamp);
CREATE TABLE IF NOT EXISTS facts(uid TEXT PRIMARY KEY,session TEXT NOT NULL,stamp INTEGER NOT NULL,payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS facts_session ON facts(session);
CREATE INDEX IF NOT EXISTS facts_stamp ON facts(stamp);
"""
COMPACT_SCHEMA = """
CREATE TABLE compact_events(
 id INTEGER PRIMARY KEY,uid BLOB NOT NULL UNIQUE CHECK(length(uid)=32),
 session BLOB NOT NULL CHECK(length(session)=32),stamp INTEGER NOT NULL,
 delivery_uid BLOB NOT NULL CHECK(length(delivery_uid)=32),record_ns INTEGER NOT NULL,
 strong_time INTEGER NOT NULL CHECK(strong_time IN (0,1)),details NOT NULL);
CREATE INDEX compact_events_session ON compact_events(session,stamp,uid);
CREATE INDEX compact_events_stamp ON compact_events(stamp);
CREATE TABLE compact_facts(
 id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL UNIQUE REFERENCES compact_events(id) ON DELETE CASCADE,
 classification TEXT NOT NULL,details TEXT NOT NULL);
-- Keep the original event_id uniqueness; this extra index covers count lookups.
CREATE UNIQUE INDEX compact_facts_event_class ON compact_facts(event_id,classification);
CREATE TABLE compact_deliveries(
 uid BLOB PRIMARY KEY CHECK(length(uid)=32),event_hash BLOB CHECK(event_hash IS NULL OR length(event_hash)=32),
 invalid INTEGER NOT NULL CHECK(invalid IN (0,1)),stamp INTEGER NOT NULL,
 CHECK((invalid=1 AND event_hash IS NULL) OR (invalid=0 AND event_hash IS NOT NULL)));
CREATE INDEX compact_deliveries_stamp ON compact_deliveries(stamp);
CREATE VIEW events AS SELECT NULL AS uid,NULL AS session,NULL AS stamp,NULL AS payload WHERE 0;
CREATE VIEW facts AS SELECT NULL AS uid,NULL AS session,NULL AS stamp,NULL AS payload WHERE 0;
CREATE VIEW deliveries AS SELECT NULL AS uid,NULL AS event_uid,NULL AS stamp WHERE 0;
"""
# Unsupported records never contribute bytes. Version 2 keeps only their exact
# identity, time and classification so replay detection and counts stay exact.
SKIPPED_SCHEMA = """
CREATE TABLE IF NOT EXISTS compact_skipped(
 uid BLOB PRIMARY KEY CHECK(length(uid)=32),stamp INTEGER NOT NULL,classification TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS compact_skipped_stamp ON compact_skipped(stamp,classification);
"""
SKIPPED_COLUMNS = ("uid", "stamp", "classification")
COMPACT_COLUMNS = {
    "compact_events": (
        "id",
        "uid",
        "session",
        "stamp",
        "delivery_uid",
        "record_ns",
        "strong_time",
        "details",
    ),
    "compact_facts": ("id", "event_id", "classification", "details"),
    "compact_deliveries": ("uid", "event_hash", "invalid", "stamp"),
    "daily": ("day", "device", "vlan", "payload"),
    "quality": ("day", "classification", "records", "first_stamp", "last_stamp"),
    "state": ("key", "value"),
}


def _hash(value):
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError("Invalid ledger identity")
    return _hash_text(str(value))


@lru_cache(maxsize=8192)
def _hash_text(value):
    try:
        result = bytes.fromhex(value)
    except ValueError:
        raise ValueError("Invalid ledger identity") from None
    if len(result) != 32 or result.hex() != value:
        raise ValueError("Invalid ledger identity")
    return result


@lru_cache(maxsize=256)
def _class_text(value):
    return json.dumps(value)


@lru_cache(maxsize=256)
def _class_value(value):
    result = json.loads(value)
    if not isinstance(result, str):
        raise ValueError("Invalid ledger classification")
    return result


def _pack(value):
    text = json.dumps(value, separators=(",", ":")).encode()
    packer = zlib.compressobj(6, zlib.DEFLATED, -15, 9, zlib.Z_DEFAULT_STRATEGY, ZDICT_V2)
    return PACKED_V2 + packer.compress(text) + packer.flush()


def _unpack(value):
    # Version 1 rows are JSON text; version 2 rows are dictionary-compressed.
    if isinstance(value, str):
        return json.loads(value)
    if not isinstance(value, bytes) or value[:1] != PACKED_V2:
        raise ValueError("Invalid compact record encoding")
    try:
        unpacker = zlib.decompressobj(-15, ZDICT_V2)
        text = unpacker.decompress(value[1:], MAX_RECORD_BYTES)
        if unpacker.unconsumed_tail or not unpacker.eof:
            raise ValueError("Invalid compact record encoding")
        return json.loads(text)
    except (zlib.error, UnicodeDecodeError):
        raise ValueError("Invalid compact record encoding") from None


@lru_cache(maxsize=256)
def _names_for_mask(mask):
    if type(mask) is not int or not 0 <= mask < (1 << len(FIELD_NAMES)):
        raise ValueError("Invalid compact event field mask")
    return tuple(name for index, name in enumerate(FIELD_NAMES) if mask & (1 << index))


@lru_cache(maxsize=256)
def _field_pattern(keys):
    mask = sum(1 << index for index, name in enumerate(FIELD_NAMES) if name in keys)
    return mask, _names_for_mask(mask), tuple(sorted(keys - FIELD_KEYS))


def _encode_fields(fields):
    mask, names, extra = _field_pattern(frozenset(fields))
    return [mask, [fields[name] for name in names], {name: fields[name] for name in extra}]


def _decode_fields(value):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("Invalid compact event field vector")
    mask, values, extra = value
    names = _names_for_mask(mask)
    if (
        not isinstance(values, list)
        or len(values) != len(names)
        or not isinstance(extra, dict)
        or extra.keys() & FIELD_KEYS
    ):
        raise ValueError("Invalid compact event field vector")
    result = dict(zip(names, values))
    result.update(extra)
    return result


def _script(db, text):
    try:
        db.executescript("BEGIN IMMEDIATE;" + text + ";COMMIT;")
    except Exception:
        db.rollback()
        raise


def detect_storage_format(db, storage_format=None):
    if storage_format not in (None, "legacy", "compact"):
        raise UnsupportedStorageFormatError("Choose a supported accounting storage format")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    objects = {
        r[0]: r[1]
        for r in db.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }
    if version in COMPACT_VERSIONS:
        if storage_format == "legacy":
            raise UnsupportedStorageFormatError("The accounting database uses compact storage")
        if not all(objects.get(name) == "table" for name in COMPACT_COLUMNS) or not all(
            objects.get(name) == "view" for name in ("events", "facts", "deliveries")
        ):
            raise UnsupportedStorageFormatError("The compact accounting layout is incomplete")
        for table, expected in COMPACT_COLUMNS.items():
            columns = tuple(r[1] for r in db.execute("PRAGMA table_info(" + table + ")"))
            if columns != expected:
                raise UnsupportedStorageFormatError("The compact accounting layout is incomplete")
        if objects.get("compact_events_session") != "index":
            raise UnsupportedStorageFormatError("The compact accounting layout is incomplete")
        if version >= 2 and (
            objects.get("compact_skipped") != "table"
            or tuple(r[1] for r in db.execute("PRAGMA table_info(compact_skipped)"))
            != SKIPPED_COLUMNS
        ):
            raise UnsupportedStorageFormatError("The compact accounting layout is incomplete")
        return "compact"
    if version != 0 or any(name.startswith("compact_") for name in objects):
        raise UnsupportedStorageFormatError("The accounting storage version is not supported")
    if storage_format == "compact":
        if objects:
            raise UnsupportedStorageFormatError(
                "Use a separately migrated compact accounting database"
            )
        return "compact"
    return "legacy"


def open_ledger(db, storage_format=None, initialize=True):
    selected = detect_storage_format(db, storage_format)
    if selected == "compact":
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == COMPACT_VERSION:
            return CompactLedger(db)
        if version == 1:
            if not initialize:
                return CompactLedger(db, skipped=False)
            # Additive upgrade: version 1 rows remain valid and readable.
            _script(db, SKIPPED_SCHEMA + "PRAGMA user_version=" + str(COMPACT_VERSION))
            return CompactLedger(db)
        if not initialize:
            raise UnsupportedStorageFormatError("The compact accounting layout is incomplete")
        _script(
            db,
            COMPACT_SCHEMA
            + SKIPPED_SCHEMA
            + COMMON_SCHEMA
            + "PRAGMA user_version="
            + str(COMPACT_VERSION),
        )
        return CompactLedger(db)
    if initialize:
        _script(db, LEGACY_SCHEMA + COMMON_SCHEMA)
    else:
        objects = {
            r[0]: r[1]
            for r in db.execute(
                "SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            )
        }
        if not all(
            objects.get(name) == "table"
            for name in ("events", "facts", "deliveries", "daily", "quality", "state")
        ):
            raise UnsupportedStorageFormatError("The legacy accounting layout is incomplete")
    return LegacyLedger(db)


class LegacyLedger:
    format = "legacy"
    supports_skipped = False

    def __init__(self, db):
        self.db = db

    @contextmanager
    def write_unit(self):
        yield

    def max_delivery_stamp(self):
        return self.db.execute("SELECT MAX(stamp) FROM deliveries").fetchone()[0]

    def insert_delivery(self, uid, event_uid, stamp):
        return self.db.execute(
            "INSERT OR IGNORE INTO deliveries VALUES(?,?,?)", (uid, event_uid, stamp)
        ).rowcount

    def insert_event(self, event):
        return self.db.execute(
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?)",
            (event["uid"], event["session"], event["event_ns"], json.dumps(event)),
        ).rowcount

    def session_events(self, session, limit=10001):
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT payload FROM events WHERE session=? ORDER BY stamp,uid LIMIT ?",
                (session, limit),
            )
        ]

    def session_facts(self, session):
        return [
            json.loads(row[0])
            for row in self.db.execute("SELECT payload FROM facts WHERE session=?", (session,))
        ]

    def delete_session_facts(self, session):
        self.db.execute("DELETE FROM facts WHERE session=?", (session,))

    def insert_fact(self, fact, session):
        self.db.execute(
            "INSERT INTO facts VALUES(?,?,?,?)",
            (fact["uid"], session, fact["event_ns"], json.dumps(fact)),
        )

    def sessions_before(self, floor):
        return [
            row[0]
            for row in self.db.execute(
                "SELECT DISTINCT session FROM events WHERE stamp<?", (floor,)
            )
        ]

    def latest_session_events_before(self, session, floor):
        return [
            (row[0], json.loads(row[1]))
            for row in self.db.execute(
                "SELECT uid,payload FROM events WHERE session=? AND stamp=(SELECT MAX(stamp) FROM events WHERE session=? AND stamp<?)",
                (session, session, floor),
            )
        ]

    def delete_events_before(self, session, floor, keep_uid=""):
        self.db.execute(
            "DELETE FROM events WHERE session=? AND stamp<? AND uid!=?", (session, floor, keep_uid)
        )

    def event_fact(self, uid):
        row = self.db.execute("SELECT payload FROM facts WHERE uid=?", (uid,)).fetchone()
        return json.loads(row[0]) if row else None

    def update_event(self, event):
        self.db.execute(
            "UPDATE events SET payload=? WHERE uid=?", (json.dumps(event), event["uid"])
        )

    def delete_facts_before(self, floor):
        self.db.execute("DELETE FROM facts WHERE stamp<?", (floor,))

    def delete_deliveries_before(self, floor):
        self.db.execute("DELETE FROM deliveries WHERE stamp<?", (floor,))

    def range_facts(self, start, end, *, classification=None):
        for row in self.db.execute(
            "SELECT payload FROM facts WHERE stamp>=? AND stamp<?", (start, end)
        ):
            fact = json.loads(row[0])
            if classification is None or fact["classification"] == classification:
                yield fact

    def count_classification(self, start, end, classification):
        return self.db.execute(
            "SELECT COUNT(*) FROM facts WHERE stamp>=? AND stamp<? AND json_extract(payload,'$.classification')=?",
            (start, end, classification),
        ).fetchone()[0]

    def fact_extrema(self, floor):
        first = self.db.execute("SELECT MIN(stamp) FROM facts WHERE stamp>=?", (floor,)).fetchone()[
            0
        ]
        last = self.db.execute("SELECT MAX(stamp) FROM facts WHERE stamp>=?", (floor,)).fetchone()[
            0
        ]
        return first, last


class CompactLedger:
    format = "compact"
    supports_skipped = True
    EVENT_SELECT = "SELECT e.* FROM compact_events e "
    FACT_SELECT = """SELECT f.*,e.uid AS event_uid,e.session AS event_session,e.stamp AS event_stamp
        FROM compact_facts f JOIN compact_events e ON e.id=f.event_id """

    def __init__(self, db, *, skipped=True):
        self.db = db
        # A read-only reader of a not-yet-upgraded version 1 file has no markers.
        self.has_skipped = skipped
        self._event_cache = None
        self._fact_cache = None
        self._unfactored_events = None
        self._fresh_session = None
        self._last_fact_count = None

    @contextmanager
    def write_unit(self):
        self._event_cache = OrderedDict()
        self._fact_cache = OrderedDict()
        self._unfactored_events = set()
        self._fresh_session = None
        self._last_fact_count = None
        try:
            yield
        finally:
            self._event_cache = None
            self._fact_cache = None
            self._unfactored_events = None
            self._fresh_session = None
            self._last_fact_count = None

    def _remember_event(self, uid, identity, session, stamp):
        if self._event_cache is not None:
            self._event_cache[uid] = (identity, session, stamp)
            self._event_cache.move_to_end(uid)
            if len(self._event_cache) > 10000:
                self._event_cache.popitem(last=False)

    def _encode_event(self, event):
        properties = {k: v for k, v in event.items() if k not in EVENT_PROPERTIES}
        details = [event["source"], _encode_fields(event["fields"]), properties]
        return (
            _hash(event["uid"]),
            _hash(event["session"]),
            event["event_ns"],
            _hash(event["delivery_uid"]),
            event["record_ns"],
            int(event["strong_event_time"]),
            _pack(details),
        )

    def _decode_event(self, row):
        details = _unpack(row[7])
        if not isinstance(details, list) or len(details) != 3:
            raise ValueError("Invalid compact event details")
        source, fields, properties = details
        fields = _decode_fields(fields)
        if not isinstance(properties, dict) or any(k in EVENT_PROPERTIES for k in properties):
            raise ValueError("Invalid compact event properties")
        uid = row[1].hex()
        self._remember_event(uid, row[0], row[2], row[3])
        event = {
            "uid": uid,
            "session": row[2].hex(),
            "event_ns": row[3],
            "delivery_uid": row[4].hex(),
            "source": source,
            "record_ns": row[5],
            "strong_event_time": bool(row[6]),
            "fields": fields,
            **properties,
        }
        return event

    def _decode_fact(self, row):
        details = _unpack(row[3])
        if not isinstance(details, list) or len(details) != 2:
            raise ValueError("Invalid compact fact details")
        values, extra = details
        if (
            not isinstance(values, list)
            or len(values) != len(FACT_FIELDS)
            or not isinstance(extra, dict)
            or any(k in FACT_PROPERTIES for k in extra)
        ):
            raise ValueError("Invalid compact fact properties")
        fact = dict(zip(FACT_FIELDS, values))
        fact.update(uid=row[4].hex(), event_ns=row[6], classification=_class_value(row[2]), **extra)
        if not isinstance(fact["vlan"], dict) or not isinstance(fact["flags"], list):
            raise ValueError("Invalid compact fact properties")
        if self._fact_cache is not None:
            cached = fact.copy()
            cached["vlan"] = deepcopy(fact["vlan"])
            cached["flags"] = fact["flags"].copy()
            if extra:
                cached = deepcopy(fact)
            self._fact_cache[fact["uid"]] = (cached, tuple(row[:4]), row[5])
            self._fact_cache.move_to_end(fact["uid"])
            if len(self._fact_cache) > 1024:
                self._fact_cache.popitem(last=False)
        return fact

    def max_delivery_stamp(self):
        return self.db.execute("SELECT MAX(stamp) FROM compact_deliveries").fetchone()[0]

    def insert_skipped(self, uid, stamp, classification):
        """Count an unsupported record once, including against version 1 rows."""
        key = _hash(uid)
        return self.db.execute(
            """INSERT OR IGNORE INTO compact_skipped SELECT ?,?,?
            WHERE NOT EXISTS (SELECT 1 FROM compact_events WHERE uid=?)""",
            (key, stamp, _class_text(classification), key),
        ).rowcount

    def delete_skipped_before(self, floor):
        self.db.execute("DELETE FROM compact_skipped WHERE stamp<?", (floor,))

    def insert_delivery(self, uid, event_uid, stamp):
        invalid = event_uid == "invalid"
        return self.db.execute(
            "INSERT OR IGNORE INTO compact_deliveries VALUES(?,?,?,?)",
            (_hash(uid), None if invalid else _hash(event_uid), int(invalid), stamp),
        ).rowcount

    def insert_event(self, event):
        values = self._encode_event(event)
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO compact_events VALUES(NULL,?,?,?,?,?,?,?)", values
        )
        if cursor.rowcount:
            self._remember_event(event["uid"], cursor.lastrowid, values[1], values[2])
            if self._unfactored_events is not None and len(self._unfactored_events) < 30000:
                self._unfactored_events.add(cursor.lastrowid)
        return cursor.rowcount

    def session_events(self, session, limit=10001):
        result = []
        fresh = self._unfactored_events is not None
        for row in self.db.execute(
            self.EVENT_SELECT + " WHERE e.session=? ORDER BY e.stamp,e.uid LIMIT ?",
            (_hash(session), limit),
        ):
            fresh = fresh and row[0] in self._unfactored_events
            result.append(self._decode_event(row))
        self._fresh_session = session if fresh and len(result) < limit else None
        return result

    def session_facts(self, session):
        # No old contribution can exist when every retained event was inserted
        # in this write unit and is still known not to have a fact.
        if self._unfactored_events is not None and self._fresh_session == session:
            self._last_fact_count = (session, 0)
            return []
        result = [
            self._decode_fact(r)
            for r in self.db.execute(
                self.FACT_SELECT + " WHERE e.session=? ORDER BY f.id", (_hash(session),)
            )
        ]
        if self._unfactored_events is not None:
            self._last_fact_count = (session, len(result))
        return result

    def delete_session_facts(self, session):
        cached, self._last_fact_count = self._last_fact_count, None
        self._fresh_session = None
        if cached == (session, 0):
            return
        self.db.execute(
            "DELETE FROM compact_facts WHERE event_id IN (SELECT id FROM compact_events WHERE session=?)",
            (_hash(session),),
        )

    def insert_fact(self, fact, session, *, ordinal=None):
        self._fresh_session = None
        self._last_fact_count = None
        old = self._fact_cache.get(fact["uid"]) if self._fact_cache is not None else None
        if old and old[0] == fact:
            if old[2] != _hash(session):
                raise ValueError("Fact does not match its retained session")
            values = (ordinal, *old[1][1:])
        else:
            event = self._event_cache.get(fact["uid"]) if self._event_cache is not None else None
            if event is None:
                event = self.db.execute(
                    "SELECT id,session,stamp FROM compact_events WHERE uid=?", (_hash(fact["uid"]),)
                ).fetchone()
            if event is None or event[1] != _hash(session) or event[2] != fact["event_ns"]:
                raise ValueError("Fact does not match its retained event")
            details = [
                [fact[k] for k in FACT_FIELDS],
                {k: v for k, v in fact.items() if k not in FACT_PROPERTIES},
            ]
            values = (ordinal, event[0], _class_text(fact["classification"]), _pack(details))
        self.db.execute("INSERT INTO compact_facts VALUES(?,?,?,?)", values)
        if self._unfactored_events is not None:
            self._unfactored_events.discard(values[1])

    def sessions_before(self, floor):
        # Keyset pages remain bounded while the caller deletes their events.
        # The covering session index avoids a potentially unbounded temp sort.
        previous = None
        while True:
            condition = "" if previous is None else " AND session>?"
            params = (floor,) if previous is None else (floor, previous)
            page = self.db.execute(
                "SELECT DISTINCT session FROM compact_events INDEXED BY compact_events_session WHERE stamp<?"
                + condition
                + " ORDER BY session LIMIT 512",
                params,
            ).fetchall()
            if not page:
                return
            previous = page[-1][0]
            yield from (r[0].hex() for r in page)

    def latest_session_events_before(self, session, floor):
        session = _hash(session)
        return [
            (r["uid"].hex(), self._decode_event(r))
            for r in self.db.execute(
                self.EVENT_SELECT
                + " WHERE e.session=? AND e.stamp=(SELECT MAX(stamp) FROM compact_events WHERE session=? AND stamp<?)",
                (session, session, floor),
            )
        ]

    def delete_events_before(self, session, floor, keep_uid=""):
        self._fresh_session = None
        self._last_fact_count = None
        if self._event_cache is not None:
            self._event_cache.clear()
        where = "session=? AND stamp<?" + (" AND uid!=?" if keep_uid else "")
        params = (_hash(session), floor, _hash(keep_uid)) if keep_uid else (_hash(session), floor)
        self.db.execute("DELETE FROM compact_events WHERE " + where, params)

    def event_fact(self, uid):
        row = self.db.execute(self.FACT_SELECT + " WHERE e.uid=?", (_hash(uid),)).fetchone()
        return self._decode_fact(row) if row else None

    def update_event(self, event):
        self._fresh_session = None
        self._last_fact_count = None
        if self._event_cache is not None:
            self._event_cache.pop(event["uid"], None)
        values = self._encode_event(event)
        self.db.execute(
            "UPDATE compact_events SET uid=?,session=?,stamp=?,delivery_uid=?,record_ns=?,strong_time=?,details=? WHERE uid=?",
            (*values, _hash(event["uid"])),
        )

    def delete_facts_before(self, floor):
        self._fresh_session = None
        self._last_fact_count = None
        self.db.execute(
            "DELETE FROM compact_facts WHERE event_id IN (SELECT id FROM compact_events WHERE stamp<?)",
            (floor,),
        )

    def delete_deliveries_before(self, floor):
        self.db.execute("DELETE FROM compact_deliveries WHERE stamp<?", (floor,))

    def range_facts(self, start, end, *, classification=None):
        predicate = "" if classification is None else " AND f.classification=?"
        params = (
            (start, end) if classification is None else (start, end, _class_text(classification))
        )
        for row in self.db.execute(
            self.FACT_SELECT
            + " WHERE e.stamp>=? AND e.stamp<?"
            + predicate
            + " ORDER BY e.stamp,f.id",
            params,
        ):
            yield self._decode_fact(row)

    def count_classification(self, start, end, classification):
        count = self.db.execute(
            """SELECT COUNT(*) FROM compact_events e JOIN compact_facts f ON f.event_id=e.id
            WHERE e.stamp>=? AND e.stamp<? AND f.classification=?""",
            (start, end, _class_text(classification)),
        ).fetchone()[0]
        if self.has_skipped:
            count += self.db.execute(
                "SELECT COUNT(*) FROM compact_skipped WHERE stamp>=? AND stamp<? AND classification=?",
                (start, end, _class_text(classification)),
            ).fetchone()[0]
        return count

    def fact_extrema(self, floor):
        first = self.db.execute(
            "SELECT e.stamp FROM compact_events e JOIN compact_facts f ON f.event_id=e.id WHERE e.stamp>=? ORDER BY e.stamp LIMIT 1",
            (floor,),
        ).fetchone()
        last = self.db.execute(
            "SELECT e.stamp FROM compact_events e JOIN compact_facts f ON f.event_id=e.id WHERE e.stamp>=? ORDER BY e.stamp DESC LIMIT 1",
            (floor,),
        ).fetchone()
        first, last = (first[0] if first else None, last[0] if last else None)
        if self.has_skipped:
            low, high = self.db.execute(
                "SELECT MIN(stamp),MAX(stamp) FROM compact_skipped WHERE stamp>=?", (floor,)
            ).fetchone()
            first = low if first is None else first if low is None else min(first, low)
            last = high if last is None else last if high is None else max(last, high)
        return first, last
