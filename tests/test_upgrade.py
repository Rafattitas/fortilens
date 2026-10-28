import contextlib
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'portable'))
spec=importlib.util.spec_from_file_location('release_setup',ROOT/'portable/setup.py')
s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
import upgrade


class UpgradeTests(unittest.TestCase):
    def installation(self,root):
        config=s.validate_config({'fortigate_host':'fw.example.test','sla':'fixture','wan_interfaces':['wan-a']})
        with contextlib.redirect_stdout(io.StringIO()):
            p=s.render(config,Path(root)/'existing','private-fixture-token','unchanged-admin-password')
        return p,config

    def prepare(self,p,c):
        with contextlib.redirect_stdout(io.StringIO()):
            return upgrade.prepare(s,p,c,enable_accounting=True,zone='UTC',expected_devid='FG-FIXTURE')

    def test_prepare_apply_rollback_preserves_existing_data_secrets_and_custom_settings(self):
        with tempfile.TemporaryDirectory() as root:
            p,c=self.installation(root)
            compose=s.load(p/'compose.yaml');compose['services']['loki']['cpus']='0.7'
            (p/'compose.yaml').write_text(s.dumps(compose))
            original={name:(p/name).read_bytes() for name in ('compose.yaml','site.json','secrets/grafana_admin_password','secrets/fortigate-key.yaml','loki/loki-config.yaml','grafana/dashboard.json')}
            plan=self.prepare(p,c)
            self.assertEqual((p/'compose.yaml').read_bytes(),original['compose.yaml'])
            upgrade.apply(s,p,plan['id'])
            final=s.load(p/'compose.yaml')
            self.assertEqual(set(final['volumes'])-set(compose['volumes']),{'usage-data'})
            for name,service in compose['services'].items():
                expected=deepcopy(service)
                if name=='prometheus':expected.setdefault('secrets',[]).append('usage_admin_password')
                self.assertEqual(final['services'][name],expected)
            self.assertIn('use_incoming_timestamp = false',(p/'alloy/config.alloy').read_text())
            for name in original.keys()-{'compose.yaml','site.json'}:
                self.assertEqual((p/name).read_bytes(),original[name])
            upgrade.rollback(s,p,plan['id'])
            for name,data in original.items():self.assertEqual((p/name).read_bytes(),data)
            self.assertTrue((p/'secrets/usage_admin_password').is_file())

    def test_edits_between_prepare_apply_and_after_apply_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            p,c=self.installation(root);plan=self.prepare(p,c)
            before=(p/'site.json').read_bytes()
            (p/'site.json').write_bytes(before+b'\n')
            with self.assertRaises(upgrade.UpgradeError):upgrade.apply(s,p,plan['id'])
            (p/'site.json').write_bytes(before);upgrade.apply(s,p,plan['id'])
            (p/'alloy/config.alloy').write_text('operator change')
            with self.assertRaises(upgrade.UpgradeError):upgrade.rollback(s,p,plan['id'])

    def test_opt_in_custom_yaml_and_paths_are_guarded(self):
        with tempfile.TemporaryDirectory() as root:
            p,c=self.installation(root)
            with self.assertRaises(upgrade.UpgradeError):upgrade.prepare(s,p,c)
            with self.assertRaises(upgrade.UpgradeError):upgrade.read_plan(p,'../outside')
            (p/'compose.yaml').write_text('services:\n  custom: {}')
            with self.assertRaises(upgrade.UpgradeError):self.prepare(p,c)


if __name__=='__main__':unittest.main()
