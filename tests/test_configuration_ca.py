"""CA candidate assembly uses temporary installations, never a live firewall."""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from email.message import Message
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    from management import helper
    from management.common import Problem, load, setup
    from management.configuration import Configuration
except ImportError:
    raise unittest.SkipTest("CA tests require management/requirements-test.txt.")

# Public trust anchor, used solely as a self-contained parseable PEM fixture.
# No production certificate or private key is included.
CA = """-----BEGIN CERTIFICATE-----
MIICTjCCAdOgAwIBAgIKPPYHqWhwDtqLhDAKBggqhkjOPQQDAzBrMQswCQYDVQQG
EwJJTjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBUZWNo
bm9sb2dpZXMgTGltaXRlZDEgMB4GA1UEAxMXZW1TaWduIEVDQyBSb290IENBIC0g
RzMwHhcNMTgwMjE4MTgzMDAwWhcNNDMwMjE4MTgzMDAwWjBrMQswCQYDVQQGEwJJ
TjETMBEGA1UECxMKZW1TaWduIFBLSTElMCMGA1UEChMcZU11ZGhyYSBUZWNobm9s
b2dpZXMgTGltaXRlZDEgMB4GA1UEAxMXZW1TaWduIEVDQyBSb290IENBIC0gRzMw
djAQBgcqhkjOPQIBBgUrgQQAIgNiAAQjpQy4LRL1KPOxst3iAhKAnjlfSU2fySU0
WXTsuwYc58Byr+iuL+FBVIcUqEqy6HyC5ltqtdyzdc6LBtCGI79G1Y4PPwT01xyS
fvalY8L1X44uT6EYGQIrMgqCZH0Wk9GjQjBAMB0GA1UdDgQWBBR8XQKEE9TMipuB
zhccLikenEhjQjAOBgNVHQ8BAf8EBAMCAQYwDwYDVR0TAQH/BAUwAwEB/zAKBggq
hkjOPQQDAwNpADBmAjEAvvNhzwIQHWSVB7gYboiFBS+DCBeQyh+KTOgNG3qxrdWB
CUfvO6wIBHxcmbHtRwfSAjEAnbpV/KlK6O3t5nYBQnvI+GDZjVGLVTv7jHvrZQnD
+JbNR6iC8hZVdyR+EhCVBCyj
-----END CERTIFICATE-----
"""
TOKEN = "synthetic-ca-test-token"


