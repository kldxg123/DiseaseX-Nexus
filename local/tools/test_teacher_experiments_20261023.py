"""Meaningful parameterized contract scenarios and forecast boundary regressions."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'packages'))
from datetime import datetime, timezone, timedelta
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import unittest
import numpy as np
from predictor_agent.execution_contract import ContractGate, ContractError, fingerprint
from predictor_agent.evidence_recommender import ranked, Admission

NOW=datetime(2026,10,23,tzinfo=timezone.utc)
def payload(i=0):
    return dict(dataset_sha256='a'*64,task_id=f'task-{i}',graph_sha256='b'*64,model='last_value',parameters={'horizon':1},origin=i,output_scope='analyst_review',risk_review=True)
def approved(i=0):
    gate=ContractGate();p=payload(i);identifier=gate.create(p,NOW+timedelta(hours=1),NOW);gate.approve(identifier,'reviewer',NOW)
    return gate,p,identifier

class ContractTests(unittest.TestCase):
    def test_failed_execution_does_not_publish_or_retry(self):
        g,p,i=approved()
        with self.assertRaises(ContractError):g.execute(i,p,'once','executor',NOW,lambda:(_ for _ in ()).throw(RuntimeError('synthetic failure')))
        self.assertEqual(g.state(i),'FAILED')
        with self.assertRaises(ContractError):g.execute(i,p,'once','executor',NOW,lambda:42)
    def test_concurrent_idempotency(self):
        g,p,i=approved();calls=[]
        def invoke(_):return g.execute(i,p,'once','executor',NOW,lambda:(calls.append(1) or [42]))
        with ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(list(pool.map(invoke,range(24))),[[42]]*24)
        self.assertEqual(len(calls),1)
    def test_output_copy_cannot_modify_registry(self):
        g,p,i=approved();v=g.execute(i,p,'once','executor',NOW,lambda:[42]);v[0]=0
        self.assertEqual(g.execute(i,p,'once','executor',NOW,lambda:None),[42])
    def test_unauthorized_approval(self):
        g=ContractGate();p=payload();i=g.create(p,NOW+timedelta(hours=1),NOW)
        with self.assertRaises(ContractError):g.approve(i,'analyst',NOW)
    def test_timezone_required(self):
        with self.assertRaises(ContractError):ContractGate().create(payload(),NOW.replace(tzinfo=None),NOW)

def valid_case(index,model):
    def test(self):
        g=ContractGate();p=payload(index);p['model']=model;i=g.create(p,NOW+timedelta(hours=1),NOW);g.approve(i,'reviewer',NOW)
        calls=[];answer=g.execute(i,p,'once','executor',NOW,lambda:(calls.append(1) or [index]))
        self.assertEqual(answer,[index]);self.assertEqual(g.execute(i,p,'once','executor',NOW,lambda:None),answer);self.assertEqual(len(calls),1)
    return test

def invalid_case(index,kind):
    def test(self):
        g,p,i=approved(index);changed=deepcopy(p);role='executor';at=NOW;key='once'
        if kind in ('dataset_sha256','graph_sha256'):changed[kind]='c'*64
        elif kind=='task_id':changed[kind]='different'
        elif kind=='model':changed[kind]='mean4'
        elif kind=='parameters':changed[kind]={'horizon':2}
        elif kind=='origin':changed[kind]+=1
        elif kind=='output_scope':changed[kind]='public'
        elif kind=='risk_review':changed[kind]=False
        elif kind=='expired':at=NOW+timedelta(hours=1)
        elif kind=='wrong_role':role='analyst'
        elif kind=='no_approval':g=ContractGate();i=g.create(p,NOW+timedelta(hours=1),NOW)
        elif kind=='revoked':g.revoke(i,'reviewer',NOW)
        elif kind=='unknown':i='absent'
        elif kind=='empty_key':key=''
        elif kind=='nonfinite':changed['parameters']={'x':float('nan')}
        calls=[]
        with self.assertRaises(ContractError):g.execute(i,changed,key,role,at,lambda:calls.append(1))
        self.assertEqual(calls,[])
    return test

# 60 legal + 150 illegal combinations vary registered adapters, task identity,
# bound fields and lifecycle conditions; these are not 210 independent attacks.
for index in range(15):
    for model in ('last_value','mean4','seasonal52','log_ar2'):setattr(ContractTests,f'test_legal_{index:02}_{model}',valid_case(index,model))
for index in range(10):
    for kind in ('dataset_sha256','graph_sha256','task_id','model','parameters','origin','output_scope','risk_review','expired','wrong_role','no_approval','revoked','unknown','empty_key','nonfinite'):
        setattr(ContractTests,f'test_illegal_{index:02}_{kind}',invalid_case(index,kind))

class RecommendationTests(unittest.TestCase):
    def test_missing_evidence_zero_not_invented(self):
        a=ranked(np.ones(156),'unregistered',{'rules':[]})
        self.assertTrue(all(r['dimensions']['M']==0 and r['dimensions']['E']==0 for r in a))
    def test_short_history_rejected(self):
        self.assertTrue(all(not r['admitted'] for r in ranked(np.ones(7),'dengue',{'rules':[]})))
    def test_seasonal_boundary(self):
        for n in (103,104):
            a=ranked(np.ones(n),'dengue',{'rules':[]});v=next(r for r in a if r['model']=='seasonal52')
            self.assertEqual(v['eligible'],n==104)
    def test_nan_rejected(self):
        with self.assertRaises(ValueError):ranked([float('nan')]*104,'dengue',{'rules':[]})
    def test_no_graph_zeros_graph_dimensions(self):
        a=ranked(np.ones(156),'dengue',{'rules':[]},policy='no_graph')
        self.assertTrue(all(r['dimensions']['M']==0 and r['dimensions']['E']==0 for r in a))
    def test_reproducible_and_no_input_mutation(self):
        y=np.arange(156,dtype=float);copy=y.copy()
        self.assertEqual(ranked(y,'dengue',{'rules':[]}),ranked(y,'dengue',{'rules':[]}));np.testing.assert_array_equal(y,copy)
    def test_high_threshold_rejects(self):
        self.assertTrue(all(not r['admitted'] for r in ranked(np.ones(156),'x',{'rules':[]},admission=Admission(threshold=100.))))

if __name__=='__main__':unittest.main(verbosity=1)
