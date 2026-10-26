"""Training-only candidate generation and rolling model selection, no I/O."""
from __future__ import annotations
import json,re
import numpy as np

BASE=('last_value','mean4','seasonal52')
OPTIONAL=('log_ar2','log_ar4','mean8','mean13','drift4','drift13')
ALL=BASE+OPTIONAL
Q=np.array([.025,.1,.25,.5,.75,.9,.975])

def forecast(y,model,h=4):
    y=np.asarray(y,dtype=float)
    if y.ndim!=1 or len(y)<104 or not np.isfinite(y).all() or (y<0).any():raise ValueError('invalid history')
    if model not in ALL:raise ValueError('unknown model')
    if model.startswith('log_ar'):
        lag=int(model[-1]);z=np.log1p(y)
        x=np.column_stack([np.ones(len(z)-lag)]+[z[lag-k:len(z)-k] for k in range(1,lag+1)])
        beta=np.linalg.lstsq(x,z[lag:],rcond=None)[0];hist=list(z);pred=[]
        for _ in range(h):
            value=float(np.clip(np.dot([1]+hist[-lag:][::-1],beta),0,np.log1p(max(10,10*y.max()))))
            hist.append(value);pred.append(np.expm1(value))
        return np.array(pred)
    if model=='last_value':return np.repeat(y[-1],h)
    if model=='seasonal52':return y[-52:-52+h] if h<52 else None
    if model.startswith('mean'):return np.repeat(np.mean(y[-int(model[4:]):]),h)
    n=int(model[5:]);slope=(y[-1]-y[-n])/(n-1)
    return np.clip(y[-1]+np.arange(1,h+1)*slope,0,max(10,10*y.max()))

def profile(y):
    y=np.asarray(y,dtype=float);z=y[-104:]
    def corr(lag):
        a=z[lag:];b=z[:-lag]
        return float(np.corrcoef(a,b)[0,1]) if a.std()>0 and b.std()>0 else 0.
    return {'observations':len(y),'missing_fraction':0,'zero_fraction':round(float((z==0).mean()),5),
        'mean_last52':round(float(z[-52:].mean()),5),'cv_last52':round(float(z[-52:].std()/max(z[-52:].mean(),1e-9)),5),
        'lag1_corr':round(corr(1),5),'lag52_corr':round(corr(52),5),
        'recent_to_previous_13_ratio':round(float((z[-13:].mean()+1)/(z[-26:-13].mean()+1)),5)}

def deterministic(p):
    return ['log_ar2','drift4'] if p['lag1_corr']>=.75 else ['mean8','mean13']

def prompt(p):
    return ('Choose exactly two optional weekly count forecasting models from the registry. '
      'All input statistics use past observations only. Do not predict case counts. '
      'The downstream selector compares historical errors and ALWAYS keeps last_value, mean4, seasonal52. '
      'Optional registry: log_ar2 (log1p AR with 2 lags); log_ar4 (4 lags); mean8 (8-week mean); '
      'mean13 (13-week mean); drift4 (recent 4-week linear drift); drift13 (13-week drift). '
      'Return only JSON {"candidates":["id1","id2"]}. No other fields. Data profile: '+json.dumps(p,sort_keys=True))

def validate_response(raw):
    try:
        value=json.loads(raw)
        if set(value)!={'candidates'}:raise ValueError('schema')
        ids=value['candidates']
        if not isinstance(ids,list) or len(ids)!=2 or len(set(ids))!=2 or not all(m in OPTIONAL for m in ids):raise ValueError('candidates')
        return ids
    except (ValueError,TypeError):return None

def pool(ids):
    if len(ids)!=2 or len(set(ids))!=2 or any(m not in OPTIONAL for m in ids):raise ValueError('bad optional candidates')
    return BASE+tuple(ids)

def select(y,candidates,origins=13):
    y=np.asarray(y,dtype=float)
    if not set(BASE)<=set(candidates):raise ValueError('baseline dropped')
    if len(y)<104+origins+4:raise ValueError('history insufficient')
    loss={m:0. for m in candidates}
    for origin in range(len(y)-origins-3,len(y)-3):
        actual=y[origin:origin+4]
        for model in candidates:loss[model]+=float(np.abs(actual-forecast(y[:origin],model)).sum())
    return min(candidates,key=lambda m:(loss[m],ALL.index(m))),loss

def quantiles(point,residuals):
    if len(residuals)<26:raise ValueError('calibration insufficient')
    return np.maximum(0,point+np.quantile(residuals,Q))

def score(actual,q):
    if len(q)!=7 or not np.isfinite(q).all() or (np.diff(q)<0).any():raise ValueError('quantiles')
    values=[alpha/2*(hi-lo+2/alpha*max(lo-actual,0)+2/alpha*max(actual-hi,0)) for alpha,lo,hi in [( .5,q[2],q[4]),(.2,q[1],q[5]),(.05,q[0],q[6])]]
    return {'wis':float((.5*abs(actual-q[3])+sum(values))/3.5),'coverage95':int(q[0]<=actual<=q[-1]),'width95':float(q[-1]-q[0])}

def aggregate(rows):
    denominator=sum(abs(r['actual']) for r in rows)
    return {'n':len(rows),'wape':sum(abs(r['actual']-r['point']) for r in rows)/denominator if denominator else None,
      'wis':float(np.mean([r['wis'] for r in rows])),'coverage95':float(np.mean([r['coverage95'] for r in rows])),
      'width95':float(np.mean([r['width95'] for r in rows]))}

def paired_ci(rows_a,rows_b,block=8,draws=5000):
    def keyed(rows):return {(r['origin'],r['horizon']):r for r in rows}
    a,b=keyed(rows_a),keyed(rows_b)
    if a.keys()!=b.keys():raise ValueError('mismatched targets')
    origins=sorted(set(k[0] for k in a));values=[]
    for o in origins:
        keys=[k for k in a if k[0]==o]
        if any(a[k]['actual']!=b[k]['actual'] for k in keys):raise ValueError('mismatched actual')
        values.append([sum(abs(a[k]['actual']-a[k]['point']) for k in keys),sum(abs(b[k]['actual']-b[k]['point']) for k in keys),sum(abs(a[k]['actual']) for k in keys)])
    v=np.array(values);n=len(v);rng=np.random.default_rng(20261023)
    starts=rng.integers(0,n-block+1,size=(draws,int(np.ceil(n/block))))
    ix=(starts[:,:,None]+np.arange(block)).reshape(draws,-1)[:,:n];s=v[ix].sum(axis=1)
    valid=s[:,2]>0;delta=(s[valid,0]-s[valid,1])/s[valid,2]*100;t=v.sum(axis=0)
    return {'delta_wape_pp':float((t[0]-t[1])/t[2]*100),'ci95_pp':np.quantile(delta,[.025,.975]).tolist(),
      'block_origins':block,'draws':draws,'valid_draws':int(valid.sum()),'origins':n}