class ConfigurationCATests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def existing(self, *, verify=True, ca=False):
        certificate = self.base / "fixture.pem"
        certificate.write_text(CA, encoding="utf-8")
        config = setup.validate_config(
            {
                "fortigate_host": "192.0.2.1",
                "api_tls_verify": verify,
                "api_ca_file": str(certificate) if ca else "",
                "accounting_enabled": True,
                "portal_enabled": True,
            }
        )
        with contextlib.redirect_stdout(io.StringIO()):
            setup.render(config, self.base / "stack", TOKEN)
        return Configuration(self.base / "stack", self.base / "control", project="ca-fixture")

    def save(self, config, *, verify=True, ca=None, acknowledge=True):
        state = config.view()
        settings = state["settings"].copy()
        settings["api_tls_verify"] = verify
        change = {
            "revision": state["revision"],
            "settings": settings,
            "acknowledge_insecure_tls": acknowledge,
        }
        if ca is not None:
            change.update(custom_ca=ca, replace_ca=True)
        return config.save(change)

    def prepare(self, config, view):
        with contextlib.redirect_stdout(io.StringIO()):
            review = config.prepare(view["revision"])
        folder, _ = config.plan(review["plan_id"])
        candidate = folder / "candidate"
        import yaml

        return (
            review,
            candidate,
            load(candidate / "site.json"),
            yaml.safe_load((candidate / "compose.yaml").read_text()),
        )

    def test_tls_enabled_preserves_applied_custom_ca(self):
        config = self.existing(ca=True)
        _, _, site, compose = self.prepare(config, self.save(config))
        self.assertTrue(site["api_tls_verify"])
        self.assertEqual(site["api_ca_file"], "secrets/fortigate-ca.pem")
        self.assertIn("-extra-ca-certs", compose["services"]["fortigate-exporter"]["command"])
        self.assertEqual((config.root / site["api_ca_file"]).read_text(), CA)

    def test_tls_enabled_uses_system_trust_without_ca(self):
        config = self.existing()
        _, _, site, compose = self.prepare(config, self.save(config))
        self.assertTrue(site["api_tls_verify"])
        self.assertEqual(site["api_ca_file"], "")
        self.assertNotIn("-extra-ca-certs", compose["services"]["fortigate-exporter"]["command"])

    def test_ca_replacement_is_staged_before_render_without_altering_applied_file(self):
        config = self.existing(ca=True)
        replacement = CA + "\n"
        _, candidate, site, _ = self.prepare(config, self.save(config, ca=replacement))
        self.assertEqual(site["api_ca_file"], "secrets/web-fortigate-ca.pem")
        self.assertEqual((candidate / site["api_ca_file"]).read_text(), replacement)
        self.assertEqual((config.root / "secrets/fortigate-ca.pem").read_text(), CA)

    def test_ca_removal_while_verified_uses_system_trust_and_retains_old_file(self):
        config = self.existing(ca=True)
        _, _, site, compose = self.prepare(config, self.save(config, ca=""))
        self.assertTrue(site["api_tls_verify"])
        self.assertEqual(site["api_ca_file"], "")
        self.assertNotIn("-extra-ca-certs", compose["services"]["fortigate-exporter"]["command"])
        self.assertEqual((config.root / "secrets/fortigate-ca.pem").read_text(), CA)

    def test_tls_disabled_retained_ca_is_an_actionable_conflict_not_silent_removal(self):
        config = self.existing(ca=True)
        view = self.save(config, verify=False)
        with self.assertRaises(Problem) as caught:
            config.prepare(view["revision"])
        self.assertEqual(caught.exception.code, "tls_ca_conflict")
        self.assertEqual(caught.exception.status, 400)
        self.assertIn("Explicitly remove", caught.exception.message)
        self.assertEqual((config.root / "secrets/fortigate-ca.pem").read_text(), CA)
        self.assertTrue(load(config.root / "site.json")["api_tls_verify"])
        self.assertFalse((config.control / "plans").exists())

    def test_tls_disabled_explicit_ca_removal_succeeds_before_validation(self):
        config = self.existing(ca=True)
        _, _, site, compose = self.prepare(config, self.save(config, verify=False, ca=""))
        self.assertFalse(site["api_tls_verify"])
        self.assertEqual(site["api_ca_file"], "")
        command = compose["services"]["fortigate-exporter"]["command"]
        self.assertIn("-insecure", command)
        self.assertNotIn("-extra-ca-certs", command)
        self.assertEqual((config.root / "secrets/fortigate-ca.pem").read_text(), CA)

    def test_tls_disabled_to_enabled_with_explicit_ca_succeeds(self):
        config = self.existing(verify=False)
        _, candidate, site, compose = self.prepare(config, self.save(config, ca=CA))
        self.assertTrue(site["api_tls_verify"])
        self.assertEqual((candidate / site["api_ca_file"]).read_text(), CA)
        self.assertNotIn("-insecure", compose["services"]["fortigate-exporter"]["command"])

    def test_disabling_tls_still_requires_acknowledgement(self):
        config = self.existing()
        with self.assertRaises(Problem) as caught:
            self.save(config, verify=False, acknowledge=False)
        self.assertEqual(caught.exception.code, "tls_ack_required")

    def test_helper_setup_error_classification_is_bounded_and_redacted(self):
        for message, code in (
            ("A custom CA requires api_tls_verify=true", "tls_ca_conflict"),
            ("synthetic-secret-bearing-validation-error", "configuration_invalid"),
        ):
            with self.subTest(code=code):
                operations = Mock()
                operations.dispatch.side_effect = setup.SetupError(message)
                cls = helper.handler(operations, "synthetic-key", 42)
                handler = cls.__new__(cls)
                raw = b'{"operation":"prepare","data":{}}'
                handler.path = "/operation"
                handler.headers = Message()
                handler.headers["Authorization"] = "Bearer synthetic-key"
                handler.headers["Content-Length"] = str(len(raw))
                handler.connection = Mock()
                handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                # The operating-system peer check is independently covered by
                # management error tests; this synthetic peer uses its fixed UID.
                import struct

                handler.connection.getsockopt.return_value = struct.pack("3i", 1, 42, 42)
                with self.assertLogs("fortilens.management", level="ERROR") as logs:
                    handler.do_POST()
                handler.send_response.assert_called_once_with(400)
                response = json.loads(handler.wfile.getvalue())
                self.assertEqual(response["error"], code)
                self.assertNotIn("synthetic-secret-bearing", handler.wfile.getvalue().decode())
                self.assertNotIn(message, " ".join(logs.output))
                self.assertIn("category=invalid_data", " ".join(logs.output))


if __name__ == "__main__":
    unittest.main()
