"""Storage trends use synthetic byte counts and clocks, never live volumes."""

import json
import math
import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from management import storage

START = 1_000_000
IDENTITY = "synthetic-volume-and-retention"


class StorageProjectionTests(unittest.TestCase):
    def observations(self, values=None, times=None):
        values = [1000, 2200, 3400, 4600] if values is None else values
        times = [0, 1200, 2400, 3600] if times is None else times
        history = {}
        for offset, size in zip(times, values):
            public, history = storage.observe(history, IDENTITY, START + offset, size)
        return public, history

    def test_formula_projects_net_growth_for_one_day(self):
        public, _ = self.observations()
        self.assertEqual(public["forecast_status"], "available")
        self.assertEqual(public["projection_bytes"], 86400)
        self.assertEqual(public["projection_at_seconds"], START + 3600)
        self.assertEqual(public["observation_seconds"], 3600)
        self.assertEqual(public["sample_count"], 4)
        self.assertEqual(public["horizon_seconds"], 86400)
        self.assertNotIn("bytes", public)

    def test_fractional_rate_rounds_up(self):
        public, _ = self.observations([0, 1, 2, 3], [0, 1200, 2400, 3601])
        self.assertEqual(public["projection_bytes"], math.ceil(3 * 86400 / 3601))

    def test_short_refresh_reuses_slope_and_original_timestamp(self):
        public, history = self.observations()
        before = deepcopy(history)
        repeated, updated = storage.observe(history, IDENTITY, START + 3800, 9000)
        self.assertEqual(repeated, public)
        self.assertEqual(updated, history)
        self.assertEqual(history, before)

    def test_sampling_waits_five_minutes(self):
        _, history = storage.observe({}, IDENTITY, START, 100)
        _, history = storage.observe(history, IDENTITY, START + 299, 200)
        self.assertEqual(len(history["samples"]), 1)
        _, history = storage.observe(history, IDENTITY, START + 300, 300)
        self.assertEqual(len(history["samples"]), 2)

    def test_four_samples_still_require_an_hour(self):
        public, _ = self.observations(times=[0, 300, 600, 900])
        self.assertEqual(public["forecast_status"], "collecting")
        self.assertNotIn("projection_bytes", public)

    def test_hour_span_still_requires_four_samples(self):
        public, _ = self.observations([100, 200, 300], [0, 1800, 3600])
        self.assertEqual(public["forecast_status"], "collecting")
        self.assertNotIn("projection_bytes", public)

    def test_backlog_invalidates_observations_until_a_new_window(self):
        _, history = self.observations()
        public, history = storage.observe(
            history,
            IDENTITY,
            START + 3601,
            5000,
            eligible=False,
            reason="Accounting is catching up.",
        )
        self.assertEqual(public["forecast_status"], "unavailable")
        self.assertEqual(public["forecast_message"], "Accounting is catching up.")
        self.assertEqual(history["samples"], [])
        public, history = storage.observe(history, IDENTITY, START + 3602, 5100)
        self.assertEqual(public["forecast_status"], "collecting")
        self.assertEqual(len(history["samples"]), 1)

    def test_json_roundtrip_preserves_restart_evidence(self):
        public, history = self.observations()
        restored = json.loads(json.dumps(history))
        repeated, _ = storage.observe(restored, IDENTITY, START + 3700, 4700)
        self.assertEqual(repeated, public)

    def test_stale_latest_observation_starts_a_new_baseline(self):
        _, history = self.observations()
        public, updated = storage.observe(history, IDENTITY, START + 7201, 9000)
        self.assertEqual(public["forecast_status"], "collecting")
        self.assertIn("expired", public["forecast_message"])
        self.assertEqual(updated["samples"], [{"at": START + 7201, "bytes": 9000}])

    def test_clock_rollback_starts_a_new_baseline(self):
        _, history = self.observations()
        public, updated = storage.observe(history, IDENTITY, START + 3500, 5000)
        self.assertEqual(public["forecast_status"], "collecting")
        self.assertIn("backwards", public["forecast_message"])
        self.assertEqual(len(updated["samples"]), 1)

    def test_shrink_starts_a_new_baseline_even_during_short_refresh(self):
        _, history = self.observations()
        public, updated = storage.observe(history, IDENTITY, START + 3601, 4599)
        self.assertEqual(public["forecast_status"], "collecting")
        self.assertIn("decreased", public["forecast_message"])
        self.assertEqual(updated["samples"], [{"at": START + 3601, "bytes": 4599}])

    def test_identity_change_discards_the_previous_rate(self):
        _, history = self.observations()
        public, updated = storage.observe(history, "replacement-volume", START + 3601, 5000)
        self.assertEqual(public["forecast_status"], "collecting")
        self.assertEqual(updated["identity"], "replacement-volume")
        self.assertEqual(len(updated["samples"]), 1)

    def test_zero_growth_is_unknown_rather_than_a_zero_forecast(self):
        public, _ = self.observations([1000, 1000, 1000, 1000])
        self.assertEqual(public["forecast_status"], "unavailable")
        self.assertNotIn("projection_bytes", public)

    def test_history_is_bounded_to_one_day_and_289_samples(self):
        history = {}
        for offset in range(0, 2 * 86400 + 1, 300):
            _, history = storage.observe(history, IDENTITY, START + offset, offset)
        self.assertEqual(len(history["samples"]), 289)
        self.assertEqual(history["samples"][-1]["at"] - history["samples"][0]["at"], 86400)
        self.assertEqual(history["samples"][0]["at"], START + 86400)

    def test_refresh_at_window_boundary_keeps_exact_previous_forecast(self):
        history = {}
        for offset in range(0, 86401, 300):
            size = offset + (1 if offset else 0)
            public, history = storage.observe(history, IDENTITY, START + offset, size)
        repeated, updated = storage.observe(history, IDENTITY, START + 86401, size + 1)
        self.assertEqual(repeated, public)
        self.assertEqual(updated, history)

    def test_invalid_measurements_are_not_persisted(self):
        for now, size in (
            (float("nan"), 1),
            (float("inf"), 1),
            (-1, 1),
            (True, 1),
            (10**1000, 1),
            (START, True),
            (START, -1),
            (START, 1.5),
            (START, storage.MAX_BYTES + 1),
        ):
            with self.subTest(now=repr(now), size=size):
                public, history = storage.observe({}, IDENTITY, now, size)
                self.assertEqual(public["forecast_status"], "unavailable")
                self.assertEqual(history, {})

    def test_invalid_identities_are_not_persisted(self):
        for identity in (None, "", "x" * 513, "volume\nsecret"):
            with self.subTest(identity=identity):
                public, history = storage.observe({}, identity, START, 1)
                self.assertEqual(public["forecast_status"], "unavailable")
                self.assertEqual(history, {})

    def test_corrupt_legacy_and_nonfinite_history_cannot_supply_a_rate(self):
        _, valid = self.observations()
        corrupt = [None, [], {"at": START, "bytes": 100}, {**valid, "version": True}]
        for field, value in (("at", float("nan")), ("at", float("inf")), ("bytes", True)):
            altered = deepcopy(valid)
            altered["samples"][1][field] = value
            corrupt.append(altered)
        for samples in (
            [{"at": START, "bytes": 1}] * 290,
            [{"at": START, "bytes": 2}, {"at": START + 300, "bytes": 1}],
            [{"at": START, "bytes": 1}, {"at": START, "bytes": 2}],
            [{"at": START, "bytes": 1}, {"at": START + 3601, "bytes": 2}],
        ):
            corrupt.append({"version": 1, "identity": IDENTITY, "samples": samples})
        for history in corrupt:
            with self.subTest(history=repr(history)[:120]):
                public, updated = storage.observe(history, IDENTITY, START + 3700, 5000)
                self.assertEqual(public["forecast_status"], "collecting")
                self.assertEqual(updated["samples"], [{"at": START + 3700, "bytes": 5000}])

    def test_large_byte_counts_are_not_clamped_or_rounded_before_projection(self):
        maximum = storage.MAX_BYTES
        public, _ = self.observations([0, maximum // 3, 2 * maximum // 3, maximum])
        self.assertEqual(public["projection_bytes"], maximum * 24)

    def test_ineligible_reason_is_bounded_and_safe(self):
        for reason in (None, "", "private\nraw", "x" * 513, []):
            with self.subTest(reason=reason):
                public, history = storage.observe(
                    {}, IDENTITY, START, 1, eligible=False, reason=reason
                )
                self.assertEqual(public["forecast_status"], "unavailable")
                self.assertEqual(history["samples"], [])
                self.assertNotIn("private", public["forecast_message"])


if __name__ == "__main__":
    unittest.main()
