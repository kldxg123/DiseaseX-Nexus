"""One frozen server experiment, no production connections or mutations."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
import csv,hashlib,json,os,sys,time,urllib.request,subprocess,io,gc
import numpy as np
import frozen_llm_validation_core_20261023 as c

ROOT=Path(__file__).resolve().parent
TARGETS=('Dengue Fever','Campylobacterenterosis','Mumps')
MODEL_REPO='Qwen/Qwen2.5-1.5B-Instruct-GGUF'
MODEL_FILE='qwen2.5-1.5b-instruct-q4_k_m.gguf'

def now():return datetime.now(timezone.utc).isoformat()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(name,value):
    p=ROOT/name
    if p.exists():raise RuntimeError('Immutable artifact exists: '+name)
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2))
def status(stage,**kw):(ROOT/'experiment_status.json').write_text(json.dumps(dict(stage=stage,updated_at=now(),**kw)))
def csvwrite(name,rows):
    p=ROOT/name
    if p.exists():raise RuntimeError('Immutable table exists')
    with p.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'AcademicForecastValidation/1.0'})
    with urllib.request.urlopen(req,timeout=60) as r:return r.read()

def register():
    if (ROOT/'protocol.json').exists():return
    protocol={'registered_at':now(),'version':'frozen-v1','diseases':TARGETS,
      'historical_llm_ablation_years':[2020,2021,2022],'new_window_year':2024,'context_year':2023,
      'new_window_identity':'newly acquired historical temporal holdout, conditional on exposure audit; not prospective, third-party-blind, or preregistered with an external registry',
      'new_window_policy':'all 52 weeks; no performance-based window replacement; retain missing-data failures',
      'base_models':c.BASE,'optional_models':c.OPTIONAL,'optional_per_arm':2,
      'arms':['deterministic','real_llm'],'candidate_refresh':'once before first target of each year, without year or disease name in prompt',
      'selector':'13 latest fully observed four-week validation origins, joint absolute error; deterministic tie-break registry order',
      'all_baselines_retained':True,'horizons':[1,2,3,4],'calibration':'signed residuals from immediately preceding year, frozen throughout test year',
      'source_revision_identity':'current frozen files and annual retrospective compilation; not guaranteed as-of publication vintages',
      'llm':{'repo':MODEL_REPO,'file':MODEL_FILE,'gpu_layers':0,'threads':4,'temperature':0,'seed':20261023,'max_output_tokens':96,'calls_per_task':1,
        'schema':'exactly 2 distinct optional model IDs; malformed output falls back to deterministic candidates, never removes baselines'},
      'budget':'same upper bound of 5 candidates, 65 candidate-history fits per selection, 1 proposal <=96 output tokens; deterministic arm may leave LLM budget unused; report extra inference costs, not claim equal total cost',
      'ci':{'draws':5000,'primary_block':8,'sensitivity_blocks':[4,13],'seed':20261023,'comparisons':'LLM minus deterministic; each selected arm minus same-year best fixed candidate (post hoc reference only)'},
      'review_status':'pending human analyst review','primary_metric':'WAPE by disease; no case-scale pooling across diseases',
      'limits':['small CPU quantized model and 9 optional/baseline models, not full deployed intelligent agent','no clinical acceptability or equivalence claim','LLM pretraining contamination cannot be completely excluded; no task identity or future labels supplied'],
      'input_sha256':sha(ROOT/'official_response.json'),
      'code_sha256':{n:sha(ROOT/n) for n in ['run_frozen_llm_validation_20261023.py','frozen_llm_validation_core_20261023.py','test_frozen_llm_validation_20261023.py']}}
    write('protocol.json',protocol)
    with (ROOT/'unit_tests.log').open('w') as f:r=subprocess.run([sys.executable,str(ROOT/'test_frozen_llm_validation_20261023.py'),'-v'],stdout=f,stderr=f)
    write('unit_tests_result.json',{'exit_code':r.returncode,'tests':12})
    if r.returncode:raise RuntimeError('tests failed')

def model():
    status('model_metadata')
    if not (ROOT/'model_source.json').exists():
        meta=json.loads(fetch('https://huggingface.co/api/models/'+MODEL_REPO+'?blobs=true'))
        revision=meta['sha'];entry=next(s for s in meta['siblings'] if s['rfilename']==MODEL_FILE)
        expected=entry.get('lfs',{}).get('sha256')
        write('model_source.json',{'repo':MODEL_REPO,'revision':revision,'filename':MODEL_FILE,'expected_sha256':expected,'licence':'Apache-2.0','metadata_at':now()})
    source=json.loads((ROOT/'model_source.json').read_text());revision=source['revision'];expected=source['expected_sha256']
    path=ROOT/'model.gguf'
    if not path.exists():
        status('model_download')
        url=f'https://huggingface.co/{MODEL_REPO}/resolve/{revision}/{MODEL_FILE}'
        with urllib.request.urlopen(url,timeout=90) as response,path.with_suffix('.part').open('wb') as f:
            while True:
                chunk=response.read(4*1024*1024)
                if not chunk:break
                f.write(chunk)
        path.with_suffix('.part').rename(path)
    if expected and sha(path)!=expected:raise RuntimeError('model checksum mismatch')
    from llama_cpp import Llama
    status('model_loading')
    start=time.perf_counter()
    llm=Llama(model_path=str(path),n_ctx=2048,n_threads=4,n_threads_batch=4,n_batch=128,n_gpu_layers=0,verbose=False,seed=20261023)
    if not (ROOT/'model_runtime.json').exists():
        import importlib.metadata
        write('model_runtime.json',{'sha256':sha(path),'load_seconds':time.perf_counter()-start,'llama_cpp_python':importlib.metadata.version('llama-cpp-python'),'numpy':np.__version__,'gpu_layers':0,'threads':4,'model_architecture':llm.metadata.get('general.architecture')})
    return llm

def source_arrays():
    records=json.loads((ROOT/'official_response.json').read_text())['result']['records'];result={}
    for disease in TARGETS:
        aliases={disease,'Campylobacter enteritis'} if disease==TARGETS[1] else {disease}
        rows=sorted([r for r in records if r['disease'] in aliases],key=lambda r:r['epi_week'])
        weeks=[r['epi_week'] for r in rows];y=[float(r['no._of_cases']) for r in rows]
        assert len(weeks)==574 and len(set(weeks))==574 and weeks[0]=='2012-W01' and weeks[-1]=='2022-W52'
        result[disease]=(weeks,np.array(y))
    return result

def proposals(llm,arrays,years):
    results=[]
    schema={'type':'object','properties':{'candidates':{'type':'array','items':{'type':'string','enum':list(c.OPTIONAL)},'minItems':2,'maxItems':2}},'required':['candidates'],'additionalProperties':False}
    for disease,(weeks,y) in arrays.items():
        for year in years:
            path=ROOT/f'proposal_{TARGETS.index(disease)}_{year}.json'
            if path.exists():results.append(json.loads(path.read_text()));continue
            origin=next(i for i,w in enumerate(weeks) if w.startswith(str(year)))
            p=c.profile(y[:origin]);prompt=c.prompt(p);start=time.perf_counter()
            status('real_llm_inference',task=len(results)+1,year=year)
            answer=llm.create_chat_completion(messages=[{'role':'system','content':'You select registered forecasting candidates using only supplied historical statistics.'},{'role':'user','content':prompt}],temperature=0,max_tokens=96,response_format={'type':'json_object','schema':schema},seed=20261023)
            seconds=time.perf_counter()-start;raw=answer['choices'][0]['message']['content'];validated=c.validate_response(raw)
            r={'disease':disease,'year':year,'profile':p,'prompt':prompt,'raw_response':raw,'usage':answer.get('usage'),'finish_reason':answer['choices'][0].get('finish_reason'),
               'seconds':seconds,'schema_valid':validated is not None,'fallback':validated is None,'deterministic_candidates':c.deterministic(p),'llm_candidates':validated or c.deterministic(p),
               'history_end':weeks[origin-1],'target_labels_provided':False,'proposed_at':now()}
            write(path.name,r);results.append(r)
    return results

def evaluate(arrays,proposals,years,prefix):
    rows=[];costs=[];all_traces=[];start=time.perf_counter()
    for disease,(weeks,y) in arrays.items():
        for year in years:
            status('rolling_evaluation',disease_index=TARGETS.index(disease),year=year)
            prop=next(p for p in proposals if p['disease']==disease and p['year']==year)
            origins=[i for i,w in enumerate(weeks) if w.startswith(str(year))]
            assert len(origins) in (52,53)
            cal_origins=[i for i,w in enumerate(weeks) if w.startswith(str(year-1))]
            residual={(m,h):[] for m in c.ALL for h in range(1,5)}
            cache={};fits=0
            def prediction(i,m):
                nonlocal fits
                key=(i,m)
                if key not in cache:cache[key]=c.forecast(y[:i],m);fits+=1
                return cache[key]
            for i in cal_origins:
                for m in c.ALL:
                    yh=prediction(i,m)
                    for h in range(1,5):
                        t=i+h-1
                        if t<len(y) and weeks[t].startswith(str(year-1)):residual[m,h].append(float(y[t]-yh[h-1]))
            for arm,key in [('deterministic','deterministic_candidates'),('real_llm','llm_candidates')]:
                candidates=c.pool(prop[key]);logical_selection=0;logical_forecast=0;chosen=Counter();arm_start=time.perf_counter()
                for i in origins:
                    loss={m:0. for m in candidates}
                    for vo in range(i-16,i-3):
                        assert vo+3<i
                        for m in candidates:loss[m]+=float(np.abs(y[vo:vo+4]-prediction(vo,m)).sum());logical_selection+=1
                    selected=min(candidates,key=lambda m:(loss[m],c.ALL.index(m)));chosen[selected]+=1;yh=prediction(i,selected);logical_forecast+=1
                    all_traces.append({'disease':disease,'year':year,'arm':arm,'origin':i,'history_end':weeks[i-1],'selected':selected,'pool':list(candidates),'validation_last_target':weeks[i-1],'loss':loss})
                    for h in range(1,5):
                        t=i+h-1
                        if t>=len(y) or not weeks[t].startswith(str(year)):continue
                        q=c.quantiles(yh[h-1],residual[selected,h]);s=c.score(y[t],q)
                        rows.append(dict(disease=disease,year=year,arm=arm,origin=i,target_week=weeks[t],horizon=h,selected=selected,actual=float(y[t]),point=float(yh[h-1]),**s,**{f'q{k}':float(v) for k,v in zip(['025','10','25','50','75','90','975'],q)}))
                costs.append({'disease':disease,'year':year,'arm':arm,'candidate_count':len(candidates),'logical_selection_fits':logical_selection,'forecast_fits':logical_forecast,
                  'calibration_fits_in_pool':len(cal_origins)*len(candidates),'cached_numeric_seconds':time.perf_counter()-arm_start,'llm_seconds':prop['seconds'] if arm=='real_llm' else 0,'selected_counts':dict(chosen),'note':'shared cache timing is not independent end-to-end latency'})
            for m in c.ALL:
                for i in origins:
                    yh=prediction(i,m)
                    for h in range(1,5):
                        t=i+h-1
                        if t>=len(y) or not weeks[t].startswith(str(year)):continue
                        q=c.quantiles(yh[h-1],residual[m,h]);s=c.score(y[t],q)
                        rows.append(dict(disease=disease,year=year,arm=m,origin=i,target_week=weeks[t],horizon=h,selected=m,actual=float(y[t]),point=float(yh[h-1]),**s,**{f'q{k}':float(v) for k,v in zip(['025','10','25','50','75','90','975'],q)}))
    csvwrite(prefix+'_predictions.csv',rows);write(prefix+'_selection_traces.json',all_traces);write(prefix+'_costs.json',costs)
    metrics=[];ci=[]
    for disease in TARGETS:
        subset=[r for r in rows if r['disease']==disease]
        for arm in ['deterministic','real_llm',*c.ALL]:
            for h in ['all',1,2,3,4]:
                rs=[r for r in subset if r['arm']==arm and (h=='all' or r['horizon']==h)]
                metrics.append(dict(disease=disease,arm=arm,horizon=h,**c.aggregate(rs)))
        fixed=[r for r in metrics if r['disease']==disease and r['horizon']=='all' and r['arm'] in c.ALL]
        best=min(fixed,key=lambda r:r['wape']);baseline=min([r for r in fixed if r['arm'] in c.BASE],key=lambda r:r['wape'])
        for a,b in [('real_llm','deterministic'),('real_llm',best['arm']),('deterministic',best['arm']),('real_llm',baseline['arm'])]:
            for block in [4,8,13]:ci.append(dict(disease=disease,comparison=a+' minus '+b,**c.paired_ci([r for r in subset if r['arm']==a],[r for r in subset if r['arm']==b],block)))
    write(prefix+'_metrics.json',metrics);write(prefix+'_confidence_intervals.json',ci)
    write(prefix+'_summary.json',{'metrics':[r for r in metrics if r['horizon']=='all'],'confidence_intervals':[r for r in ci if r['block_origins']==8],
      'real_llm_tasks':len(proposals),'valid_responses':sum(p['schema_valid'] for p in proposals),'total_inference_seconds':sum(p['seconds'] for p in proposals),'numeric_stage_seconds':time.perf_counter()-start})
    return metrics

def main():
    register();arrays=source_arrays();llm=model();ps=proposals(llm,arrays,[2020,2021,2022]);del llm;gc.collect()
    evaluate(arrays,ps,[2020,2021,2022],'historical_llm')
    status('historical_llm_completed_new_window_pending',real_calls=len(ps))
    write('result_summary.json',{'historical_llm_ablation_completed':True,'new_window_completed':False,'real_llm_calls':len(ps),'review_status':'pending human analyst review'})

if __name__=='__main__':
    try:main()
    except Exception as e:
        status('failed',error_type=type(e).__name__)
        (ROOT/'failure.log').write_text(type(e).__name__+': '+str(e).split('\n')[0][:250])
        raise
