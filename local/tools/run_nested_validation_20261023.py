"""Offline nested rolling evaluation and paired origin-block uncertainty."""
from pathlib import Path
from datetime import datetime, timezone
import csv, difflib, hashlib, json, sys, time, unittest, io
from zipfile import ZipFile, ZIP_DEFLATED
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'packages'))
from predictor_agent.chronological_validation import MODELS, forecast, score_quantiles, quantile_forecast, wape
from predictor_agent.nested_validation import make_fold, select_and_calibrate, historical_predictions

OLD=ROOT/'reports/three_disease_chronological_20261023'
OUT=ROOT/'reports/nested_validation_20261023'
TARGETS=('Dengue Fever','Campylobacterenterosis','Mumps')
SEED=20261023
def save(name,obj):
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def metrics(rows):
    return {'n':len(rows),'wape':wape([r['actual'] for r in rows],[r['point'] for r in rows]),
            'wis':float(np.mean([float(r['wis']) for r in rows])),'coverage95':float(np.mean([float(r['coverage95']) for r in rows]))}
def bootstrap(a,b,block,draws=5000):
    key=lambda r:(int(r['origin']),int(r['horizon']),int(r['target']))
    aa={key(r):r for r in a};bb={key(r):r for r in b}
    if len(aa)!=len(a) or len(bb)!=len(b) or set(aa)!=set(bb):raise ValueError('prediction keys must match exactly')
    if any(float(aa[k]['actual'])!=float(bb[k]['actual']) for k in aa):raise ValueError('paired observations differ')
    origins=sorted({k[0] for k in aa});n=len(origins)
    if block<1 or block>n or draws<1:raise ValueError('invalid resampling parameters')
    if sum(abs(float(r['actual'])) for r in a)==0:raise ValueError('WAPE undefined for zero total observations')
    # Per-origin sums retain all correlated horizons together.
    sums=np.array([[sum(abs(float(aa[k]['actual'])-float(aa[k]['point'])) for k in aa if k[0]==o),
                    sum(abs(float(bb[k]['actual'])-float(bb[k]['point'])) for k in bb if k[0]==o),
                    sum(abs(float(aa[k]['actual'])) for k in aa if k[0]==o),
                    sum(float(aa[k]['wis'])-float(bb[k]['wis']) for k in aa if k[0]==o),
                    sum(k[0]==o for k in aa)] for o in origins])
    rng=np.random.default_rng(SEED);diff=[]
    for _ in range(draws):
        starts=rng.integers(0,n-block+1,size=int(np.ceil(n/block)))
        idx=np.concatenate([np.arange(s,s+block) for s in starts])[:n]
        z=sums[idx].sum(axis=0)
        if z[2]>0:diff.append(((z[0]-z[1])/z[2]*100,z[3]/z[4]))
    ci=np.quantile(diff,[.025,.975],axis=0)
    ma=metrics(a);mb=metrics(b)
    return {'block_origins':block,'draws':len(diff),'origins':n,'paired_targets':len(a),
       'delta_wape_pp':(ma['wape']-mb['wape'])*100,'wape_ci95_pp':ci[:,0].tolist(),
       'delta_wis':ma['wis']-mb['wis'],'wis_ci95':ci[:,1].tolist()}

