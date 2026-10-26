from __future__ import annotations

import csv
import itertools
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from epidemic_core.kg_qwen.adapters_ext import fit_lagged_count_boosting, predict_lagged_count_boosting
from epidemic_core.kg_r3c.execution import TrainingRow


def score(actual: list[float], prediction: list[float]) -> dict[str, float]:
    ae = [abs(a - p) for a, p in zip(actual, prediction, strict=True)]
    wape = sum(ae) / sum(abs(a) for a in actual)
    return {
        "wape": wape,
        "agreement": 1.0 - wape,
        "mae": sum(ae) / len(ae),
        "rmse": math.sqrt(sum((a - p) ** 2 for a, p in zip(actual, prediction, strict=True)) / len(actual)),
        "smape": sum(0 if abs(a)+abs(p)==0 else 2*abs(a-p)/(abs(a)+abs(p)) for a,p in zip(actual,prediction,strict=True))/len(actual),
    }


def fit_ar2(train: list[float]) -> tuple[float, float, float]:
    y = np.asarray(train[2:], dtype=float)
    x = np.column_stack([np.ones(len(y)), np.asarray(train[1:-1]), np.asarray(train[:-2])])
    beta = np.linalg.lstsq(x, y, rcond=None)[0]
    return tuple(float(v) for v in beta)


def rolling_ar2(train: list[float], actual: list[float]) -> tuple[list[float], list[tuple[float,float,float]]]:
    history = list(train)
    out, coefficients = [], []
    for value in actual:
        b0, b1, b2 = fit_ar2(history)
        out.append(max(b0 + b1*history[-1] + b2*history[-2], 0.0))
        coefficients.append((b0,b1,b2))
        history.append(value)
    return out, coefficients


def rolling_fixed_ar2(
    train: list[float], actual: list[float], coefficients: tuple[float, float, float]
) -> list[float]:
    history = list(train)
    b0, b1, b2 = coefficients
    output = []
    for value in actual:
        output.append(max(b0 + b1 * history[-1] + b2 * history[-2], 0.0))
        history.append(value)
    return output


def rows(values: list[float]) -> list[TrainingRow]:
    start = date(2012,1,2)
    return [TrainingRow(start+timedelta(weeks=i),None,v,f"official-{i+1}") for i,v in enumerate(values)]


def project_forecast(train: list[float], params: dict[str,float|int], h: int) -> list[float]:
    r = rows(train)
    fit = fit_lagged_count_boosting(r,params,20260813,r[-1].period_start)
    return [float(x["yhat"]) for x in predict_lagged_count_boosting(fit,list(range(1,h+1)),r[-1].period_start)]


def rolling_project(train: list[float], actual: list[float], params: dict[str,float|int]) -> list[float]:
    history=list(train); output=[]
    for value in actual:
        output.append(project_forecast(history,params,1)[0])
        history.append(value)
    return output


def rolling_last_value(train: list[float], actual: list[float]) -> list[float]:
    history=list(train); output=[]
    for value in actual:
        output.append(history[-1]); history.append(value)
    return output


def blend_predictions(
    ar2: list[float], project: list[float], last: list[float], weights: tuple[float, float, float]
) -> list[float]:
    wa, wp, wl = weights
    return [wa * a + wp * p + wl * l for a, p, l in zip(ar2, project, last, strict=True)]


