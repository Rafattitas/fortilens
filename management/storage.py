"""Bounded filesystem growth observations, independent of retention policy.

The result is a one-day continuation of observed net growth, not a retained-size
model or a storage guarantee. Callers decide whether collection conditions are
representative and provide an identity that changes with the measured volume.
"""

import math
from fractions import Fraction

SAMPLE_SECONDS = 300
MIN_OBSERVATION_SECONDS = 3600
MIN_SAMPLES = 4
MAX_SAMPLE_AGE_SECONDS = 3600
WINDOW_SECONDS = 86400
HORIZON_SECONDS = 86400
MAX_SAMPLES = 289
MAX_BYTES = 2**63 - 1
MAX_TIME_SECONDS = 253402300799


def _seconds(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and 0 <= value <= MAX_TIME_SECONDS
        and math.isfinite(value)
    )


def _bytes(value):
    return type(value) is int and 0 <= value <= MAX_BYTES


def _identity(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 512
        and not any(ord(char) < 32 for char in value)
    )


def _history(value):
    """Copy only the known, bounded schema; never trust persisted forecasts."""
    if not isinstance(value, dict) or set(value) != {"version", "identity", "samples"}:
        return None
    if type(value["version"]) is not int or value["version"] != 1:
        return None
    if not _identity(value["identity"]):
        return None
    samples = value["samples"]
    if not isinstance(samples, list) or len(samples) > MAX_SAMPLES:
        return None
    copied = []
    for sample in samples:
        if not isinstance(sample, dict) or set(sample) != {"at", "bytes"}:
            return None
        if not _seconds(sample["at"]) or not _bytes(sample["bytes"]):
            return None
        item = {"at": float(sample["at"]), "bytes": sample["bytes"]}
        if copied:
            gap = item["at"] - copied[-1]["at"]
            if not SAMPLE_SECONDS <= gap <= MAX_SAMPLE_AGE_SECONDS:
                return None
            if item["bytes"] < copied[-1]["bytes"]:
                return None
        copied.append(item)
    return copied


def _public(status, message):
    return {"forecast_status": status, "forecast_message": message}


def _reason(value, fallback):
    if (
        isinstance(value, str)
        and value.strip()
        and len(value) <= 512
        and not any(ord(char) < 32 for char in value)
    ):
        return value.strip()
    return fallback


def observe(history, identity, now, size, *, eligible=True, reason=None):
    """Return public trend evidence and a new JSON-safe observation history.

    Fresh requests inside the five-minute sampling interval reuse the same
    persisted endpoints and original estimate timestamp. The history window is
    anchored to that latest persisted sample so refreshes cannot alter a slope
    merely by ageing its first sample out. No input dictionary is mutated.
    """
    if not _identity(identity) or not _seconds(now) or not _bytes(size):
        return (
            _public("unavailable", "A valid storage measurement is required for estimation."),
            {},
        )
    empty = {"version": 1, "identity": identity, "samples": []}
    if eligible is not True:
        return (
            _public(
                "unavailable",
                _reason(reason, "Storage growth is not representative of normal collection."),
            ),
            empty,
        )

    message = "Collecting storage observations: at least one hour and four samples are required."
    samples = _history(history)
    if samples is None:
        samples = []
    elif history["identity"] != identity:
        samples = []
        message = "Storage identity changed; collecting a new growth baseline."
    if samples:
        latest = samples[-1]
        if now < latest["at"]:
            samples = []
            message = "Server time moved backwards; collecting a new growth baseline."
        elif now - latest["at"] > MAX_SAMPLE_AGE_SECONDS:
            samples = []
            message = "Storage observations expired; collecting a new growth baseline."
        elif size < latest["bytes"]:
            samples = []
            message = "Storage size decreased; collecting a new baseline after compaction or reset."
    if not samples or now - samples[-1]["at"] >= SAMPLE_SECONDS:
        samples.append({"at": float(now), "bytes": size})
    cutoff = samples[-1]["at"] - WINDOW_SECONDS
    samples = [sample for sample in samples if sample["at"] >= cutoff][-MAX_SAMPLES:]
    updated = {"version": 1, "identity": identity, "samples": samples}
    span = samples[-1]["at"] - samples[0]["at"]
    if span < MIN_OBSERVATION_SECONDS or len(samples) < MIN_SAMPLES:
        return _public("collecting", message), updated
    growth = samples[-1]["bytes"] - samples[0]["bytes"]
    if growth <= 0:
        return (
            _public(
                "unavailable",
                "No positive net growth was measured; a zero-byte forecast is not established.",
            ),
            updated,
        )
    # Fraction avoids rounding large integer byte counts before taking the ceil.
    projection = math.ceil(Fraction(growth * HORIZON_SECONDS) / Fraction(span))
    return (
        {
            **_public(
                "available",
                "One-day continuation of observed net growth; compaction and traffic may change it.",
            ),
            "projection_bytes": projection,
            "projection_at_seconds": samples[-1]["at"],
            "observation_seconds": span,
            "sample_count": len(samples),
            "horizon_seconds": HORIZON_SECONDS,
        },
        updated,
    )
