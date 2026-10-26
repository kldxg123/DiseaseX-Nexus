"""Isolated authenticated HTTP, persistence and process-boundary tests."""
from pathlib import Path
import os,sys,json,hashlib,importlib.util,multiprocessing,secrets,tempfile,time,unittest
from datetime import datetime,timezone,timedelta
from copy import deepcopy
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'packages'))
from predictor_agent.durable_execution import DurableGate,GateError,Principal,OpaqueTokenRegistry,payload_digest

NOW=datetime(2026,10,23,tzinfo=timezone.utc)
A=Principal('fixture-analyst','analyst');R=Principal('fixture-reviewer','reviewer');X=Principal('fixture-executor','executor')

def payload():
    return dict(dataset_sha256='a'*64,graph_sha256='b'*64,task_id='isolated-task',model='last_value',parameters={'horizon':4},origin=156,output_scope='analyst_review',risk_review=True)

def process_execute(directory,identifier,crash=False):
    gate=DurableGate(Path(directory))
    def callback():
        if crash:os._exit(23)
        with (Path(directory)/'callback_count.txt').open('a') as stream:stream.write('1\n')
        time.sleep(.03)
        return {'prediction':[1.,1.,1.,1.],'review_state':'pending analyst review'}
    gate.execute(identifier,payload(),'same-key',X,NOW,callback)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name);self.gate=DurableGate(self.path)
        self.identifier=self.gate.create(payload(),A,NOW+timedelta(hours=1),NOW)
    def tearDown(self):self.temp.cleanup()
    def approve(self):self.gate.approve(self.identifier,R,NOW)
    def test_approval_survives_reconstruction(self):
        self.approve();self.assertEqual(DurableGate(self.path).snapshot()['records'][self.identifier]['state'],'APPROVED')
    def test_completion_survives_reconstruction(self):
        self.approve();self.gate.execute(self.identifier,payload(),'same-key',X,NOW,lambda:[3])
        self.assertEqual(DurableGate(self.path).execute(self.identifier,payload(),'same-key',X,NOW,lambda:[9]),[3])
    def test_different_key_replay_rejected(self):
        self.approve();self.gate.execute(self.identifier,payload(),'same-key',X,NOW,lambda:[3])
        with self.assertRaisesRegex(GateError,'REPLAY_KEY_CHANGED'):self.gate.execute(self.identifier,payload(),'other',X,NOW,lambda:[9])
    def test_process_lock_and_idempotency(self):
        self.approve();ctx=multiprocessing.get_context('spawn')
        workers=[ctx.Process(target=process_execute,args=(str(self.path),self.identifier)) for _ in range(8)]
        for worker in workers:worker.start()
        for worker in workers:worker.join(30);self.assertEqual(worker.exitcode,0)
        self.assertEqual((self.path/'callback_count.txt').read_text().splitlines(),['1'])
        events=self.gate.snapshot()['audit'];self.assertEqual(sum(e['event']=='COMPLETED' for e in events),1)
    def test_crash_is_not_silently_reexecuted(self):
        self.approve();ctx=multiprocessing.get_context('spawn');worker=ctx.Process(target=process_execute,args=(str(self.path),self.identifier,True));worker.start();worker.join(30)
        self.assertEqual(worker.exitcode,23)
        self.assertEqual(DurableGate(self.path).snapshot()['records'][self.identifier]['state'],'RUNNING')
        with self.assertRaisesRegex(GateError,'NOT_APPROVED'):self.gate.execute(self.identifier,payload(),'same-key',X,NOW,lambda:[9])
    def test_failure_persists_and_hides_exception(self):
        self.approve()
        with self.assertRaisesRegex(GateError,'^EXECUTION_FAILED$'):self.gate.execute(self.identifier,payload(),'same-key',X,NOW,lambda:1/0)
        self.assertEqual(DurableGate(self.path).snapshot()['records'][self.identifier]['state'],'FAILED')
    def test_expiry_persists(self):
        with self.assertRaisesRegex(GateError,'EXPIRED'):self.gate.approve(self.identifier,R,NOW+timedelta(hours=1))
        self.assertEqual(DurableGate(self.path).snapshot()['records'][self.identifier]['state'],'EXPIRED')
    def test_revocation_persists(self):
        self.gate.revoke(self.identifier,R,NOW)
        with self.assertRaises(GateError):DurableGate(self.path).execute(self.identifier,payload(),'same-key',X,NOW,lambda:[9])
    def test_self_approval_rejected(self):
        with self.assertRaisesRegex(GateError,'SELF_APPROVAL'):self.gate.approve(self.identifier,Principal(A.subject,'reviewer'),NOW)
    def test_unknown_role_cannot_create(self):
        with self.assertRaises(GateError):self.gate.create(payload(),X,NOW+timedelta(hours=1),NOW)
    def test_unknown_role_cannot_approve(self):
        with self.assertRaises(GateError):self.gate.approve(self.identifier,A,NOW)
    def test_unknown_role_cannot_execute(self):
        self.approve()
        with self.assertRaises(GateError):self.gate.execute(self.identifier,payload(),'same-key',A,NOW,lambda:[1])
    def test_corrupt_ledger_fails_closed(self):
        self.gate.state_file.write_text('{')
        with self.assertRaises(json.JSONDecodeError):DurableGate(self.path).snapshot()
    def test_output_copy_cannot_mutate_ledger(self):
        self.approve();v=self.gate.execute(self.identifier,payload(),'same-key',X,NOW,lambda:[3]);v[0]=9
        self.assertEqual(self.gate.execute(self.identifier,payload(),'same-key',X,NOW,lambda:[0]),[3])


