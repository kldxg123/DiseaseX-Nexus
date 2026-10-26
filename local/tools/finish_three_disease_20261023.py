from pathlib import Path
import sys,json,csv,hashlib,unittest,io
from datetime import datetime,timezone
import numpy as np
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'packages'))
from predictor_agent.chronological_validation import *
O=R/'reports/three_disease_chronological_20261023'
records=json.loads((O/'official_response.json').read_text())['result']['records']
base=list(csv.DictReader((O/'predictions.csv').open(encoding='utf-8')))
rows=[];results=[]
(O/'adaptive_protocol.json').write_text(json.dumps({'registered_at':datetime.now(timezone.utc).isoformat(),'status':'exploratory optimization after static test inspection; not new blind validation','window_origins':13,'horizons':[1,2,3,4],'calibration':'2019 signed residuals per model and horizon; unchanged','test':'2020-2022','code_sha256':hashlib.sha256((R/'packages/predictor_agent/chronological_validation.py').read_bytes()).hexdigest()},indent=2),encoding='utf-8')
for disease in ('Dengue Fever','Campylobacterenterosis','Mumps'):
 labels={disease,'Campylobacter enteritis'} if disease=='Campylobacterenterosis' else {disease}
 series=sorted([r for r in records if r['disease'] in labels],key=lambda r:r['epi_week']);y=np.array([float(r['no._of_cases']) for r in series])
 chosen={}
 for r in base:
  if r['disease']!=disease:continue
  origin=int(r['origin'])
  if origin not in chosen:chosen[origin]=rolling_select(y[:origin])
  if r['model']==chosen[origin]:rows.append(dict(r,model='rolling_select',chosen_component=r['model']))
 for h in ['all','1','2','3','4']:
  a=[r for r in rows if r['disease']==disease and (h=='all' or r['horizon']==h)]
  results.append({'disease':disease,'model':'rolling_select','horizon':h,'n':len(a),'wape':wape([float(r['actual']) for r in a],[float(r['point']) for r in a]),'wis':float(np.mean([float(r['wis']) for r in a])),'coverage95':float(np.mean([float(r['coverage95']) for r in a])),'width95':float(np.mean([float(r['width95']) for r in a]))})
(O/'adaptive_metrics.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
with (O/'adaptive_predictions.csv').open('w',encoding='utf-8',newline='') as f:
 w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
class Checks(unittest.TestCase):
 def test_zero_wape(self):self.assertIsNone(wape([0,0],[1,2]))
 def test_reject_missing(self):self.assertRaises(ValueError,forecast,[1]*10+[float('nan')],'last_value',4)
 def test_reject_negative(self):self.assertRaises(ValueError,forecast,[-1]*20,'last_value',4)
 def test_seasonal_admission(self):self.assertRaises(ValueError,forecast,[1]*52,'seasonal52',4)
 def test_determinism(self):self.assertEqual(rolling_select(list(range(200))),rolling_select(list(range(200))))
 def test_input_not_modified(self):
  x=list(range(180));a=x.copy();rolling_select(x);self.assertEqual(x,a)
 def test_quantile_order(self):self.assertTrue((np.diff(quantile_forecast(1,list(range(-40,40))))>=0).all())
 def test_wis_zero(self):self.assertEqual(score_quantiles(1,[1]*7)['wis'],0)
 def test_wis_hand_example(self):self.assertAlmostEqual(score_quantiles(0,[0,0,0,0,2,2,2])['wis'],.75/3.5)
 def test_pair_count(self):self.assertEqual(len(rows),3*622)
 def test_no_cross_boundary(self):self.assertTrue(all(r['week']>='2020' for r in rows))
 def test_finite(self):self.assertTrue(all(np.isfinite(float(r['wis'])) for r in rows))
stream=io.StringIO();result=unittest.TextTestRunner(stream=stream,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Checks));(O/'new_tests.txt').write_text(stream.getvalue(),encoding='utf-8');assert result.wasSuccessful(),stream.getvalue()
print(json.dumps([r for r in results if r['horizon']=='all'],ensure_ascii=False,indent=2));print(stream.getvalue())
