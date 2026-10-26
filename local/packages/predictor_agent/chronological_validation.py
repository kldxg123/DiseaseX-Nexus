"""Deterministic, training-only forecasts and explicit probabilistic scoring."""
from __future__ import annotations
from typing import Sequence
import numpy as np

MODELS = ('last_value', 'mean4', 'seasonal52', 'log_ar2')
QUANTILES = np.array([.025,.10,.25,.50,.75,.90,.975])

def forecast(history: Sequence[float], model: str, horizon: int) -> np.ndarray:
    y=np.asarray(history,dtype=float)
    if y.ndim!=1 or len(y)<8 or not np.isfinite(y).all() or (y<0).any():
        raise ValueError('finite nonnegative observations required; missing is not zero')
    if horizon not in (1,2,3,4): raise ValueError('supported horizons are 1..4')
    if model not in MODELS: raise ValueError('unregistered model')
    if model=='seasonal52' and len(y)<104: raise ValueError('seasonal history insufficient')
    if model=='log_ar2':
        z=np.log1p(y); x=np.column_stack([np.ones(len(z)-2),z[1:-1],z[:-2]])
        coef=np.linalg.lstsq(x,z[2:],rcond=None)[0]; hist=list(z); out=[]
        for _ in range(horizon):
            val=float(np.clip(np.dot([1,hist[-1],hist[-2]],coef),0,np.log1p(max(y.max()*10,10))))
            hist.append(val);out.append(float(np.expm1(val)))
        return np.asarray(out)
    if model=='last_value': return np.repeat(y[-1],horizon)
    if model=='mean4': return np.repeat(y[-4:].mean(),horizon)
    return np.asarray([y[-52+i] for i in range(horizon)])

def wape(actual, predicted):
    y=np.asarray(actual,dtype=float);p=np.asarray(predicted,dtype=float)
    if y.shape!=p.shape or not np.isfinite(y).all() or not np.isfinite(p).all():raise ValueError('aligned finite arrays required')
    denominator=np.abs(y).sum()
    return None if denominator==0 else float(np.abs(y-p).sum()/denominator)

def rolling_select(history: Sequence[float], validation_origins: int=13) -> str:
    """Select only on outcomes already observable before the current origin."""
    y=np.asarray(history,dtype=float)
    if len(y)<104+validation_origins+4: return 'last_value'
    errors={m:[] for m in MODELS}
    for origin in range(len(y)-validation_origins-3,len(y)-3):
        actual=y[origin:origin+4]
        for model in MODELS:
            errors[model].extend(abs(actual-forecast(y[:origin],model,4)))
    return min(MODELS,key=lambda m:sum(errors[m]))

def quantile_forecast(point: float, calibration_errors: Sequence[float]):
    residual=np.asarray(calibration_errors,dtype=float)
    if len(residual)<26 or not np.isfinite(residual).all():raise ValueError('26 calibration errors required')
    return np.maximum(0,point+np.quantile(residual,QUANTILES))

def score_quantiles(actual: float, q):
    q=np.asarray(q,dtype=float)
    if q.shape!=(7,) or not np.isfinite(q).all() or (np.diff(q)<0).any():raise ValueError('ordered seven quantiles required')
    scores=[]
    for alpha,lo,hi in [(.5,q[2],q[4]),(.2,q[1],q[5]),(.05,q[0],q[6])]:
        scores.append(alpha/2*(hi-lo+2/alpha*max(lo-actual,0)+2/alpha*max(actual-hi,0)))
    return {'wis':float((.5*abs(actual-q[3])+sum(scores))/3.5),
            'coverage95':float(q[0]<=actual<=q[-1]),'width95':float(q[-1]-q[0])}