def main() -> None:
    source, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    with source.open(newline="",encoding="utf-8") as handle:
        source_rows=list(csv.DictReader(handle))
    usable=[row for row in source_rows if row["cases"] not in {"",None}]
    values=[float(row["cases"]) for row in usable]
    if len(values) != 412:
        raise RuntimeError(f"paper-aligned official series must contain 412 points, got {len(values)}")
    test_n=50; val_n=50
    # The paper reports 412 source points and a 311/50/50 label split. The first
    # point is a lag warm-up observation, leaving exactly 411 evaluated labels.
    train=values[:312]; val=values[312:362]; train_val=values[:362]; test=values[362:412]

    published_coefficients=(8.841,1.024,-0.062)
    ar_val=rolling_fixed_ar2(train,val,published_coefficients)
    ar_test=rolling_fixed_ar2(train_val,test,published_coefficients)
    fitted_coefficients=fit_ar2(train)
    fitted_ar_val=rolling_fixed_ar2(train,val,fitted_coefficients)
    grid=[]
    for ne,lr,leaf in itertools.product((30,50,80,100),(0.03,0.05,0.1,0.2),(5,10,15,20)):
        params={"n_estimators":ne,"learning_rate":lr,"min_samples_leaf":leaf}
        m=score(val,rolling_project(train,val,params))
        grid.append({**params,**m})
    grid.sort(key=lambda x:(x["wape"],x["rmse"],x["n_estimators"],x["learning_rate"],x["min_samples_leaf"]))
    best={key:grid[0][key] for key in ("n_estimators","learning_rate","min_samples_leaf")}
    p_val=rolling_project(train,val,best)
    p_test=rolling_project(train_val,test,best)
    last_val=rolling_last_value(train,val)
    last=rolling_last_value(train_val,test)

    # The project-level orchestration optimization is selected on validation data only.
    # It does not inspect the test labels: a deterministic 0.05 simplex grid combines
    # the paper-aligned AR(2), the existing project adapter, and last-value persistence.
    ensemble_grid=[]
    for ar_step in range(21):
        for project_step in range(21-ar_step):
            last_step=20-ar_step-project_step
            weights=(ar_step/20,project_step/20,last_step/20)
            metrics=score(val,blend_predictions(ar_val,p_val,last_val,weights))
            ensemble_grid.append({"ar2_weight":weights[0],"project_weight":weights[1],"last_value_weight":weights[2],**metrics})
    ensemble_grid.sort(key=lambda x:(x["wape"],x["rmse"],-x["project_weight"],-x["ar2_weight"]))
    ensemble_weights=tuple(ensemble_grid[0][key] for key in ("ar2_weight","project_weight","last_value_weight"))
    ensemble_test=blend_predictions(ar_test,p_test,last,ensemble_weights)
    result={
        "status":"POST_2023_2024_PAPER_METHOD_COMPARISON_COMPLETE",
        "paper":{"title":"Machine Learning Models for Dengue Forecasting in Singapore","year":2024,"url":"https://arxiv.org/abs/2407.00332"},
        "scope":"paper-period and paper-length aligned 2012-2019 official Singapore weekly dengue series; official catalogue reconstruction, not a byte-identical author dataset artifact",
        "split":{"source_points":len(values),"lag_warmup_points":1,"training_labels":311,"training_history_points":len(train),"validation":len(val),"test":len(test)},
        "ar2_method":{"description":"paper's univariate two-lag autoregression equation with observed prior-week values in one-step evaluation","published_coefficients":{"intercept":published_coefficients[0],"lag1":published_coefficients[1],"lag2":published_coefficients[2]},"paper_reported_validation_rmse":14.654,"reproduced_validation_metrics":score(val,ar_val),"reproduced_test_metrics":score(test,ar_test),"locally_refitted_training_coefficients":{"intercept":fitted_coefficients[0],"lag1":fitted_coefficients[1],"lag2":fitted_coefficients[2]},"locally_refitted_validation_metrics":score(val,fitted_ar_val)},
        "project_method":{"adapter":"lagged_count_boosting.v1","protocol":"same rolling one-step update as comparator; each target is revealed only after its prediction","parameters":best,"validation_metrics":{k:grid[0][k] for k in ("wape","agreement","mae","rmse","smape")},"test_metrics":score(test,p_test)},
        "project_optimized_ensemble":{"selection":"weights chosen only on validation WAPE over a deterministic 0.05 simplex grid; test labels unopened until final evaluation","components":["paper_ar2","lagged_count_boosting.v1","last_value"],"weights":{"paper_ar2":ensemble_weights[0],"lagged_count_boosting.v1":ensemble_weights[1],"last_value":ensemble_weights[2]},"validation_metrics":{k:ensemble_grid[0][k] for k in ("wape","agreement","mae","rmse","smape")},"test_metrics":score(test,ensemble_test)},
        "last_value_baseline":{"test_metrics":score(test,last)},
        "comparison":{"raw_project_relative_wape_vs_ar2":score(test,p_test)["wape"]/score(test,ar_test)["wape"]-1,"optimized_project_relative_wape_vs_ar2":score(test,ensemble_test)["wape"]/score(test,ar_test)["wape"]-1,"best_test_method":min({"ar2":score(test,ar_test)["wape"],"project_raw":score(test,p_test)["wape"],"project_optimized":score(test,ensemble_test)["wape"],"last_value":score(test,last)["wape"]},key={"ar2":score(test,ar_test)["wape"],"project_raw":score(test,p_test)["wape"],"project_optimized":score(test,ensemble_test)["wape"],"last_value":score(test,last)["wape"]}.get)},
    }
    (out/"comparison_2024_method.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    with (out/"paired_predictions_2024_official_overlap.csv").open("w",newline="",encoding="utf-8") as handle:
        w=csv.DictWriter(handle,fieldnames=["year","week","actual","ar2_paper_method","project_lagged_count_boosting_v1","project_optimized_ensemble","last_value"]); w.writeheader()
        for row,a,x,y,e,z in zip(usable[-test_n:],test,ar_test,p_test,ensemble_test,last,strict=True):
            w.writerow({"year":row["year"],"week":row["week"],"actual":a,"ar2_paper_method":x,"project_lagged_count_boosting_v1":y,"project_optimized_ensemble":e,"last_value":z})
    with (out/"project_grid_2024_official_overlap.csv").open("w",newline="",encoding="utf-8") as handle:
        w=csv.DictWriter(handle,fieldnames=list(grid[0]));w.writeheader();w.writerows(grid)
    with (out/"ensemble_grid_2024_official_overlap.csv").open("w",newline="",encoding="utf-8") as handle:
        w=csv.DictWriter(handle,fieldnames=list(ensemble_grid[0]));w.writeheader();w.writerows(ensemble_grid)
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__": main()
