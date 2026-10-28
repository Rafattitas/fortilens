from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portable'))
import alerts


class AlertTests(unittest.TestCase):
    def config(self,**changes):
        return {'fortigate_host':'fw.example.test','vdom':'root','sla':'fixture','wan_latency_warning_ms':150,
            'wan_loss_warning_pct':5,'accounting_enabled':True,'loki_receive_timestamps':True,'confirmed_wan_interfaces':['uplink-a'],**changes}

    def test_candidates_are_paused_and_outage_requires_successful_collection(self):
        result=alerts.candidates(self.config())
        rules=result['rules']['groups'][0]['rules']
        self.assertEqual(len(rules),5)
        self.assertTrue(all(r['isPaused'] and r['noDataState']=='NoData' and r['execErrState']=='Error' for r in rules))
        outage=rules[0]['data'][0]['model']['expr']
        self.assertIn('state="down"} == bool 1',outage)
        self.assertIn('probe_success',outage)
        self.assertNotIn('contact-points',result)
        self.assertFalse(any('syslog' in r['uid'] for r in rules))

    def test_notifications_use_environment_references_without_global_policy(self):
        for name in ('email','telegram'):
            result=alerts.candidates(self.config(),notification=name,expected_log_interval=600)
            self.assertEqual(len(result['rules']['groups'][0]['rules']),6)
            self.assertNotIn('policies',result)
            receiver=result['contact-points']['contactPoints'][0]['receivers'][0]
            self.assertEqual(receiver['type'],name)
            self.assertTrue(any(str(v).startswith('$FORTILENS_') for v in receiver['settings'].values()))
        with self.assertRaises(ValueError):alerts.candidates(self.config(loki_receive_timestamps=False),expected_log_interval=600)


if __name__=='__main__':unittest.main()
