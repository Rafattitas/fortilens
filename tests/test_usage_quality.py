"""Period, duplicate and identity evidence must not become coverage guarantees."""
from copy import deepcopy
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import usage_quality as usage
spec = importlib.util.spec_from_file_location("usage_setup", ROOT / "portable/setup.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)
AT = "2026-10-28T12:00:00Z"


def record(stamp="2026-10-28T11:00:00Z", **fields):
    return {"timestamp": stamp, "fields": {"type": "traffic", "subtype": "forward", "vd": "root",
        "logid": "0000000020", "dstintfrole": "wan", "mastersrcmac": "02:00:00:00:00:01",
        "srcip": "192.0.2.20", "srcname": "demo-device", "sessionid": "100",
        "sentdelta": "100", "rcvddelta": "200", **fields}}


def report(records, **options):
    return usage.audit(records, **{"period": "today", "zone": "UTC", "at": AT, "vdom": "root", **options})


class UsageQualityTests(unittest.TestCase):
    def test_zero_delta_is_not_replaced_by_cumulative(self):
        r = report([record(logid="0000000013", sentdelta="0", rcvddelta="0", sentbyte="9000", rcvdbyte="8000")])
        self.assertEqual(r["sample_recorded_bytes"], 0)
        self.assertEqual(r["categories"], {"included_delta": 1})

    def test_end_only_and_mixed_direction_fallback(self):
        r = report([record(logid="0000000013", sentdelta="", sentbyte="400"),
                    record("2026-10-28T11:01:00Z", logid="0000000013", sentdelta="", rcvddelta="", sentbyte="5", rcvdbyte="6")])
        self.assertEqual(r["sample_recorded_bytes"], 611)
        self.assertEqual(r["categories"], {"included_cumulative_fallback": 2})

    def test_stats_without_delta_and_invalid_deltas_are_excluded(self):
        r = report([record(sentdelta="", sentbyte="9000"), record(sentdelta="-1"), record(sentdelta="bad"), record(sentdelta="1.5")])
        self.assertEqual(r["categories"], {"excluded_invalid_bytes": 4})
        self.assertIsNone(r["sample_recorded_bytes"])

    def test_cumulative_is_never_added_to_existing_delta(self):
        r = report([record(sentbyte="100000", rcvdbyte="200000"),
                    record("2026-10-28T11:01:00Z", logid="0000000013", sentdelta="10", rcvddelta="20", sentbyte="100010", rcvdbyte="200020")])
        self.assertEqual(r["sample_recorded_bytes"], 330)

    def test_replayed_sample_is_flagged_but_dashboard_total_not_silently_deduplicated(self):
        sample = record()
        r = report([sample, deepcopy(sample)])
        self.assertEqual(r["sample_recorded_bytes"], 600)
        self.assertEqual(r["replay_candidate_records"], 1)
        self.assertEqual(r["replay_candidate_bytes"], 300)
        self.assertEqual(r["coverage"], "UNKNOWN")

    def test_same_session_different_records_are_not_duplicate_candidates(self):
        r = report([record(), record("2026-10-28T11:00:01Z"), record(sentdelta="10")])
        self.assertEqual(r["replay_candidate_records"], 0)

    def test_ip_and_name_changes_are_flagged_without_unverified_person_merge(self):
        r = report([record(), record("2026-10-28T11:01:00Z", srcip="192.0.2.21", srcname="demo-renamed")])
        self.assertEqual(r["macs_with_multiple_ip_name_combinations"], 1)
        text = json.dumps(r)
        for value in ("192.0.2.20", "demo-device", "02:00:00:00:00:01"):
            self.assertNotIn(value, text)

    def test_different_macs_with_same_ip_are_not_merged(self):
        r = report([record(), record(mastersrcmac="02:00:00:00:00:02")])
        self.assertEqual(r["macs_with_multiple_ip_name_combinations"], 0)
        self.assertEqual(r["replay_candidate_records"], 0)

    def test_missing_mac_role_and_log_type_are_separate(self):
        r = report([record(mastersrcmac=""), record(dstintfrole="undefined"), record(logid="0000000011"), record(type="event")])
        self.assertEqual(set(r["categories"]), {"excluded_missing_mac", "excluded_role", "excluded_event", "excluded_type"})

    def test_vdom_scope_and_unscoped_evidence(self):
        r = report([record(vd="other"), record(vd=""), record(vd="other", parse_error="true")])
        self.assertEqual(r["categories"], {"unscoped_missing_vdom": 1, "unscoped_parse_error": 1})
        self.assertIsNone(r["sample_recorded_bytes"])

    def test_empty_input_is_unavailable_not_zero_or_complete(self):
        r = report([])
        self.assertEqual(r["status"], "UNAVAILABLE")
        self.assertIsNone(r["sample_recorded_bytes"])
        self.assertIsNone(r["first_observed_record_utc"])
        self.assertEqual(r["coverage"], "UNKNOWN")

    def test_exact_time_edges_match_loki_start_exclusive_end_inclusive(self):
        r = report([record("2026-10-28T00:00:00Z"), record(AT), record("2026-10-28T12:00:00.000001Z")])
        self.assertEqual(r["sample_recorded_bytes"], 300)
        self.assertEqual(r["outside_window"], 2)

    def test_rolling_week_is_168_hours_and_calendar_month_is_not_30_days(self):
        a, b, _ = usage.window("week", "UTC", "2026-10-28T12:00:00Z")
        self.assertEqual((b-a).total_seconds(), 168*3600)
        a, _, _ = usage.window("month", "UTC", "2026-03-31T12:00:00Z")
        self.assertEqual(a.isoformat(), "2026-03-01T00:00:00+00:00")
        a, _, _ = usage.window("month", "UTC", "2028-02-29T12:00:00Z")
        self.assertEqual(a.isoformat(), "2028-02-01T00:00:00+00:00")

    def test_cairo_calendar_boundary_uses_timezone_not_utc_midnight(self):
        try:
            ZoneInfo("Africa/Cairo")
        except ZoneInfoNotFoundError:
            self.skipTest("IANA database unavailable on this configuration-generation host")
        r = report([record("2026-09-30T22:00:00Z", logid="0000000013", sentdelta="", rcvddelta="", sentbyte="7", rcvdbyte="9")],
                   period="month", zone="Africa/Cairo", at="2026-10-01T01:01:00+03:00")
        self.assertEqual(r["period"]["start_utc"], "2026-09-30T21:00:00+00:00")
        self.assertEqual(r["sample_recorded_bytes"], 16)
        self.assertTrue(any("not redistributed" in w for w in r["warnings"]))

    def test_unknown_browser_timezone_and_naive_timestamps_are_rejected(self):
        for change in ({"zone": "browser"}, {"zone": "Invalid/Zone"}, {"at": "2026-10-28T12:00:00"}):
            with self.assertRaises(ValueError):
                report([], **change)

    def test_session_crossing_week_boundary_counts_only_received_in_window_delta(self):
        r = report([record("2026-10-21T11:59:59Z", sentdelta="1000", rcvddelta="2000"),
                    record("2026-10-21T12:00:01Z", logid="0000000013", sentdelta="10", rcvddelta="20", sentbyte="1010", rcvdbyte="2020")], period="week")
        self.assertEqual(r["sample_recorded_bytes"], 30)
        self.assertEqual(r["outside_window"], 1)

    def test_invalid_input_errors_and_reports_hide_extra_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)/"input.json"
            p.write_text('{"secret":"DO_NOT_ECHO" BROKEN', encoding="utf-8")
            with self.assertRaises(ValueError) as e:
                usage.audit_file(p, period="today", zone="UTC", at=AT, vdom="root")
            self.assertNotIn("DO_NOT_ECHO", str(e.exception))
        r = report([record(token="DO_NOT_ECHO")])
        self.assertNotIn("DO_NOT_ECHO", json.dumps(r))
        for value in (True, 100, None, {}):
            with self.assertRaises(ValueError):
                report([record(sentdelta=value)])

    def test_oversized_integer_is_not_claimed_as_python_reference_match(self):
        r = report([record(sentdelta="9"*30)])
        self.assertIsNone(r["sample_recorded_bytes"])
        self.assertTrue(any("Oversized" in w for w in r["warnings"]))

    def test_cli_is_offline_and_has_no_success_coverage_exit_code(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"sample.json"
            p.write_text(json.dumps({"records": [record()]}), encoding="utf-8")
            with patch.object(setup, "grafana_request", side_effect=AssertionError("No network")), contextlib.redirect_stdout(io.StringIO()) as out:
                code = setup.main(["usage-audit", "--input", str(p), "--period", "week", "--timezone", "UTC", "--vdom", "root", "--at", AT, "--json"])
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out.getvalue())["coverage"], "UNKNOWN")

    def test_dashboard_adds_collapsed_diagnostics_and_preserves_existing_measurements(self):
        source = setup.load(ROOT/"portable/templates/dashboard.json")
        candidate = usage.enhance_dashboard(source)
        self.assertEqual(source["spec"]["elements"], {k: v for k, v in candidate["spec"]["elements"].items() if k in source["spec"]["elements"]})
        row = next(r["spec"] for r in candidate["spec"]["layout"]["spec"]["rows"] if r["spec"]["title"].startswith("Usage Data Quality"))
        self.assertTrue(row["collapse"])
        self.assertEqual(usage.enhance_dashboard(candidate), candidate)
        for pid, relative in ((43,"now/d"),(44,"7d"),(45,"now/M")):
            panel=candidate["spec"]["elements"][f"panel-{pid}"]["spec"]
            self.assertEqual(panel["data"]["spec"]["queryOptions"]["timeFrom"], relative)
            self.assertNotIn("vector(0)", panel["data"]["spec"]["queries"][0]["spec"]["query"]["spec"]["expr"])

    def test_candidate_command_preserves_source_and_never_calls_grafana(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"installation"
            with contextlib.redirect_stdout(io.StringIO()):
                setup.render({"fortigate_host":"fw.example.test"}, p, "FAKE_TEST_TOKEN")
            source=setup.load(ROOT/"portable/templates/dashboard.json")
            path=p/"grafana/dashboard.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            before=path.read_bytes()
            with patch.object(setup,"grafana_request",side_effect=AssertionError("No network")), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(setup.main(["prepare-usage-quality","--output",str(p)]),0)
            self.assertEqual(path.read_bytes(),before)
            candidates=list((p/"grafana").glob("dashboard.usage-quality-*.json"))
            self.assertEqual(len(candidates),1)
            self.assertEqual(len(setup.load(candidates[0])["spec"]["elements"]),30)
