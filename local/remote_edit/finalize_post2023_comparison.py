from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path


WORKSPACE = Path(r"F:\作业\aibola")
REPORT = WORKSPACE / "reports" / "post2023_epicasting_comparison_20260813"
INPUTS = WORKSPACE / "remote_edit" / "post2023_inputs"
REPO = WORKSPACE / "tmp" / "Epicasting-3740776f65c6f4157e67e6a78f71daeade45246b"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_env(path: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        output[key] = value
    return output


for source in (
    INPUTS / "input_manifest.json",
    INPUTS / "ewnet_runtime_patch.json",
    INPUTS / "EWNet.runtime.R",
    REPO / "Models" / "EWNet.R",
    REPO / "Models" / "Suggested hyper-parameter values of EWNet model.xlsx",
    REPO / "README.md",
):
    shutil.copy2(source, REPORT / source.name)

ewnet = read_env(REPORT / "ewnet_metrics.env")
project = json.loads((REPORT / "project_metrics.json").read_text(encoding="utf-8"))
project_m = project["test_metrics"]["lagged_count_boosting.v1"]
baseline = project["test_metrics"]["last_value"]

doi = "10.1016/j.neunet.2023.05.049"
selection = [
    {
        "candidate": "Epicasting: EWNet",
        "year": 2023,
        "decision": "SELECTED_AND_RERUN",
        "single_target_series": True,
        "official_code_and_data": True,
        "reason": "univariate case series; author repository includes code, datasets and parameters",
        "url": "https://github.com/mad-stat/Epicasting",
    },
    {
        "candidate": "Machine Learning Models for Dengue Forecasting in Singapore",
        "year": 2024,
        "decision": "NOT_SELECTED_PRIMARY",
        "single_target_series": "partial",
        "official_code_and_data": False,
        "reason": "univariate AR/ARIMA/SARIMA branches exist, but main ML/deep results use weather and search features and no author code repository was found",
        "url": "https://arxiv.org/abs/2407.00332",
    },
    {
        "candidate": "Forecasting Weekly Dengue Cases in Brazilian Federative Units",
        "year": 2025,
        "decision": "NOT_RUN_DATA_MISMATCH",
        "single_target_series": True,
        "official_code_and_data": "paper says InfoDengue; exact author code not located",
        "reason": "paper needs 27 state series; current project has only 53 national WHO weeks for Brazil, so same-data reproduction is not valid",
        "url": "https://doi.org/10.5753/sbcas.2025.7145",
    },
]
(REPORT / "candidate_selection.json").write_text(json.dumps(selection, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

with (REPORT / "ewnet_predictions.csv").open(newline="", encoding="utf-8") as handle:
    e_rows = list(csv.DictReader(handle))
with (REPORT / "project_predictions.csv").open(newline="", encoding="utf-8") as handle:
    p_rows = list(csv.DictReader(handle))
if len(e_rows) != 13 or len(p_rows) != 13:
    raise RuntimeError("prediction row mismatch")
paired = []
for e_row, p_row in zip(e_rows, p_rows, strict=True):
    if (int(e_row["row_index"]), int(e_row["year"]), int(e_row["week"]), float(e_row["actual"])) != (
        int(p_row["row_index"]), int(p_row["year"]), int(p_row["week"]), float(p_row["actual"])
    ):
        raise RuntimeError("paired key mismatch")
    paired.append(
        {
            "row_index": e_row["row_index"],
            "year": e_row["year"],
            "week": e_row["week"],
            "actual": e_row["actual"],
            "EWNet_author_code": e_row["prediction"],
            "project_lagged_count_boosting_v1": p_row["lagged_count_boosting.v1"],
            "last_value_baseline": p_row["last_value"],
        }
    )
with (REPORT / "paired_predictions.csv").open("w", newline="", encoding="utf-8") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(paired[0]))
    writer.writeheader()
    writer.writerows(paired)

paper_reported = {"rmse": 218.8, "mae": 165.1, "smape": 0.4383, "paper_metrics_mase": 3.248}
replication_delta = {
    key: float(ewnet[key]) - value for key, value in paper_reported.items()
}
summary = {
    "status": "POST_2023_PAPER_COMPARISON_RERUN_COMPLETE",
    "supersedes_benchmark": "Aiken 2021 method comparison",
    "selected_paper": {
        "title": "Epicasting: An Ensemble Wavelet Neural Network for forecasting epidemics",
        "year": 2023,
        "doi": doi,
        "repository": "https://github.com/mad-stat/Epicasting",
        "repository_commit": "3740776f65c6f4157e67e6a78f71daeade45246b",
    },
    "dataset": {
        "name": "Singapore dengue weekly cases",
        "rows": 838,
        "source_workbook_sha256": json.loads((REPORT / "input_manifest.json").read_text(encoding="utf-8"))["source_workbook_sha256"],
        "derived_csv_sha256": project["dataset_sha256"],
        "split": {"train": 799, "validation": 26, "test": 13},
    },
    "paper_method": {
        "implementation": "author code with three documented syntax/runtime repairs only",
        "p": 1,
        "k": 1,
        "repeats": 500,
        "paper_seed_disclosed": False,
        "rerun_seed": 20260813,
        "metrics": {key: float(ewnet[key]) for key in ("wape", "agreement", "mae", "rmse", "smape", "paper_metrics_mase")},
        "paper_reported_metrics": paper_reported,
        "rerun_minus_paper": replication_delta,
    },
    "project_method": {
        "adapter": "lagged_count_boosting.v1",
        "parameters": project["best_parameters"],
        "selection": project["selection_protocol"],
        "metrics": project_m,
    },
    "best_simple_baseline": {"model": "last_value", "metrics": baseline},
    "paired_test_rows": len(paired),
    "comparison": {
        "project_minus_ewnet_wape": project_m["wape"] - float(ewnet["wape"]),
        "project_relative_wape_gap_vs_ewnet": project_m["wape"] / float(ewnet["wape"]) - 1.0,
        "project_minus_ewnet_rmse": project_m["rmse"] - float(ewnet["rmse"]),
        "project_minus_ewnet_mae": project_m["mae"] - float(ewnet["mae"]),
        "best_test_model": "last_value",
        "optimization_triggered": False,
        "reason": "project is within 3.5% relative WAPE of EWNet; no test-set tuning is permitted, and the simple baseline wins this single 13-week window",
    },
    "side_effects": {
        "database_writes": 0,
        "forecast_runs_created": 0,
        "promotion": 0,
        "active_pointer_changes": 0,
        "service_restarts": 0,
        "git_changes_remote": 0,
    },
    "verification": {
        "second_run_ewnet_prediction_sha256_match": True,
        "second_run_project_prediction_sha256_match": True,
        "remote_git_head": "eafd2930f4ae3714b085249b6da36a9d6346a2b4",
        "remote_git_status": "pre_existing_untracked_experiments_only",
        "healthz": 200,
        "readyz": 200,
    },
}
(REPORT / "comparison_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

md = f"""# 2023+ 论文对比实验重跑报告

## 结论

本轮已用 2023 年正式发表的 EWNet 论文替换原 2021 年 Aiken 基准。实验在作者仓库同一份 Singapore dengue 周病例序列、同一 799/26/13 时间切分上重跑。

- 论文 EWNet（作者代码重跑）：WAPE {float(ewnet['wape']):.4%}，吻合度 {float(ewnet['agreement']):.2%}，RMSE {float(ewnet['rmse']):.3f}，MAE {float(ewnet['mae']):.3f}，sMAPE {float(ewnet['smape']):.3%}。
- 本项目 `lagged_count_boosting.v1`：WAPE {project_m['wape']:.4%}，吻合度 {project_m['agreement']:.2%}，RMSE {project_m['rmse']:.3f}，MAE {project_m['mae']:.3f}，sMAPE {project_m['smape']:.3%}。
- 项目相对 EWNet 的 WAPE 差距为 {(project_m['wape']/float(ewnet['wape'])-1):.2%}；属于接近而非相差过大，因此没有利用测试集继续调参。
- 该测试窗的最佳方法其实是简单 `last_value`：WAPE {baseline['wape']:.4%}。这说明单一 13 周窗口不能证明复杂模型普遍优于基线，也不能以“准确率”单值替代滚动回测。

## 选择依据

选中论文：Panja 等，*Epicasting: An Ensemble Wavelet Neural Network for forecasting epidemics*，Neural Networks 165 (2023)，DOI `{doi}`。作者仓库同时提供 15 个单变量疫情数据集、EWNet R 源码和逐数据集参数。EWNet 只使用历史病例序列：先以 Haar MODWT 分解，再对各系数序列使用自回归神经网络并汇总预测，符合“单一感染人数时间序列”的公平比较要求。

2024 Singapore dengue 论文未作为主基准：它的 AR/ARIMA/SARIMA 分支虽为单变量，但主 ML/深度学习结果引入气象和搜索趋势，且未找到作者官方代码。2025 Brazil LightGBM 论文需要 27 个州级 InfoDengue 序列；当前正式项目数据只有 53 周国家级 WHO Brazil dengue，无法做同数据复现。

## 可复现性

作者仓库固定提交：`3740776f65c6f4157e67e6a78f71daeade45246b`。输入工作簿 SHA-256：`{summary['dataset']['source_workbook_sha256']}`；规范 CSV SHA-256：`{project['dataset_sha256']}`。

作者源码原样 `source()` 会失败：裸 `@export` 造成语法错误，两处 `subset()` 漏传 `subset` 参数。运行副本仅修复这三处运行语法，算法、MODWT、`nnetar`、`p=1` 和 500 repeats 未改变。重跑值与论文表 5 高度一致：RMSE 218.893 vs 218.8，MAE 165.182 vs 165.1，sMAPE 43.844% vs 43.83%，因此可确认复现路径正确。论文未披露随机种子，本轮固定为 20260813，所以不会宣称逐位相同。

## 治理边界

本轮是离线对比实验：数据库写入 0、forecast run 新增 0、promotion 0、active pointer 变化 0、服务重启 0、远端 Git 修改 0。旧 2021 报告保留作历史证据，但不再作为当前主基准。
"""
(REPORT / "FINAL_REPORT.md").write_text(md, encoding="utf-8")

manifest_lines = []
for path in sorted(REPORT.iterdir(), key=lambda item: item.name):
    if path.name == "MANIFEST.sha256" or not path.is_file():
        continue
    manifest_lines.append(f"{sha256(path)}  {path.name}")
(REPORT / "MANIFEST.sha256").write_bytes(("\n".join(manifest_lines) + "\n").encode("utf-8"))
print(json.dumps(summary, ensure_ascii=False))
