"""Offline regression tests; run directly to avoid unrelated API fixtures."""
import sys
from pathlib import Path
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parent))
from run_nested_validation_20261023 import bootstrap, metrics, make_fold, select_and_calibrate, historical_predictions

class NestedValidationTests(unittest.TestCase):
    def test_disjoint_partitions(self):
        f=make_fold(208,260)
        self.assertEqual((f.selection_start,f.selection_end,f.calibration_start,f.calibration_end),(104,156,156,208))
    def test_insufficient_history(self):
        with self.assertRaises(ValueError):make_fold(150,200)
    def test_outer_labels_rejected(self):
        with self.assertRaises(ValueError):select_and_calibrate(np.ones(260),make_fold(208,260))
    def test_future_perturbation(self):
        y=np.arange(260,dtype=float);f=make_fold(208,260)
        before=select_and_calibrate(y[:208],f);y[208:]=1e9
        self.assertEqual(before,select_and_calibrate(y[:208],f))
    def test_targets_do_not_cross_partition(self):
        rows=historical_predictions(np.arange(260,dtype=float),104,156,'mean4')
        self.assertEqual(len(rows),202)
        self.assertTrue(all(o+h-1==t and t<156 for o,h,t,_,_ in rows))
    def test_no_input_mutation(self):
        y=np.arange(208,dtype=float);before=y.copy()
        select_and_calibrate(y,make_fold(208,260));np.testing.assert_array_equal(y,before)
    def test_reproducible_selection(self):
        y=np.arange(208,dtype=float);f=make_fold(208,260)
        self.assertEqual(select_and_calibrate(y,f),select_and_calibrate(y,f))
    def rows(self):
        return [dict(origin=i,horizon=h,target=i+h-1,actual=10.,point=9.,wis=1.,coverage95=1.) for i in range(20) for h in (1,2,3,4)]
    def test_identical_predictions_zero_ci(self):
        a=self.rows();v=bootstrap(a,a,4,100)
        self.assertEqual(v['wape_ci95_pp'],[0.,0.]);self.assertEqual(v['wis_ci95'],[0.,0.])
    def test_bootstrap_reproducible(self):
        a=self.rows();b=[dict(r,point=8.) for r in a]
        self.assertEqual(bootstrap(a,b,4,100),bootstrap(a,b,4,100))
    def test_mismatched_keys_rejected(self):
        with self.assertRaises(ValueError):bootstrap(self.rows(),self.rows()[:-1],4,10)
    def test_duplicate_keys_rejected(self):
        a=self.rows()
        with self.assertRaises(ValueError):bootstrap(a,a+[a[0]],4,10)
    def test_observation_mismatch_rejected(self):
        a=self.rows();b=[dict(r) for r in a];b[0]['actual']=11.
        with self.assertRaises(ValueError):bootstrap(a,b,4,10)
    def test_invalid_block_rejected(self):
        with self.assertRaises(ValueError):bootstrap(self.rows(),self.rows(),21,10)
    def test_csv_numeric_conversion(self):
        rows=[{k:str(v) for k,v in r.items()} for r in self.rows()]
        self.assertAlmostEqual(metrics(rows)['wape'],.1)
    def test_calibration_support_and_quantile_shape(self):
        from run_nested_validation_20261023 import quantile_forecast
        _,_,res=select_and_calibrate(np.arange(208,dtype=float),make_fold(208,260))
        for by_h in res.values():
            for errors in by_h.values():
                self.assertGreaterEqual(len(errors),26)
                q=quantile_forecast(10.,errors)
                self.assertEqual(q.shape,(7,));self.assertTrue((np.diff(q)>=0).all())

if __name__=='__main__':unittest.main(verbosity=2)
