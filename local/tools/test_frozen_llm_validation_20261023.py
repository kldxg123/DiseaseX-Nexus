"""Local and remote tests require no credentials, network, or model weights."""
import unittest
import numpy as np
import frozen_llm_validation_core_20261023 as c

class Tests(unittest.TestCase):
    def setUp(self):self.y=10+np.sin(np.arange(220)/10)*3
    def test_registry(self):
        for m in c.ALL:
            p=c.forecast(self.y,m);self.assertEqual(p.shape,(4,));self.assertTrue((p>=0).all())
    def test_reject(self):
        for y in [np.ones(3),np.array([np.nan]*105),-np.ones(105)]:
            with self.assertRaises(ValueError):c.forecast(y,'last_value')
    def test_baselines_always_retained(self):
        for ids in [['mean8','mean13'],['log_ar2','drift4']]:self.assertTrue(set(c.BASE)<=set(c.pool(ids)))
    def test_schema(self):
        self.assertEqual(c.validate_response('{"candidates":["mean8","log_ar2"]}'),['mean8','log_ar2'])
        for s in ['text','{"candidates":["mean8","mean8"]}','{"candidates":["mean8","invented"]}','{"candidates":["mean8","mean13"],"code":"x"}']:
            self.assertIsNone(c.validate_response(s))
    def test_history_isolation(self):
        all_y=np.r_[self.y,[999999]*4]
        self.assertEqual(c.select(self.y,c.pool(['mean8','mean13'])),c.select(all_y[:220],c.pool(['mean8','mean13'])))
    def test_baseline_enforced(self):
        with self.assertRaises(ValueError):c.select(self.y,['mean8'])
    def test_profile_no_identity(self):
        p=c.profile(self.y);self.assertNotIn('disease',p);self.assertNotIn('year',p);self.assertEqual(p['observations'],220)
    def test_reproducibility(self):self.assertEqual(c.select(self.y,c.pool(['log_ar2','mean8'])),c.select(self.y,c.pool(['log_ar2','mean8'])))
    def test_calibration(self):
        q=c.quantiles(3,np.arange(-26,26));self.assertTrue((np.diff(q)>=0).all());self.assertGreaterEqual(c.score(4,q)['wis'],0)
    def test_zero_denominator(self):
        self.assertIsNone(c.aggregate([dict(actual=0,point=0,wis=0,coverage95=1,width95=0)])['wape'])
    def test_pair_mismatch(self):
        with self.assertRaises(ValueError):c.paired_ci([dict(origin=1,horizon=1,actual=1,point=1)],[])
    def test_ci_zero(self):
        rows=[dict(origin=i,horizon=1,actual=2,point=1) for i in range(20)]
        r=c.paired_ci(rows,rows);self.assertEqual(r['ci95_pp'],[0,0])

if __name__=='__main__':unittest.main()
