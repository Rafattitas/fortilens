"""Draft readiness and applied diagnostics use synthetic, disposable state."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    import test_runtime_errors as fixtures

    from management.common import atomic
except ImportError:
    raise unittest.SkipTest("Management test dependencies are required.")


class RepairRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.RuntimeErrorTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.c = self.fixture.config
        self.ops = self.fixture.operations

    def wan_draft(self):
        view = self.c.view()
        settings = dict(view["settings"])
        settings.update(
            fortigate_host="192.0.2.1", sla="ISP", wan_interfaces=["uplink-a"], wan_confirmed=True
        )
        return self.ops.dispatch(
            "save",
            {
                "revision": view["revision"],
                "settings": settings,
                "api_token": "synthetic-token",
                "replace_token": True,
            },
        )

    def test_readiness_requires_discovery_after_connection_or_credential_change(self):
        saved = self.wan_draft()
        self.assertTrue(saved["discovery"]["required"])
        self.assertFalse(saved["discovery"]["current"])
        result = self.ops.dispatch("discover", {"revision": saved["revision"]})
        self.assertTrue(result["discovery"]["current"])
        view = self.ops.dispatch("view", {})
        saved = self.ops.dispatch(
            "save",
            {
                "revision": view["revision"],
                "settings": view["settings"],
                "api_token": "synthetic-replacement",
                "replace_token": True,
            },
        )
        self.assertFalse(saved["discovery"]["current"])
        self.assertNotIn("synthetic-replacement", str(saved["discovery"]))

    def test_corrupt_discovery_record_is_actionable_and_not_overwritten(self):
        self.wan_draft()
        path = self.c.control / "inventory.json"
        atomic(path, ["invalid"])
        self.assertFalse(self.ops.view()["discovery"]["current"])
        self.assertTrue(path.exists())

    def test_diagnostics_observe_applied_connection_not_unapplied_tls_draft(self):
        self.fixture.plan(existing=True)
        view = self.c.view()
        settings = dict(view["settings"])
        settings["api_tls_verify"] = False
        self.c.save(
            {"revision": view["revision"], "settings": settings, "acknowledge_insecure_tls": True}
        )
        with patch(
            "management.runtime.doctor.run", return_value={"checks": [], "status": "UNKNOWN"}
        ) as run:
            result = self.ops.diagnostics()
        self.assertTrue(run.call_args.args[1]["api_tls_verify"])
        self.assertEqual(result["observed_revision"], self.c.state()["applied_revision"])
        self.assertEqual(result["saved_revision"], self.c.state()["revision"])

    def test_completed_jobs_record_requested_and_applied_revision(self):
        request = self.fixture.plan(existing=True)
        with patch.object(self.ops, "diagnostics", return_value={"checks": []}):
            job = self.ops.apply(request)
            result = self.fixture.wait(job["job_id"])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["requested_revision"], request.revision)
        self.assertEqual(result["applied_revision"], self.c.state()["applied_revision"])
