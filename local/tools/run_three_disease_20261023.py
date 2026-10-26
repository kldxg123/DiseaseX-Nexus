"""Locked chronological retrospective experiment; no remote deployment or LLM."""
from pathlib import Path
from datetime import datetime,timezone
import sys,json,csv,hashlib,time,urllib.request
import numpy as np
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'packages'))
from predictor_agent.chronological_validation import MODELS,forecast,wape,quantile_forecast,score_quantiles
OUT=ROOT/'reports/three_disease_chronological_20261023';OUT.mkdir(exist_ok=True)
URL='https://data.gov.sg/api/action/datastore_search?resource_id=d_ca168b2cb763640d72c4600a68f9909e&limit=30000'
TARGETS=('Dengue Fever','Campylobacterenterosis','Mumps')
def save(n,o): (OUT/n).write_text(json.dumps(o,ensure_ascii=False,indent=2),encoding='utf-8')
def main():
    protocol={'registered_at':datetime.now(timezone.utc).isoformat(),'targets':TARGETS,'models':MODELS,
      'training':'2012-2017','selection':'2018','calibration':'2019','test':'2020-2022','horizons':[1,2,3,4],
      'selection_metric':'WAPE over all 1..4 week validation targets','intervals':'signed 2019 residual quantiles; no test recalibration',
      'identity':'chronological retrospective; prior exposure cannot be excluded; dengue external period already seen',
      'review_status':'pending human analyst review','limitations':'same country and reporting system; not proof of KG/LLM improvement'}
    if not (OUT/'protocol.json').exists():save('protocol.json',protocol)
    else:
        if (OUT/'metrics.json').exists():raise RuntimeError('Completed experiment is immutable')
        save('parser_repair.json',{'reason':'2017 official source naming transition: Campylobacterenterosis -> Campylobacter enteritis; original exact-label parser rejected incomplete coverage before aggregate results export','outcome_based_target_change':False,'repair_registered_at':datetime.now(timezone.utc).isoformat(),'aliases':['Campylobacterenterosis','Campylobacter enteritis']})
    cache=OUT/'official_response.json'
    raw=cache.read_bytes() if cache.exists() else urllib.request.urlopen(URL,timeout=40).read();cache.write_bytes(raw)
    data=json.loads(raw);records=data['result']['records'];assert data['success'] and len(records)==data['result']['total']
    save('provenance.json',{'url':URL,'retrieved_at':datetime.now(timezone.utc).isoformat(),'sha256':hashlib.sha256(raw).hexdigest(),
       'licence':'Singapore Open Data Licence v1.0','licence_url':'https://data.gov.sg/open-data-licence',
       'attribution':'Contains information from MOH Weekly Infectious Disease Bulletin Cases, accessed 2026-10-23 from data.gov.sg, under Singapore Open Data Licence v1.0. No official endorsement.'})
    results=[];predictions=[];profiles=[]
    for disease in TARGETS:
        aliases={disease,'Campylobacter enteritis'} if disease=='Campylobacterenterosis' else {disease}
        rows=sorted([r for r in records if r['disease'] in aliases],key=lambda r:r['epi_week'])
        if not rows:raise ValueError('Predeclared disease unavailable: '+disease)
        weeks=[r['epi_week'] for r in rows];assert len(weeks)==len(set(weeks))
        vals=np.array([float(r['no._of_cases']) for r in rows]);assert np.isfinite(vals).all() and (vals>=0).all()
        assert weeks[0]=='2012-W01' and weeks[-1].startswith('2022-W')
        # Require no omitted report weeks within a year (official epi-week labels preserved).
        for year in range(2012,2023):
            ws=[int(w[-2:]) for w in weeks if w.startswith(str(year))];assert ws==list(range(1,max(ws)+1))
        profiles.append({'disease':disease,'n':len(rows),'first':weeks[0],'last':weeks[-1],'zero_fraction':float((vals==0).mean()),'missing_cells':0})
        collected={};started=time.perf_counter()
        for model in MODELS:
            out=[]
            for origin,week in enumerate(weeks):
                if not ('2018'<=week[:4]<='2022'):continue
                yh=forecast(vals[:origin],model,4)
                for h in range(1,5):
                    t=origin+h-1
                    if t>=len(vals):continue
                    target=weeks[t];split='selection' if target.startswith('2018') else 'calibration' if target.startswith('2019') else 'test' if target>='2020' else None
                    # Predictions crossing partition boundaries are assigned by origin AND target.
                    origin_split='selection' if week.startswith('2018') else 'calibration' if week.startswith('2019') else 'test'
                    if split!=origin_split:continue
                    out.append({'origin':origin,'target':t,'week':target,'horizon':h,'actual':float(vals[t]),'point':float(yh[h-1]),'split':split})
            collected[model]=out
        selection={m:wape([r['actual'] for r in rs if r['split']=='selection'],[r['point'] for r in rs if r['split']=='selection']) for m,rs in collected.items()}
        chosen=min(MODELS,key=lambda m:float('inf') if selection[m] is None else selection[m])
        for model,rows in collected.items():
            for h in range(1,5):
                cal=[r['actual']-r['point'] for r in rows if r['split']=='calibration' and r['horizon']==h]
                for r in rows:
                    if r['split']!='test' or r['horizon']!=h:continue
                    q=quantile_forecast(r['point'],cal);sc=score_quantiles(r['actual'],q)
                    predictions.append(dict(disease=disease,model=model,selected=model==chosen,**r,**sc,**{f'q{v}':float(x) for v,x in zip(['025','10','25','50','75','90','975'],q)}))
            for h in [0,1,2,3,4]:
                subset=[r for r in predictions if r['disease']==disease and r['model']==model and (h==0 or r['horizon']==h)]
                results.append({'disease':disease,'model':model,'selected':model==chosen,'horizon':'all' if h==0 else h,'n':len(subset),
                 'wape':wape([r['actual'] for r in subset],[r['point'] for r in subset]),'wis':float(np.mean([r['wis'] for r in subset])),
                 'coverage95':float(np.mean([r['coverage95'] for r in subset])),'width95':float(np.mean([r['width95'] for r in subset]))})
        save(disease.replace(' ','_')+'_selection.json',{'validation_scores':selection,'chosen':chosen,'elapsed_seconds':time.perf_counter()-started})
    save('profiles.json',profiles);save('metrics.json',results)
    with (OUT/'predictions.csv').open('w',encoding='utf-8',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=list(predictions[0]));wr.writeheader();wr.writerows(predictions)
    print(json.dumps({'profiles':profiles,'overall':[r for r in results if r['horizon']=='all']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
