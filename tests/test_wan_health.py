from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import wan_health as w


class WanHealthTests(unittest.TestCase):
    def test_missing_metrics_are_not_down(self):
        self.assertEqual(w.current_state([], 100, 60, True), "NO DATA")
        self.assertEqual(
            w.current_state([{"state": "down", "value": 1, "timestamp": 99}], 100, 60, False),
            "NO DATA",
        )
        r = w.summarize([], 0, 100)
        self.assertIsNone(r["availability_percent"])
        self.assertEqual(r["down_observed_seconds"], 0)
        self.assertEqual(r["outages"], [])

    def test_zero_onehot_down_value_is_not_an_active_down_state(self):
        rows = [
            {"state": "down", "value": 0, "timestamp": 99},
            {"state": "up", "value": 1, "timestamp": 99},
        ]
        self.assertEqual(w.current_state(rows, 100, 60, True), "UP")

    def test_current_state_uses_latest_observation_after_a_transition(self):
        rows = [
            {"state": "down", "value": 1, "timestamp": 80},
            {"state": "up", "value": 1, "timestamp": 99},
        ]
        self.assertEqual(w.current_state(rows, 100, 60, True), "UP")

    def test_conflicting_or_stale_states_remain_unknown_or_no_data(self):
        rows = [
            {"state": "down", "value": 1, "timestamp": 99},
            {"state": "up", "value": 1, "timestamp": 99},
        ]
        self.assertEqual(w.current_state(rows, 100, 60, True), "UNKNOWN")
        self.assertEqual(w.current_state(rows, 1000, 60, True), "NO DATA")

    def test_availability_uses_valid_observations_and_reports_coverage(self):
        r = w.summarize(
            [{"timestamp": 0, "state": "UP"}, {"timestamp": 20, "state": "DOWN"}],
            0,
            100,
            max_hold=20,
        )
        self.assertEqual(r["availability_percent"], 50)
        self.assertEqual(r["valid_state_coverage_percent"], 40)
        self.assertEqual(r["unknown_or_unobserved_seconds"], 60)
        self.assertTrue(r["outages"][0]["end_censored"])

    def test_gap_does_not_bridge_two_down_observation_runs(self):
        r = w.summarize(
            [{"timestamp": 0, "state": "DOWN"}, {"timestamp": 80, "state": "DOWN"}],
            0,
            100,
            max_hold=20,
        )
        self.assertEqual(len(r["outages"]), 2)
        self.assertEqual(r["down_observed_seconds"], 40)

    def test_observed_recovery_is_distinct_from_censored_end(self):
        r = w.summarize(
            [{"timestamp": 0, "state": "DOWN"}, {"timestamp": 20, "state": "UP"}],
            0,
            40,
            max_hold=20,
        )
        self.assertFalse(r["outages"][0]["end_censored"])
        self.assertEqual(r["outages"][0]["observed_seconds"], 20)

    def test_error_samples_do_not_enter_up_down_denominator(self):
        r = w.summarize([{"timestamp": 0, "state": "ERROR"}], 0, 20, max_hold=20)
        self.assertIsNone(r["availability_percent"])
        self.assertEqual(r["sample_coverage_percent"], 100)
        self.assertEqual(r["valid_state_coverage_percent"], 0)

    def test_degradation_requires_observed_up_state(self):
        r = w.summarize(
            [
                {"timestamp": 0, "state": "UP", "latency_ms": 200},
                {"timestamp": 20, "state": "UP", "packet_loss_pct": 6},
                {"timestamp": 40, "state": "DOWN", "packet_loss_pct": 100},
            ],
            0,
            60,
            max_hold=20,
        )
        self.assertEqual(r["degradation"][0]["observed_seconds"], 40)
        self.assertEqual(r["degradation"][0]["reasons"], ["HIGH_LATENCY", "HIGH_PACKET_LOSS"])
        self.assertEqual(r["down_observed_seconds"], 20)

    def test_active_route_is_not_guessed_from_internet_health(self):
        self.assertIsNone(w.route_evidence({}, ["wan-a"], "1.1.1.1")["interface"])
        result = w.route_evidence(
            {"status": "success", "results": {"success": True, "interface": "wan-a"}},
            ["wan-a"],
            "1.1.1.1",
        )
        self.assertEqual(result["interface"], "wan-a")
        self.assertEqual(
            w.route_evidence(
                {
                    "status": "success",
                    "results": {"success": True, "interface": "virtual-wan-link"},
                },
                ["wan-a"],
                "1.1.1.1",
            )["status"],
            "UNKNOWN",
        )


