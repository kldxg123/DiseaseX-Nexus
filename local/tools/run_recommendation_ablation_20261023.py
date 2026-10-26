"""Actual offline fits for fixed-pool recommendation and admission sensitivity."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import hashlib, json, sys, time
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'packages'));sys.path.insert(0,str(ROOT/'tools'))
from predictor_agent.chronological_validation import MODELS,forecast,quantile_forecast,score_quantiles,wape
from predictor_agent.nested_validation import make_fold,historical_predictions
from predictor_agent.evidence_recommender import ranked,Admission,profile
from predictor_agent.execution_contract import ContractGate
from run_teacher_experiments_20261023 import OUT,save,csvsave,sha
from run_nested_validation_20261023 import bootstrap

DATA=ROOT/'reports/three_disease_chronological_20261023/official_response.json'
GRAPH=ROOT/'kg_deploy_staging/reference/forecast_model_knowledge_graph.v1.json'
TARGETS=('Dengue Fever','Campylobacterenterosis','Mumps')
POLICIES=('six_dim','no_graph','no_mechanism','no_transfer','profile_only','random','fixed_rule','budget_search','frozen_no_feedback')

def load():
    raw=json.loads(DATA.read_text(encoding='utf-8'))['result']['records'];series={}
    for disease in TARGETS:
        aliases={disease,'Campylobacter enteritis'} if disease=='Campylobacterenterosis' else {disease}
        rows=sorted([r for r in raw if r['disease'] in aliases],key=lambda r:r['epi_week'])
        weeks=[r['epi_week'] for r in rows];assert len(weeks)==len(set(weeks))==574
        series[disease]=(weeks,np.array([float(r['no._of_cases']) for r in rows]))
    return series,json.loads(GRAPH.read_text(encoding='utf-8'))

def main():
    stamp=datetime.now(timezone.utc);graphhash=sha(GRAPH);datahash=sha(DATA)
    save('recommendation_protocol.json',{'locked_at':stamp.isoformat(),'source_sha256':datahash,'graph_sha256':graphhash,'seed':20261023,
       'status':'exploratory retrospective implementation and ablation, not untouched blind confirmation','models':MODELS,'policies':POLICIES,
       'outer_years':[2020,2021,2022],'selection_window':52,'disjoint_calibration_window':52,
       'selection_origins_per_candidate':5,'candidate_ranking':'six equal dimensions; graph required-baseline links are partial evidence E=0.5, not verified superiority',
       'selection_fit_caps':[5,15,30],'recommendation_max_shortlist':3,'full_search_cap':4,'wall_clock_caps_minutes':[5,15,30],
       'cost_scope':'separate ranking, selection, chosen-model calibration and outer forecast fits; no cross-policy timing cache',
       'primary_metric':'WAPE','secondary':'WIS','topk_oracle':'same four candidates and outer keys, 5% relative WAPE tolerance, evaluation only',
       'real_llm':'not executed: no local torch/transformers; deterministic six-dimension code is explicitly no-LLM',
       'sensitivity':'trailing real history lengths 4/7/8/15/16/25/26/52/104/156; minimum 8/16/26; seasonal 104/156; threshold 40/50/60; k 1/3/5 (clamped to four candidates); no imputed labels'})
    series,graph=load();records=[];results=[];scores=[];frozen={};gate=ContractGate()
    # Oracle predictions are frozen prior outputs, used for labels only after selection.
    import csv
    with (ROOT/'reports/nested_validation_20261023/predictions.csv').open(encoding='utf-8',newline='') as f:oracle=list(csv.DictReader(f))
    oracle_map={(r['disease'],int(r['fold']),r['model'],int(r['origin']),int(r['horizon'])):r for r in oracle if r['model'] in MODELS}
    for disease,(weeks,y) in series.items():
        for year in (2020,2021,2022):
            indexes=[i for i,w in enumerate(weeks) if w.startswith(str(year))];fold=make_fold(min(indexes),max(indexes)+1)
            bank=[v[:fold.selection_end] for k,(_,v) in series.items() if k!=disease]
            oracle_losses={m:wape([float(r['actual']) for r in oracle if r['disease']==disease and int(r['fold'])==year and r['model']==m],
                                  [float(r['point']) for r in oracle if r['disease']==disease and int(r['fold'])==year and r['model']==m]) for m in MODELS}
            best=min(oracle_losses.values())
            for budget in (5,15,30):
                for policy in POLICIES:
                    tick=time.perf_counter();rank=ranked(y[:fold.selection_end],'dengue' if disease=='Dengue Fever' else 'unsupported',graph,bank,policy=policy)
                    if policy=='random':
                        rng=np.random.default_rng(20261023+year+TARGETS.index(disease));rng.shuffle(rank)
                    if policy=='budget_search':rank=sorted(rank,key=lambda r:MODELS.index(r['model']))
                    for r in rank:scores.append(dict(disease=disease,year=year,budget=budget,policy=policy,**r))
                    ranking_seconds=time.perf_counter()-tick
                    admitted=[r['model'] for r in rank if r['admitted']]
                    shortlist=admitted[:min(budget//5,4 if policy=='budget_search' else 3)]
                    selection_fits=0;loss={};tick=time.perf_counter()
                    if policy=='fixed_rule':
                        chosen='mean4' if np.std(y[:fold.selection_end])/(np.mean(y[:fold.selection_end])+1)>.7 else 'last_value'
                    elif policy=='frozen_no_feedback' and (disease,budget) in frozen:chosen=frozen[disease,budget]
                    else:
                        for model in shortlist:
                            error=0.
                            for origin in range(fold.selection_end-8,fold.selection_end-3):
                                point=forecast(y[:origin],model,4);selection_fits+=1
                                error+=float(np.abs(y[origin:origin+4]-point).sum())
                            loss[model]=error
                        chosen=min(loss,key=loss.get) if loss else None
                    if policy=='frozen_no_feedback':frozen.setdefault((disease,budget),chosen)
                    selection_seconds=time.perf_counter()-tick
                    assert selection_fits<=budget
                    if chosen is None:raise RuntimeError('Default admission unexpectedly refused all models')
                    payload=dict(dataset_sha256=datahash,task_id=f'{disease}-{year}-{budget}-{policy}',graph_sha256=graphhash,model=chosen,
                                 parameters={'horizon':4,'calibration_weeks':52},origin=fold.test_start,output_scope='analyst_review',risk_review=True)
                    cid=gate.create(payload,stamp+timedelta(hours=1),stamp);gate.approve(cid,'reviewer',stamp)
                    def execute():
                        start=time.perf_counter()
                        cal=historical_predictions(y[:fold.test_start],fold.calibration_start,fold.calibration_end,chosen)
                        residual={h:[actual-point for _,hh,_,actual,point in cal if hh==h] for h in range(1,5)}
                        calibration_seconds=time.perf_counter()-start;start=time.perf_counter();rows=[]
                        for origin in indexes:
                            points=forecast(y[:origin],chosen,4)
                            for h,point in enumerate(points,1):
                                target=origin+h-1
                                if target>=fold.test_end:continue
                                ref=oracle_map[disease,year,chosen,origin,h]
                                assert abs(float(point)-float(ref['point']))<1e-9
                                q=quantile_forecast(float(point),residual[h]);sc=score_quantiles(float(y[target]),q)
                                rows.append(dict(disease=disease,fold=year,policy=policy,budget=budget,chosen=chosen,origin=origin,horizon=h,target=target,
                                    target_week=weeks[target],actual=float(y[target]),point=float(point),**sc,
                                    **{f'q{a}':float(b) for a,b in zip(('025','10','25','50','75','90','975'),q)},
                                    source_sha256=datahash,graph_sha256=graphhash,model_version='six_dim_offline_v1',run_timestamp=stamp.isoformat(),review_state='pending analyst review'))
                        return dict(rows=rows,calibration_seconds=calibration_seconds,forecast_seconds=time.perf_counter()-start)
                    executed=gate.execute(cid,payload,'one-run','executor',stamp,execute);rows=executed['rows'];records.extend(rows)
                    ranking_models=[r['model'] for r in rank if r['eligible']]
                    results.append(dict(disease=disease,year=year,budget=budget,policy=policy,chosen=chosen,targets=len(rows),
                       wape=wape([r['actual'] for r in rows],[r['point'] for r in rows]),wis=float(np.mean([r['wis'] for r in rows])),coverage95=float(np.mean([r['coverage95'] for r in rows])),
                       top1_hit=bool(ranking_models and oracle_losses[ranking_models[0]]<=best*1.05),top3_hit=any(oracle_losses[m]<=best*1.05 for m in ranking_models[:3]),
                       relative_wape_regret=(wape([r['actual'] for r in rows],[r['point'] for r in rows])-best)/best if best else None,
                       selection_fits=selection_fits,calibration_fits=52,forecast_fits=len(indexes),total_fits=selection_fits+52+len(indexes),
                       ranking_seconds=ranking_seconds,selection_seconds=selection_seconds,calibration_seconds=executed['calibration_seconds'],forecast_seconds=executed['forecast_seconds'],
                       short_list=shortlist,selection_losses=loss,wall_clock_budget_exceeded=(ranking_seconds+selection_seconds)>300))
    save('recommendation_fold_results.json',results);save('recommendation_score_traces.json',scores);csvsave('recommendation_predictions.csv',records)
    cis=[]
    for disease in TARGETS:
        a=[r for r in records if r['disease']==disease and r['budget']==30 and r['policy']=='six_dim']
        for other in ('profile_only','no_graph','no_mechanism','no_transfer','budget_search','random','fixed_rule','frozen_no_feedback'):
            b=[r for r in records if r['disease']==disease and r['budget']==30 and r['policy']==other]
            cis.append(dict(disease=disease,comparison='six_dim minus '+other,**bootstrap(a,b,8)))
    save('recommendation_paired_ci.json',cis)
    save('contract_forecast_integration.json',{'contracts_completed':len(results),'all_completed':all(gate.state(f'local-contract-{i}')=='COMPLETED' for i in range(1,len(results)+1)),
       'mode':'offline synthetic reviewer/executor roles only; not real human approval or authenticated HTTP endpoint','audit_events':len(gate.audit)})
    save('recommendation_timing_summary.json',{'selection_wall_budget_minutes':[5,15,30],'selection_seconds_max':max(r['selection_seconds']+r['ranking_seconds'] for r in results),
       'all_minute_caps_nonbinding':not any(r['wall_clock_budget_exceeded'] for r in results),'interpretation':'models are too small for 5/15/30 minute caps to bind; this does not establish time-budget savings or GPU/LLM efficiency'})
    sensitivity(series,graph)
    print(json.dumps({'outer_tasks':9,'policy_budget_runs':len(results),'predictions':len(records),'pairwise_ci':len(cis)},ensure_ascii=False))

def sensitivity(series,graph):
    traces=[];cache={}
    lengths=(4,7,8,15,16,25,26,52,104,156)
    for disease,(weeks,y) in series.items():
        for year in (2020,2021,2022):
            start=min(i for i,w in enumerate(weeks) if w.startswith(str(year)))
            for n in lengths:
                hist=y[start-n:start];future=y[start:start+4]
                bank=[v[start-n:start] for k,(_,v) in series.items() if k!=disease]
                for minimum in (8,16,26):
                    for seasonal in (104,156):
                        for threshold in (40.,50.,60.):
                            rank=ranked(hist,'dengue' if disease=='Dengue Fever' else 'unsupported',graph,bank,admission=Admission(minimum,seasonal,threshold))
                            for k in (1,3,5):
                                admitted=[r['model'] for r in rank if r['admitted']][:k]
                                tick=time.perf_counter();pred=[];actual_calls=0
                                for model in admitted:
                                    point=forecast(hist,model,4);actual_calls+=1;pred.append(point)
                                forecast_seconds=time.perf_counter()-tick
                                # Equal-weight aggregate is specified before outcomes; no test-side selection.
                                point=np.mean(pred,axis=0) if pred else None
                                traces.append(dict(disease=disease,year=year,history_observations=n,minimum=minimum,seasonal=seasonal,threshold=threshold,k=k,
                                    effective_k=len(admitted),rejected=not admitted,selected=admitted,fit_calls=actual_calls,forecast_seconds=forecast_seconds,
                                    wape=wape(future,point) if point is not None else None))
    save('admission_sensitivity.json',traces)

if __name__=='__main__':main()
