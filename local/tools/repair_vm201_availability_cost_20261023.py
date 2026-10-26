"""Versioned cost-accounting repair; original predictions and records retained."""
from pathlib import Path
import json,time
import numpy as np
import run_vm201_completion_20261023 as b


def main():
    b.save('availability_cost_amendment.json',{'registered_at':b.now().isoformat(),'reason':'Original fit_calls counted final accepted adapters, not all attempted and complete-reference evaluations; repair cost counters without changing masks, forecasts, rejection rules or outcomes',
        'original_results_retained':True,'data_or_model_selection_changed':False,'scope':'availability-cost-v2'})
    data=b.arrays();before=json.loads((b.OUT/'availability_traces.json').read_text());after=[]
    for r in before:
        weeks,y=data[r['disease']];origin=r['origin'];replicate=r['replicate'];available=r['available_pct'];threshold=r['threshold_pct']
        rng=np.random.default_rng(b.SEED+b.DISEASES.index(r['disease'])*100000+origin*10+replicate);permutation=rng.permutation(100);masked=y[:origin].copy();masked[origin-100+permutation[:100-available]]=np.nan
        tick=time.perf_counter();attempts=0;outputs=0;pred=[];models=[]
        if available>=threshold:
            for model in ('last_value','mean4','seasonal52','log_ar2'):
                attempts+=1;p=b.missing_forecast(masked,model)
                if p is not None:pred.append(p);models.append(model);outputs+=1
        if not np.isfinite(masked[-1]):pred=[];models=[]
        accepted=bool(pred);assert accepted==r['accepted'] and models==r['models']
        reference=[b.small_forecast(y[:origin],m,4) for m in models]
        if accepted:
            point=np.mean(pred,axis=0);actual=y[origin:origin+4]
            assert abs(float(np.abs(actual-point).sum())-r['absolute_error_sum'])<1e-9
        updated=dict(r);updated.pop('fit_calls');updated.update(adapter_attempts=attempts,masked_adapter_outputs=outputs,reference_adapter_evaluations=len(reference),
            successful_adapter_evaluations=outputs+len(reference),seconds=time.perf_counter()-tick,cost_version='v2')
        after.append(updated)
    b.save('availability_traces_v2.json',after);summary=[]
    for r in json.loads((b.OUT/'availability_summary.json').read_text()):
        group=[v for v in after if (v['disease'],v['available_pct'],v['threshold_pct'])==(r['disease'],r['available_pct'],r['threshold_pct'])]
        updated=dict(r);updated.pop('fit_calls');updated.update({k:sum(v[k] for v in group) for k in ('adapter_attempts','masked_adapter_outputs','reference_adapter_evaluations','successful_adapter_evaluations','seconds')});summary.append(updated)
    b.save('availability_summary_v2.json',summary)
    b.save('availability_cost_repair_verified.json',{'rows':len(after),'all_original_acceptance_and_predictions_preserved':True,'original_cost_counters_superseded':True})

if __name__=='__main__':main()