STEP = 600


def check(n, loss=0.0, *, down=None, up=None, bad=None):
    """One health check over ``n`` ten-minute buckets; per-bucket overrides by index."""
    down, up, bad = down or {}, up or {}, bad or {}
    losses = loss if isinstance(loss, list) else [loss] * n
    return {
        "loss_avg": list(losses),
        "loss_max": list(losses),
        "loss_bad": [bad.get(i, 0) for i in range(n)],
        "down_max": [1 if down.get(i) else 0 for i in range(n)],
        "down_avg": [down.get(i, 0) for i in range(n)],
        "up_avg": [up.get(i, 0 if down.get(i) else 1) for i in range(n)],
        "latency": [0.02] * n,
    }


def line(checks, sessions):
    n = len(sessions)
    return {
        "checks": checks,
        "sessions": sessions,
        "peak_sessions": sessions,
        "rx_bytes": [1e6] * n,
        "tx_bytes": [5e5] * n,
    }


def kinds(result):
    return [(f["kind"], f["interfaces"]) for f in result["findings"]]


class WanInsightTests(unittest.TestCase):
    def test_missing_evidence_is_no_evidence_not_down(self):
        r = w.insights(
            {"wan-a": line({"g": {k: [None] * 6 for k in check(6)}}, [None] * 6)},
            0,
            STEP,
            6,
            check="g",
        )
        self.assertEqual(r["status"], "UNAVAILABLE")
        self.assertEqual(r["links"][0]["states"], "nnnnnn")
        self.assertEqual(r["links"][0]["summary"]["down_minutes"], 0)
        self.assertEqual(r["incidents"], [])
        self.assertEqual(kinds(r), [("no_evidence", ["wan-a"])])

    def test_a_quiet_period_says_so(self):
        r = w.insights(
            {
                "wan-a": line({"g": check(12)}, [50] * 12),
                "wan-b": line({"g": check(12)}, [40] * 12),
            },
            0,
            STEP,
            12,
            check="g",
        )
        self.assertEqual([l["states"] for l in r["links"]], ["g" * 12, "g" * 12])
        self.assertEqual(kinds(r), [("clear", [])])

    def test_down_needs_every_check_to_agree(self):
        n = 12
        dead_ping = check(n, down={i: 1 for i in range(2, 10)})
        for i in range(2, 10):
            dead_ping["loss_max"][i] = dead_ping["loss_avg"][i] = 1.0
        r = w.insights(
            {"wan-a": line({"ping": dead_ping, "google": check(n)}, [100] * n)},
            0,
            STEP,
            n,
            check="ping",
        )
        link = r["links"][0]
        # The broken check's 100% loss is not the line's loss: the alive check judges.
        self.assertEqual(link["states"], "g" * n)
        self.assertEqual(link["disagree"], "..pppppppp..")
        self.assertEqual(link["summary"]["down_minutes"], 0)
        self.assertEqual(link["summary"]["disagree_minutes"], 80)
        self.assertEqual(link["summary"]["avg_loss_pct"], 0)
        finding = r["findings"][0]
        self.assertEqual(finding["kind"], "checks_disagree")
        self.assertIn(
            "“ping” reported wan-a dead while “google” reported it alive", finding["detail"]
        )

    def test_a_line_every_check_calls_dead_is_down(self):
        n = 6
        r = w.insights(
            {
                "wan-a": line(
                    {"ping": check(n, down={2: 1, 3: 1}), "google": check(n, down={2: 1, 3: 1})},
                    [10] * n,
                )
            },
            0,
            STEP,
            n,
            check="google",
            incident_seconds=1200,
        )
        self.assertEqual(r["links"][0]["states"], "ggddgg")
        self.assertEqual(r["links"][0]["summary"]["down_minutes"], 20)
        self.assertEqual(
            [(x["interface"], x["state"], x["start"], x["end"]) for x in r["incidents"]],
            [("wan-a", "DOWN", 1200, 2400)],
        )

    def test_short_trouble_is_not_an_incident(self):
        n = 6
        loss = [0, 0.2, 0, 0, 0, 0]
        r = w.insights({"wan-a": line({"g": check(n, loss)}, [10] * n)}, 0, STEP, n, check="g")
        self.assertEqual(r["links"][0]["states"], "gwgggg")
        self.assertEqual(r["incidents"], [])

    def test_a_line_outside_the_configured_check_falls_back_and_says_so(self):
        n = 6
        r = w.insights(
            {
                "wan-a": line({"google": check(n), "ping": check(n)}, [10] * n),
                "wan-b": line({"ping": check(n)}, [10] * n),
            },
            0,
            STEP,
            n,
            check="google",
        )
        self.assertEqual(
            [(l["interface"], l["check"], l["check_fallback"]) for l in r["links"]],
            [("wan-a", "google", False), ("wan-b", "ping", True)],
        )
        self.assertIn(("coverage", ["wan-b"]), kinds(r))

    def test_loss_that_rises_with_load_is_reported_on_the_busy_line(self):
        n = 72
        sessions = [200] * 24 + [1500] * 48
        loss = [0.001] * 24 + [0.03] * 48
        r = w.insights(
            {
                "busy": line({"g": check(n, loss)}, sessions),
                "calm": line({"g": check(n)}, [100] * n),
            },
            0,
            STEP,
            n,
            check="g",
        )
        busy = r["links"][0]
        self.assertEqual(
            [(b["min"], b["max"], b["samples"]) for b in busy["loss_by_load"]],
            [(100, 500, 24), (1000, 2000, 48)],
        )
        self.assertAlmostEqual(busy["summary"]["session_share_pct"], 1066.666 / 11.66666, 1)
        finding = r["findings"][0]
        self.assertEqual((finding["kind"], finding["interfaces"]), ("load", ["busy"]))
        self.assertIn(
            "Below 500 sessions busy loses 0.10% of packets; above that it loses 3.0%",
            finding["detail"],
        )
        self.assertNotIn(("unstable", ["busy"]), kinds(r))

    def test_loss_at_low_load_points_at_the_line(self):
        n = 36
        r = w.insights(
            {
                "flaky": line({"g": check(n, 0.02, bad={i: 10 for i in range(n)})}, [90] * n),
                "fine": line({"g": check(n)}, [100] * n),
            },
            0,
            STEP,
            n,
            check="g",
        )
        flaky = next(l for l in r["links"] if l["interface"] == "flaky")
        self.assertEqual(flaky["summary"]["loss_minutes"], 360)
        finding = r["findings"][0]
        self.assertEqual((finding["kind"], finding["interfaces"]), ("unstable", ["flaky"]))
        self.assertEqual(finding["title"], "flaky loses packets often, even with little traffic")

    def test_lines_that_fail_together_are_linked(self):
        n = 24

        def bad(*runs):
            loss = [0.0] * n
            for i in runs:
                loss[i] = 0.1
            return loss

        together = bad(2, 3, 4, 12, 13, 14, 20, 21, 22)
        r = w.insights(
            {
                "wan-a": line({"g": check(n, together)}, [10] * n),
                "wan-b": line({"g": check(n, list(together))}, [10] * n),
                "wan-c": line({"g": check(n, bad(2, 3, 4))}, [10] * n),
            },
            0,
            STEP,
            n,
            check="g",
        )
        self.assertEqual(len(r["incidents"]), 7)
        first = r["incidents"][0]
        self.assertEqual(
            {o["interface"]: o["affected_fraction"] for o in first["others"]},
            {"wan-b": 1.0, "wan-c": 1.0},
        )
        self.assertAlmostEqual(first["peak_loss_pct"], 10)
        shared = [f for f in r["findings"] if f["kind"] == "shared"]
        # wan-c failed with the others once: a coincidence, not a pattern.
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]["interfaces"], ["wan-a", "wan-b"])
        self.assertEqual(shared[0]["title"], "wan-a and wan-b often lose packets at the same time")
        self.assertTrue(
            shared[0]["detail"].startswith("3 incidents on these two lines overlapped.")
        )

    def test_a_bad_window_is_refused(self):
        with self.assertRaises(ValueError):
            w.insights({}, 0, 0, 10)


