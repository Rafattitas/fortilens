"""Accounting acceptance cases use synthetic endpoints and in-memory/local SQLite."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import accounting as a
import interfaces

CONFIG = {"source_id": "fixture", "vdom": "root", "accounting_timezone": "UTC", "confirmed_wan_interfaces": ["uplink-a", "uplink-b"]}
INVENTORY = {"interfaces": [
    {"name": "staff", "vdom": "root", "type": "vlan", "role": "lan", "parent": "port1", "vlan_id": 100, "issues": []},
    {"name": "guests", "vdom": "root", "type": "vlan", "role": "lan", "parent": "port1", "vlan_id": 200, "issues": []},
    {"name": "uplink-a", "vdom": "root", "type": "physical", "role": "wan", "parent": None, "vlan_id": None, "issues": []}]}


def record(at="2026-10-20T12:00:00Z", **fields):
    return {"timestamp": at, "fields": {"devid": "FG-FIXTURE", "vd": "root", "type": "traffic", "subtype": "forward", "logid": "0000000020", "srcip": "192.0.2.10", "dstip": "198.51.100.10", "srcport": "1234", "dstport": "443", "proto": "6", "sessionid": "10", "srcintf": "staff", "dstintf": "uplink-a", "srcintfrole": "lan", "dstintfrole": "wan", "mastersrcmac": "02:00:00:00:00:01", "srcname": "fixture", "sentdelta": "100", "rcvddelta": "200", "duration": "60", **fields}}


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.store = a.Store(":memory:", CONFIG, INVENTORY)
        self.addCleanup(self.store.close)

    def report(self, **kwargs):
        return self.store.report("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z", **kwargs)

    def test_direction_and_decimal_gb_reference(self):
        self.store.ingest([record(sentdelta="1000000000", rcvddelta="2000000000")])
        self.assertEqual(self.report()["totals"]["upload_bytes"], 1000000000)
        self.assertEqual(self.report()["totals"]["download_bytes"], 2000000000)

    def test_changed_mac_cannot_inherit_another_endpoints_cumulative_baseline(self):
        self.store.ingest([record(sentbyte='100',rcvdbyte='200'),record('2026-10-20T12:01:00Z',
            mastersrcmac='02:00:00:00:00:02',duration='120',logid='0000000013',sentdelta='',rcvddelta='',sentbyte='500',rcvdbyte='900')])
        result=self.report()
        self.assertEqual(result['totals']['upload_bytes'],100)
        self.assertEqual(result['totals']['pending_upload_bytes'],500)
        self.assertIn('session_endpoint_changed',result['flags'])

    def test_future_metadata_cannot_certify_historical_direction_or_vlan(self):
        self.store.inventory={**INVENTORY,'collected_at':'2026-10-21T00:00:00Z'}
        self.store.ingest([record(srcintfrole='',dstintfrole='')])
        result=self.report()
        self.assertIsNone(result['totals']['upload_bytes'])
        self.assertEqual(result['source_record_classifications'],{'UNRESOLVED_DIRECTION':1})
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],1)

    def test_excluded_classifications_are_visible_and_retries_do_not_inflate_counts(self):
        records=[record(),record(sessionid='11',dstintf='guests',dstintfrole='lan')]
        self.store.ingest(records);self.store.ingest(records)
        result=self.report()
        self.assertEqual(result['source_record_classifications'],{'INTERNET':1,'INTERNAL':1})
        self.assertEqual(self.report(search='no matching device')['source_record_classifications'],result['source_record_classifications'])

    def test_explicit_zero_does_not_fall_back(self):
        self.store.ingest([record(logid="0000000013", sentdelta="0", rcvddelta="0", sentbyte="9000", rcvdbyte="8000")])
        self.assertEqual(self.report()["totals"]["upload_bytes"], 0)
        self.assertEqual(self.report()["totals"]["download_bytes"], 0)
        self.assertEqual(self.report()["observed_endpoint_identities"], 1)

    def test_missing_mac_uses_session_not_global_ip_identity(self):
        self.store.ingest([record(mastersrcmac=""), record("2026-10-20T13:00:00Z", mastersrcmac="", sessionid="11")])
        r = self.report()
        self.assertEqual(r["observed_endpoint_identities"], 2)
        self.assertEqual(r["unverified_endpoint_identities"], 2)
        self.assertEqual(r["totals"]["upload_bytes"], 200)

    def test_ip_without_session_start_evidence_is_never_a_persistent_device(self):
        self.store.ingest([record(mastersrcmac="", duration=""), record("2026-10-20T12:00:01Z", mastersrcmac="", duration="")])
        self.assertEqual(self.report()["observed_endpoint_identities"], 2)
        self.assertEqual({r["identity_kind"] for r in self.report()["rows"]}, {"OBSERVATION"})

    def test_srcmac_is_a_supported_fallback(self):
        self.store.ingest([record(mastersrcmac="", srcmac="02:00:00:00:00:02")])
        self.assertEqual(self.report()["rows"][0]["identity_kind"], "MAC")

    def test_invalid_mac_does_not_bypass_session_identity_reuse_checks(self):
        self.store.ingest([record(mastersrcmac='invalid'),record('2026-10-20T13:00:00Z',mastersrcmac='invalid')])
        self.assertEqual(self.report()['observed_endpoint_identities'],2)
        other=record(mastersrcmac='01:00:00:00:00:01',sessionid='other')
        self.store.ingest([other])
        self.assertTrue(all(x['identity_kind']=='IP_SESSION' for x in self.report()['rows']))

    def test_mac_does_not_split_when_name_ip_or_wan_changes(self):
        self.store.ingest([record(), record("2026-10-20T13:00:00Z", srcip="192.0.2.20", srcname="renamed", dstintf="uplink-b", sessionid="11")])
        self.assertEqual(self.report()["observed_endpoint_identities"], 1)
        self.assertEqual(self.report()["totals"]["download_bytes"], 400)

    def test_stats_then_end_without_delta_counts_only_counter_change(self):
        self.store.ingest([record(sentbyte="100", rcvdbyte="200"),
            record("2026-10-20T12:01:00Z", duration="120", logid="0000000013", sentdelta="", rcvddelta="", sentbyte="150", rcvdbyte="300")])
        self.assertEqual(self.report()["totals"]["upload_bytes"], 150)
        self.assertEqual(self.report()["totals"]["download_bytes"], 300)

    def test_equal_timestamp_stats_precede_closing_cumulative_record(self):
        self.store.ingest([record(sentbyte='100',rcvdbyte='200'),record(logid='0000000013',sentdelta='',rcvddelta='',sentbyte='150',rcvdbyte='300')])
        self.assertEqual(self.report()['totals']['upload_bytes'],150)
        self.assertEqual(self.report()['totals']['download_bytes'],300)
        self.store.prune('2026-10-29T12:00:00Z')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM events').fetchone()[0],0)

    def test_second_closing_observation_does_not_recount_the_same_session(self):
        self.store.ingest([record(logid='0000000013',sentdelta='',rcvddelta='',sentbyte='500',rcvdbyte='900'),
            record('2026-10-20T12:01:00Z',duration='120',logid='0000000013',sentdelta='',rcvddelta='',sentbyte='500',rcvdbyte='900',idseq='second')])
        self.assertEqual(self.report()['totals']['upload_bytes'],500)
        self.assertEqual(self.report()['totals']['download_bytes'],900)
        self.assertIn('repeated_close_observation',self.report()['flags'])

    def test_end_only_without_safe_duration_baseline_is_pending(self):
        self.store.ingest([record(logid="0000000013", duration="", sentdelta="", rcvddelta="", sentbyte="500", rcvdbyte="600")])
        self.assertEqual(self.report()["totals"]["upload_bytes"], 0)
        self.assertEqual(self.report()["totals"]["pending_upload_bytes"], 500)

    def test_end_only_session_contained_in_one_day_can_use_full_session(self):
        self.store.ingest([record(logid="0000000013", sentdelta="", rcvddelta="", sentbyte="500", rcvdbyte="600")])
        self.assertEqual(self.report()["totals"]["upload_bytes"], 500)

    def test_cross_midnight_end_only_bytes_are_not_invented_for_either_day(self):
        self.store.ingest([record("2026-10-20T00:00:30Z", duration="120", logid="0000000013", sentdelta="", rcvddelta="", sentbyte="500", rcvdbyte="600")])
        self.assertEqual(self.report()["totals"]["upload_bytes"], 0)
        self.assertEqual(self.report()["totals"]["pending_download_bytes"], 600)
        self.assertIn("calendar_boundary_allocation_uncertain", self.report()["flags"])

    def test_counter_reset_does_not_subtract_or_recount_prior_session(self):
        self.store.ingest([record(sentbyte="100", rcvdbyte="200"),
            record("2026-10-20T12:01:00Z", sentdelta="10", rcvddelta="20", sentbyte="10", rcvdbyte="20", duration="5")])
        self.assertEqual(self.report()["totals"]["upload_bytes"], 110)
        self.assertIn("session_restart_or_counter_reset", self.report()["flags"])

    def test_invalid_delta_does_not_trigger_cumulative_fallback(self):
        self.store.ingest([record(logid="0000000013", sentdelta="-1", sentbyte="9000")])
        self.assertEqual(self.report()["totals"]["upload_bytes"], 0)
        self.assertIn("upload_invalid_delta", self.report()["flags"])

    def test_retries_restart_and_transport_replays_are_idempotent(self):
        item = record(eventtime="1792497600000000000")
        self.store.ingest([item], checkpoint=10)
        self.store.ingest([item], checkpoint=20)
        replay = {**item, "timestamp": "2026-10-20T12:00:05Z"}
        r = self.store.ingest([replay])
        self.assertEqual(r["new_events"], 0)
        self.assertEqual(r["new_replay_candidates"], 1)
        self.assertEqual(self.report()["totals"]["upload_bytes"], 100)
        self.assertEqual(self.store.state("checkpoint_ns"), 20)
        with tempfile.TemporaryDirectory() as path:
            filename = str(Path(path)/"usage.db")
            s = a.Store(filename, CONFIG, INVENTORY); s.ingest([record()]); s.close()
            s = a.Store(filename, CONFIG, INVENTORY); s.ingest([record()])
            self.assertEqual(s.report("2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z")["totals"]["download_bytes"], 200)
            s.close()

    def test_late_stats_recompute_session_and_replace_old_rollup(self):
        end = record("2026-10-20T12:01:00Z", logid="0000000013", duration="120", sentdelta="", rcvddelta="", sentbyte="150", rcvdbyte="300")
        self.store.ingest([end])
        self.store.ingest([record(sentbyte="100", rcvdbyte="200")])
        self.assertEqual(self.report()["totals"]["upload_bytes"], 150)
        self.assertEqual(self.report()["totals"]["download_bytes"], 300)

    def test_vlan_mapping_uses_exact_metadata_not_ip_subnets(self):
        self.store.ingest([record(), record(sessionid="11", srcintf="guests", mastersrcmac="02:00:00:00:00:02")])
        self.assertEqual({r["vlan"]["vlan_id"] for r in self.report()["rows"]}, {100, 200})
        self.assertEqual(self.report(vlan="vlan:100:staff")["totals"]["upload_bytes"], 100)
        self.assertEqual(interfaces.map_source_interface(INVENTORY, "other", "staff")["status"], "UNKNOWN")

    def test_internal_and_inbound_traffic_are_excluded_without_vanishing_evidence(self):
        self.store.ingest([record(dstintf="guests", dstintfrole="lan"), record(sessionid="11", srcintfrole="wan")])
        self.assertEqual(self.report()["status"], "UNAVAILABLE")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0], 2)

    def test_period_edges_and_custom_subday_range(self):
        self.store.ingest([record("2026-10-20T00:00:00Z"), record("2026-10-21T00:00:00Z", sessionid="11")])
        self.assertEqual(self.report()["totals"]["upload_bytes"], 100)
        self.assertEqual(self.store.report("2026-10-20T00:00:00Z", "2026-10-20T00:00:01Z")["totals"]["download_bytes"], 200)

    def test_empty_is_unknown_not_zero_and_full_days_use_materialized_summaries(self):
        self.assertIsNone(self.report()["totals"]["upload_bytes"])
        self.store.ingest([record()])
        self.store.db.execute("DELETE FROM facts"); self.store.db.commit()
        self.assertEqual(self.report()["totals"]["upload_bytes"], 100)

    def test_duplicate_log_keys_and_unscoped_timezone_are_rejected(self):
        with self.assertRaises(ValueError):a.parse_line('type="traffic" type="event"')
        with self.assertRaises(ValueError):a.Store(":memory:", {**CONFIG, "accounting_timezone": "browser"})

    def test_retention_preserves_summaries_and_open_session_baselines(self):
        self.store.ingest([record(sentbyte="100", rcvdbyte="200")])
        self.store.prune("2026-10-29T00:00:00Z", ledger_days=8)
        self.assertEqual(self.report()["totals"]["upload_bytes"], 100)
        self.store.ingest([record("2026-10-29T12:00:00Z", duration="777660", logid="0000000013", sentdelta="", rcvddelta="", sentbyte="150", rcvdbyte="300")])
        latest = self.store.report("2026-10-29T00:00:00Z", "2026-10-30T00:00:00Z")
        self.assertEqual(latest["totals"]["upload_bytes"], 50)
        self.assertEqual(self.report()["totals"]["upload_bytes"], 100)

    def test_late_records_beyond_retained_ledger_are_visible_rejections_not_recounts(self):
        self.store.ingest([record()])
        self.store.prune("2026-10-29T00:00:00Z")
        self.store.ingest([record(srcname="replayed later")], checkpoint=100)
        self.assertEqual(self.store.state("late_rejections"), 1)
        self.assertEqual(self.store.state("checkpoint_ns"), 100)
        self.assertEqual(self.report()["totals"]["upload_bytes"], 100)
        with self.assertRaises(ValueError):self.store.report("2026-10-20T11:59:00Z", "2026-10-20T12:01:00Z")

    def test_future_vlan_inventory_is_not_assumed_to_describe_historical_membership(self):
        self.store.inventory = {**INVENTORY, "collected_at": "2026-10-21T00:00:00Z"}
        self.store.ingest([record()])
        row = self.report()["rows"][0]
        self.assertEqual(row["vlan"]["status"], "UNKNOWN")
        self.assertIsNone(row["vlan"]["vlan_id"])
        self.assertIn("historical_vlan_mapping_not_verified", self.report()["flags"])

    def test_inventory_refresh_does_not_rewrite_old_evidence_on_late_arrival(self):
        self.store.ingest([record(sentbyte="100", rcvdbyte="200")])
        self.store.inventory = {"interfaces": []}
        self.store.ingest([record("2026-10-20T12:01:00Z", duration="120", sentdelta="10", rcvddelta="20", sentbyte="110", rcvdbyte="220")])
        rows = self.report()["rows"]
        self.assertEqual(len(rows), 2)
        known = next(x for x in rows if x["vlan"]["status"] == "MAPPED")
        self.assertEqual(known["upload_bytes"], 100)

    def test_timezone_contract_change_requires_explicit_rebuild(self):
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder)/"usage.db")
            store = a.Store(path, CONFIG, INVENTORY);store.ingest([record()]);store.close()
            with self.assertRaises(ValueError):a.Store(path, {**CONFIG, "accounting_timezone": "Europe/London"}, INVENTORY)


if __name__ == "__main__":
    unittest.main()