def changed_field_test(field):
    def test(self):
        self.approve();p=payload()
        p[field]={'dataset_sha256':'c'*64,'graph_sha256':'d'*64,'task_id':'other','model':'mean4','parameters':{'horizon':1},'origin':157,'output_scope':'public','risk_review':False}[field]
        calls=[]
        with self.assertRaises(GateError):self.gate.execute(self.identifier,p,'same-key',X,NOW,lambda:calls.append(1))
        self.assertEqual(calls,[])
    return test
for name in payload():setattr(GateTests,'test_bound_field_'+name,changed_field_test(name))


class HTTPTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI,Request,HTTPException
        from fastapi.testclient import TestClient
        self.temp=tempfile.TemporaryDirectory();self.gate=DurableGate(Path(self.temp.name));self.values={p.role:secrets.token_urlsafe(32) for p in (A,R,X)}
        auth=OpaqueTokenRegistry({self.values[p.role]:p for p in (A,R,X)})
        self.app=FastAPI();gate=self.gate
        # The HTTP envelope derives roles from verified headers, not body fields.
        @self.app.post('/contracts/{action}')
        async def route(action:str,request:Request):
            try:
                actor=auth.authenticate(request.headers.get('Authorization'));body=await request.json()
                if 'role' in body or 'actor' in body:raise GateError('INVALID_FIELDS')
                if action=='create':return {'id':gate.create(body['payload'],actor,NOW+timedelta(hours=1),NOW)}
                if action=='approve':gate.approve(body['id'],actor,NOW);return {'approved':True}
                if action=='execute':return {'result':gate.execute(body['id'],body['payload'],body['key'],actor,NOW,lambda:{'forecast':[1,1,1,1],'review_state':'pending analyst review'})}
                raise GateError('UNKNOWN_ACTION')
            except GateError as exc:raise HTTPException(401 if str(exc)=='AUTH_REQUIRED' else 403,detail=str(exc)) from None
        self.client=TestClient(self.app)
    def tearDown(self):self.client.close();self.temp.cleanup()
    def post(self,action,body,role=None,header=None):
        return self.client.post('/contracts/'+action,json=body,headers={'Authorization':header if header is not None else 'Bearer '+self.values[role]} if role or header else {})
    def test_missing_authentication(self):self.assertEqual(self.post('create',{'payload':payload()}).status_code,401)
    def test_invalid_authentication(self):self.assertEqual(self.post('create',{'payload':payload()},header='Bearer REDACTED').status_code,401)
    def test_body_role_escalation_rejected(self):self.assertEqual(self.post('create',{'payload':payload(),'role':'reviewer'},'analyst').status_code,403)
    def test_authenticated_wrong_role_rejected(self):self.assertEqual(self.post('create',{'payload':payload()},'executor').status_code,403)
    def test_authenticated_full_chain_and_persistent_readback(self):
        response=self.post('create',{'payload':payload()},'analyst');self.assertEqual(response.status_code,200);identifier=response.json()['id']
        self.assertEqual(self.post('approve',{'id':identifier},'analyst').status_code,403)
        self.assertEqual(self.post('approve',{'id':identifier},'reviewer').status_code,200)
        response=self.post('execute',{'id':identifier,'payload':payload(),'key':'one'},'executor');self.assertEqual(response.status_code,200)
        self.assertEqual(DurableGate(self.gate.directory).snapshot()['records'][identifier]['state'],'COMPLETED')
    def test_existing_server_authentication_module(self):
        from fastapi import FastAPI,Depends
        from fastapi.testclient import TestClient
        target=Path.home()/'projects/openclaw/src/epidemic_core/api/auth.py'
        spec=importlib.util.spec_from_file_location('audited_server_auth',target);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        app=FastAPI();value=secrets.token_urlsafe(32);app.state.api_token=value
        @app.get('/check',dependencies=[Depends(module.require_auth)])
        def check():return {'authenticated':True}
        with TestClient(app) as client:
            self.assertEqual(client.get('/check').status_code,401)
            self.assertEqual(client.get('/check',headers={'Authorization':'Bearer REDACTED'}).status_code,401)
            self.assertEqual(client.get('/check',headers={'Authorization':'Bearer '+value}).status_code,200)


if __name__=='__main__':unittest.main(verbosity=2)