class WanInsightServiceTests(unittest.TestCase):
    """The service turns Prometheus range queries into per-line series for insights()."""

    VALUES = {
        "loss_bad": 0,
        "loss_max": 0.01,
        "loss_avg": 0.005,
        "down_max": 0,
        "down_avg": 0,
        "up_avg": 1,
        "latency": 0.03,
        "peak_sessions": 40,
        "sessions": 25,
        "rx_bytes": 250000,
        "tx_bytes": 125000,
    }

    @staticmethod
    def field(query):
        if "active_sessions" in query:
            return "peak_sessions" if "max_over_time" in query else "sessions"
        if "rx_byte" in query or "tx_byte" in query:
            return "rx_bytes" if "rx_byte" in query else "tx_bytes"
        if "latency" in query:
            return "latency"
        if "packet_loss" in query:
            return {"sum": "loss_bad", "max": "loss_max", "avg": "loss_avg"}[query[:3]]
        state = "up" if 'state="up"' in query else "down"
        return state + ("_max" if query.startswith("max") else "_avg")

    def engine(self):
        import accounting as a
        import usage_service as u
        from test_accounting import CONFIG, INVENTORY

        config = {**CONFIG, "fortigate_host": "fw:443", "sla": "google"}
        store = a.Store(":memory:", config, INVENTORY, storage_format="compact")
        self.addCleanup(store.close)
        self.calls = []

        def fetch(base, route, params=None, **_):
            self.calls.append((route, params))
            start, end, step = (int(params[k]) for k in ("start", "end", "step"))
            value = self.VALUES[self.field(params["query"])]
            points = [[t, str(value)] for t in range(start, end + 1, step)]
            points[0][1] = "NaN"  # a non-finite sample is missing evidence
            result = [
                {
                    "metric": {"interface": "uplink-a", "vdom": "root", "sla": "google"},
                    "values": points,
                },
                {
                    "metric": {"interface": "uplink-a", "vdom": "other", "sla": "google"},
                    "values": points,
                },
                {"metric": {"interface": "lab", "vdom": "root", "sla": "google"}, "values": points},
            ]
            return {"status": "success", "data": {"resultType": "matrix", "result": result}}

        return u.Engine(store, fetch=fetch)

    def test_a_week_is_read_as_ten_minute_intervals_per_line(self):
        engine = self.engine()
        now = 600_000_000 + 123
        r = engine.wan_insights(7 * 86400, now=now)
        end = 600_000_000
        start = end - 1008 * 600
        self.assertEqual((r["start"], r["step"], r["buckets"]), (start, 600, 1008))
        self.assertEqual(len(self.calls), 11)
        route, params = self.calls[0]
        self.assertEqual(route, "/api/v1/query_range")
        self.assertEqual(
            (params["start"], params["end"], params["step"]), (str(start + 600), str(end), "600")
        )
        self.assertTrue(all('instance="fw:443"' in p["query"] for _, p in self.calls))
        links = {l["interface"]: l for l in r["links"]}
        self.assertEqual(sorted(links), ["uplink-a", "uplink-b"])
        a = links["uplink-a"]
        self.assertEqual((a["check"], a["check_fallback"]), ("google", False))
        self.assertEqual(a["states"], "n" + "g" * 1007)
        self.assertAlmostEqual(a["summary"]["avg_loss_pct"], 0.5)
        self.assertAlmostEqual(a["summary"]["avg_download_mbps"], 2.0)
        self.assertEqual(a["summary"]["peak_sessions"], 40)
        self.assertEqual(links["uplink-b"]["states"], "n" * 1008)
        self.assertIn(
            "uplink-b", [f["interfaces"][0] for f in r["findings"] if f["kind"] == "no_evidence"]
        )

    def test_results_are_reused_within_an_interval_and_windows_are_bounded(self):
        import usage_service as u

        engine = self.engine()
        engine.wan_insights(86400, now=600_000_000)
        engine.wan_insights(86400, now=600_000_000 + 30)
        self.assertEqual(len(self.calls), 11)
        r = engine.wan_insights(86400, now=600_000_000 + 130)
        self.assertEqual(len(self.calls), 22)
        self.assertEqual((r["step"], r["buckets"]), (120, 720))
        for seconds in (60, 8 * 86400):
            with self.assertRaises(u.ServiceError):
                engine.wan_insights(seconds, now=600_000_000)

    def test_the_portal_forwards_the_insights_route(self):
        import portal

        self.assertIn("/api/wan/insights", portal.REPORT_ROUTES)


if __name__ == "__main__":
    unittest.main()