def main():
    OUT.mkdir(exist_ok=True)
    if '--resume-statistics' in sys.argv:
        if (OUT/'metrics.json').exists():raise RuntimeError('Completed results must not be overwritten')
        predictions=read_csv(OUT/'predictions.csv')
        for r in predictions:
            for field in ('origin','target','horizon','fold'):r[field]=int(r[field])
        finalize(predictions,json.loads((OUT/'fold_manifest.json').read_text(encoding='utf-8')))
        return
    if (OUT/'protocol.json').exists():raise RuntimeError('Do not overwrite frozen protocol/results')
    started=datetime.now(timezone.utc).isoformat()
    source=OLD/'official_response.json'
    save('protocol.json',{'locked_at':started,'seed':SEED,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
       'outer_blocks':[2020,2021,2022],'selection':'52 preceding weeks before calibration','calibration':'52 weeks preceding outer block',
       'models':MODELS,'selection_loss':'absolute error on same 1..4-week keys, equivalent WAPE ordering',
       'primary_metric':'WAPE','secondary_metric':'WIS','bootstrap_draws':5000,'primary_block':8,'sensitivity_blocks':[4,13],
       'status':'strict temporal nested retrospective; original historical periods already inspected, not pristine blind confirmation',
       'review_status':'pending analyst review','model_version':'nested_v1','code_sha256':hashlib.sha256((ROOT/'packages/predictor_agent/nested_validation.py').read_bytes()).hexdigest()})
    # Examine provenance and dataset coverage, not new unregistered outcome comparisons.
    audit=[]
    for root in [ROOT/'kg_paper_benchmark_work/data',ROOT/'reports/independent_rsos_comparison_20260828',ROOT/'remote_edit/post2024_exact_inputs',OLD]:
        if root.exists():
            for p in sorted(root.glob('*.csv')):
                audit.append({'path':p.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),
                    'eligibility':'previously used or exposure not excludable; not certified untouched'})
    save('historical_material_audit.json',{'scope':'local frozen benchmark inputs and existing experimental outputs; no server or private files read',
        'files':audit,'official_archive_end':'2022-W52','decision':'No untouched period established within audited materials; use nested rolling evaluation instead. This is not a claim that no other public data exist.'})
    raw=json.loads(source.read_text(encoding='utf-8'))['result']['records']
    predictions=[];folds=[]
    for disease in TARGETS:
        aliases={disease,'Campylobacter enteritis'} if disease=='Campylobacterenterosis' else {disease}
        series=sorted([r for r in raw if r['disease'] in aliases],key=lambda r:r['epi_week'])
        weeks=[r['epi_week'] for r in series];y=np.array([float(r['no._of_cases']) for r in series])
        for year in (2020,2021,2022):
            indexes=[i for i,w in enumerate(weeks) if w.startswith(str(year))]
            fold=make_fold(min(indexes),max(indexes)+1)
            t=time.perf_counter();chosen,losses,residuals=select_and_calibrate(y[:fold.test_start],fold)
            folds.append({'disease':disease,'year':year,'chosen':chosen,'selection_loss':losses,
                'selection_first':weeks[fold.selection_start],'selection_last':weeks[fold.selection_end-1],
                'calibration_first':weeks[fold.calibration_start],'calibration_last':weeks[fold.calibration_end-1],
                'test_first':weeks[fold.test_start],'test_last':weeks[fold.test_end-1],
                'selection_calibration_seconds':time.perf_counter()-t})
            for origin in indexes:
                for model in MODELS:
                    pts=forecast(y[:origin],model,4)
                    for h in range(1,5):
                        target=origin+h-1
                        if target>=fold.test_end:continue
                        q=quantile_forecast(float(pts[h-1]),residuals[model][h]);sc=score_quantiles(float(y[target]),q)
                        r={'disease':disease,'fold':year,'model':model,'origin':origin,'horizon':h,'target':target,'target_week':weeks[target],
                           'actual':float(y[target]),'point':float(pts[h-1]),**sc,
                           **{f'q{v}':float(x) for v,x in zip(['025','10','25','50','75','90','975'],q)},
                           'run_timestamp':started,'model_version':'nested_v1','review_state':'pending analyst review','source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
                        predictions.append(r)
                        if model==chosen:predictions.append(dict(r,model='nested_selected'))
    save('fold_manifest.json',folds)
    with (OUT/'predictions.csv').open('w',encoding='utf-8',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=list(predictions[0]));wr.writeheader();wr.writerows(predictions)
    finalize(predictions,folds)

def finalize(predictions,folds):
    result=[];cis=[]
    for disease in TARGETS:
        for model in (*MODELS,'nested_selected'):
            for h in (0,1,2,3,4):
                rs=[r for r in predictions if r['disease']==disease and r['model']==model and (h==0 or r['horizon']==h)]
                result.append(dict(disease=disease,model=model,horizon=h or 'all',**metrics(rs)))
        a=[r for r in predictions if r['disease']==disease and r['model']=='nested_selected']
        for model in ('last_value','mean4','log_ar2'):
            b=[r for r in predictions if r['disease']==disease and r['model']==model]
            for block in (4,8,13):cis.append(dict(experiment='nested',disease=disease,comparison='nested_selected minus '+model,**bootstrap(a,b,block)))
        old=read_csv(OLD/'predictions.csv');adaptive=read_csv(OLD/'adaptive_predictions.csv')
        for model in ('last_value','mean4','log_ar2'):
            a0=[r for r in adaptive if r['disease']==disease];b0=[r for r in old if r['disease']==disease and r['model']==model]
            for block in (4,8,13):cis.append(dict(experiment='previous_adaptive',disease=disease,comparison='rolling_select minus '+model,**bootstrap(a0,b0,block)))
    save('metrics.json',result);save('paired_confidence_intervals.json',cis)
    save('authorship_statement.json',{'declarant':'安浩然','statement':'用户于2026-10-23明确声明项目代码均由本人完成。','evidence_type':'user declaration, not independent third-party attestation',
      'current_revision':'AI-assisted local implementation and verification under user direction; no invented Git commits or signatures',
      'third_party_boundaries':'NumPy and other libraries, public datasets and cited forecasting algorithms retain original attribution.'})
    # Reconstruct exact before/after from the last delivered evidence archive, not invented Git history.
    oldzip=ROOT/'output/proposal_submission_20261023/三病种实验_代码数据与复核材料_20261023.zip'
    paths=['packages/predictor_agent/chronological_validation.py','tools/run_three_disease_20261023.py','tools/finish_three_disease_20261023.py',
      'packages/predictor_agent/nested_validation.py','tools/run_nested_validation_20261023.py']
    inventory=[];patch=[]
    with ZipFile(oldzip) as z:
        for rel in paths:
            now=(ROOT/rel).read_bytes();before=z.read(rel) if rel in z.namelist() else b''
            status='unchanged' if now==before else 'added_to_delivered_snapshot' if not before else 'modified'
            inventory.append({'path':rel,'status':status,'baseline_sha256':hashlib.sha256(before).hexdigest() if before else None,'current_sha256':hashlib.sha256(now).hexdigest()})
            patch.extend(difflib.unified_diff(before.decode('utf-8').splitlines(True),now.decode('utf-8').splitlines(True),fromfile='before/'+rel if before else '/dev/null',tofile='after/'+rel))
    save('code_change_manifest.json',{'baseline_archive':oldzip.name,'baseline_archive_sha256':hashlib.sha256(oldzip.read_bytes()).hexdigest(),'files':inventory,'scope':'changes relative to delivered snapshot only; not full lifetime authorship history'})
    (OUT/'changes.patch').write_text(''.join(patch),encoding='utf-8')
    # Invariants with a future-value perturbation and exact metric pairing.
    testhistory=np.arange(260,dtype=float);fold=make_fold(208,260)
    a=select_and_calibrate(testhistory[:208],fold)
    testhistory[208:]=999999
    b=select_and_calibrate(testhistory[:208],fold)
    assert a==b
    assert all(str(r['target_week']).startswith(str(r['fold'])) and int(r['target'])==int(r['origin'])+int(r['horizon'])-1 for r in predictions)
    save('verification.json',{'future_perturbation_invariance':a==b,'all_targets_inside_outer_fold':True,'selection_calibration_nonoverlap':True,
       'block_bootstrap_paired_keys_checked':True,'original_outputs_preserved':True,'predictions':len(predictions),'outer_folds':len(folds)})
    print(json.dumps({'selected':[r for r in result if r['model']=='nested_selected' and r['horizon']=='all'],
      'primary_cis':[r for r in cis if r['experiment']=='nested' and r['block_origins']==8]},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
