"""Auditable six-dimension prototype over the four registered offline adapters.

Graph baseline requirements provide partial applicability evidence, not proof
of forecasting superiority. Unsupported disease aliases do not inherit evidence.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Sequence
import numpy as np
from .chronological_validation import MODELS

@dataclass(frozen=True)
class Admission:
    minimum_observations: int=8
    seasonal_observations: int=104
    threshold: float=50.

def profile(history: Sequence[float]) -> np.ndarray:
    y=np.asarray(history,dtype=float)
    if y.ndim!=1 or not np.isfinite(y).all() or (y<0).any():raise ValueError('finite nonnegative weekly counts required')
    cv=float(np.std(y)/(np.mean(y)+1)) if len(y) else 0.
    corr=0.
    if len(y)>104 and np.std(y[52:])>0 and np.std(y[:-52])>0:corr=float(np.corrcoef(y[52:],y[:-52])[0,1])
    return np.array([len(y)/156,float(np.mean(y==0)) if len(y) else 0.,min(cv,10)/10,corr])

def ranked(history: Sequence[float],disease_id: str,graph: dict,bank: Sequence[Sequence[float]]=(),policy: str='six_dim',admission: Admission=Admission()):
    y=np.asarray(history,dtype=float);x=profile(y)
    alias={'last_value':'last_value_baseline','mean4':'moving_average_baseline','seasonal52':'seasonal_baseline'}
    # Only explicit disease rules with satisfied available conditions are used.
    rules=[]
    for rule in graph['rules']:
        if disease_id not in rule.get('disease_ids',[]):continue
        when=rule.get('when',{})
        matches=all((not bool(v) if k=='has_climate_or_weather' else 4<=v if k=='horizon_max' else False) for k,v in when.items())
        if matches:rules.append(rule)
    rows=[]
    for model in MODELS:
        needed=admission.seasonal_observations if model=='seasonal52' else admission.minimum_observations
        eligible=len(y)>=max(needed,104 if model=='seasonal52' else 8)
        supporting=[r for r in rules if alias.get(model) in (*r.get('recommend',[]),*r.get('required_baselines',[])) and r.get('evidence_ids')]
        distances=[float(np.mean(np.abs(x-profile(v)))) for v in bank if len(v)>=needed]
        dims={'D':float(.4*min(len(y)/max(needed,1),1)+.3+.3*min(len(y)/(52 if model=='seasonal52' else 2 if model=='log_ar2' else 4 if model=='mean4' else 1),1)),
              'M':float(bool(supporting)),'T':float(np.exp(-min(distances))) if distances else 0.,
              'E':.5 if supporting else 0.,'P':1.,'R':1.}
        if policy in ('no_graph','profile_only'):dims['M']=dims['E']=0.
        if policy in ('no_mechanism','profile_only'):dims['M']=0.
        if policy in ('no_transfer','profile_only'):dims['T']=0.
        score=100*sum(dims.values())/6 if policy!='profile_only' else 100*dims['D']
        rows.append({'model':model,'eligible':bool(eligible),'score':score,'dimensions':dims,
                     'rule_ids':[r['rule_id'] for r in supporting],'evidence_ids':sorted({e for r in supporting for e in r['evidence_ids']}),
                     'admitted':bool(eligible and score>=admission.threshold)})
    return sorted(rows,key=lambda r:(-r['score'],MODELS.index(r['model'])))
