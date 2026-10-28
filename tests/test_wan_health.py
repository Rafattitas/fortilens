from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "portable"))
import wan_health as w


class WanHealthTests(unittest.TestCase):
    def test_missing_metrics_are_not_down(self):
        self.assertEqual(w.current_state([],100,60,True),"NO DATA")
        self.assertEqual(w.current_state([{'state':'down','value':1,'timestamp':99}],100,60,False),'NO DATA')
        r=w.summarize([],0,100)
        self.assertIsNone(r['availability_percent'])
        self.assertEqual(r['down_observed_seconds'],0)
        self.assertEqual(r['outages'],[])

    def test_zero_onehot_down_value_is_not_an_active_down_state(self):
        rows=[{'state':'down','value':0,'timestamp':99},{'state':'up','value':1,'timestamp':99}]
        self.assertEqual(w.current_state(rows,100,60,True),'UP')

    def test_conflicting_or_stale_states_remain_unknown_or_no_data(self):
        rows=[{'state':'down','value':1,'timestamp':99},{'state':'up','value':1,'timestamp':99}]
        self.assertEqual(w.current_state(rows,100,60,True),'UNKNOWN')
        self.assertEqual(w.current_state(rows,1000,60,True),'NO DATA')

    def test_availability_uses_valid_observations_and_reports_coverage(self):
        r=w.summarize([{'timestamp':0,'state':'UP'},{'timestamp':20,'state':'DOWN'}],0,100,max_hold=20)
        self.assertEqual(r['availability_percent'],50)
        self.assertEqual(r['valid_state_coverage_percent'],40)
        self.assertEqual(r['unknown_or_unobserved_seconds'],60)
        self.assertTrue(r['outages'][0]['end_censored'])

    def test_gap_does_not_bridge_two_down_observation_runs(self):
        r=w.summarize([{'timestamp':0,'state':'DOWN'},{'timestamp':80,'state':'DOWN'}],0,100,max_hold=20)
        self.assertEqual(len(r['outages']),2)
        self.assertEqual(r['down_observed_seconds'],40)

    def test_observed_recovery_is_distinct_from_censored_end(self):
        r=w.summarize([{'timestamp':0,'state':'DOWN'},{'timestamp':20,'state':'UP'}],0,40,max_hold=20)
        self.assertFalse(r['outages'][0]['end_censored'])
        self.assertEqual(r['outages'][0]['observed_seconds'],20)

    def test_error_samples_do_not_enter_up_down_denominator(self):
        r=w.summarize([{'timestamp':0,'state':'ERROR'}],0,20,max_hold=20)
        self.assertIsNone(r['availability_percent'])
        self.assertEqual(r['sample_coverage_percent'],100)
        self.assertEqual(r['valid_state_coverage_percent'],0)

    def test_degradation_requires_observed_up_state(self):
        r=w.summarize([{'timestamp':0,'state':'UP','latency_ms':200},{'timestamp':20,'state':'UP','packet_loss_pct':6},
                        {'timestamp':40,'state':'DOWN','packet_loss_pct':100}],0,60,max_hold=20)
        self.assertEqual(r['degradation'][0]['observed_seconds'],40)
        self.assertEqual(r['degradation'][0]['reasons'],['HIGH_LATENCY','HIGH_PACKET_LOSS'])
        self.assertEqual(r['down_observed_seconds'],20)

    def test_active_route_is_not_guessed_from_internet_health(self):
        self.assertIsNone(w.route_evidence({},['wan-a'],'1.1.1.1')['interface'])
        result=w.route_evidence({'status':'success','results':{'success':True,'interface':'wan-a'}},['wan-a'],'1.1.1.1')
        self.assertEqual(result['interface'],'wan-a')
        self.assertEqual(w.route_evidence({'status':'success','results':{'success':True,'interface':'virtual-wan-link'}},['wan-a'],'1.1.1.1')['status'],'UNKNOWN')


if __name__=='__main__':unittest.main()
