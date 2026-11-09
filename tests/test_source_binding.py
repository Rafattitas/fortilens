"""Binding an existing accounting store to the FortiGate's serial, forward only."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portable"))
import accounting as a
sys.path.insert(0, str(ROOT / "tests"))
from test_accounting import CONFIG, INVENTORY, record  # noqa: E402

BOUND = {**CONFIG, "expected_devid": "FG-FIXTURE"}
START, END = "2026-10-20T00:00:00Z", "2026-10-21T00:00:00Z"


class ContractTests(unittest.TestCase):
    def test_only_forward_moves_are_allowed(self):
        old = {"accounting_timezone": "UTC", "source_id": "fixture", "vdom": "root", "expected_devid": ""}
        allowed = a.contract_change_allowed
        self.assertTrue(allowed(None, old))
        self.assertTrue(allowed(old, old))
        self.assertTrue(allowed(old, {**old, "expected_devid": "FG-1"}))
        self.assertTrue(allowed({**old, "expected_devid": None}, {**old, "expected_devid": "FG-1"}))
        self.assertTrue(allowed(old, {**old, "expected_devid": "FG-1", "accounting_timezone": "fortigate"}))
        bound = {**old, "expected_devid": "FG-1"}
        for change in ({"expected_devid": "FG-2"}, {"expected_devid": ""}, {"vdom": "other"},
                       {"source_id": "other"}, {"accounting_timezone": "Africa/Cairo"}):
            self.assertFalse(allowed(bound, {**bound, **change}), change)
        self.assertFalse(allowed({**old, "accounting_timezone": "fortigate"}, old))
        self.assertFalse(a.follows_fortigate_from(old, {**old, "expected_devid": "FG-1"}))
        self.assertTrue(a.follows_fortigate_from(old, {**old, "expected_devid": "FG-1", "accounting_timezone": "fortigate"}))


class BindingTests(unittest.TestCase):
    def test_an_unbound_store_binds_its_serial_and_keeps_counted_usage(self):
        for storage_format in (None, "compact"):
            with self.subTest(storage_format=storage_format):
                self.bind(storage_format)

    def bind(self, storage_format):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "usage.sqlite"
            store = a.Store(path, CONFIG, INVENTORY, storage_format=storage_format)
            try:
                store.ingest([record(sessionid="1"), record(sessionid="2", devid="FG-OTHER")])
                before = store.report(START, END)
            finally:
                store.close()
            # Unbound, the other FortiGate's session is counted too.
            self.assertEqual(before["source_record_classifications"].get("INTERNET"), 2)
            bound = a.Store(path, BOUND, INVENTORY)
            try:
                self.assertEqual(bound.state("contract")["expected_devid"], "FG-FIXTURE")
                self.assertTrue(bound.diagnostics()["source_identity_bound"])
                bound.ingest([record("2026-10-20T13:00:00Z", sessionid="3"),
                              record("2026-10-20T13:00:00Z", sessionid="4", devid="FG-OTHER")])
                after = bound.report(START, END)
            finally:
                bound.close()
            kinds = after["source_record_classifications"]
            self.assertEqual(kinds.get("INTERNET"), 3)
            self.assertEqual(kinds.get("UNSCOPED_SOURCE"), 1)
            reader = a.Store.open_readonly(path, BOUND, INVENTORY)
            reader.close()
            # Once bound, removing or changing the serial is refused.
            for config in (CONFIG, {**CONFIG, "expected_devid": "FG-OTHER"}):
                with self.assertRaises(ValueError):
                    a.Store(path, config, INVENTORY)
                with self.assertRaises(ValueError):
                    a.Store.open_readonly(path, config, INVENTORY)


if __name__ == "__main__":
    unittest.main()
