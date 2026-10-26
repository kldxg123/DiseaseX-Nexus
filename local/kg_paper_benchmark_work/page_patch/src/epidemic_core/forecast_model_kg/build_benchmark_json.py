# ruff: noqa: E501  # long lines hold verbatim paper excerpts / hashes / Chinese UI strings
"""Build the page-facing benchmark JSON (kg.paper_benchmark.v2) from the
experiment outputs. Deterministic; reads only out/ + evidence artifacts.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

WORK = Path(__file__).resolve().parents[4]
OUT = WORK / "out"
REF = Path(__file__).resolve().parent / "reference"
REPORT = Path("F:/作业/aibola/reports/top_venue_paper_benchmark_20260819")


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> None:
    metrics = json.loads((OUT / "metrics_overall.json").read_text(encoding="utf-8"))
    manifest = json.loads((OUT / "parameter_and_seed_manifest.json").read_text(encoding="utf-8"))
    sentinel = json.loads((OUT / "leakage_and_sentinel_results.json").read_text(encoding="utf-8"))
    spans = json.loads((WORK / "evidence" / "kg_evidence_spans.json").read_text(encoding="utf-8"))

    def _norm(d: dict[str, float]) -> dict[str, float]:
        """Normalize to the JS-expected lowercase keys (wape/mae/rmse/coverage/mean_width)."""
        return {
            "n": d["n"],
            "mae": d["MAE"],
            "mse": d["MSE"],
            "rmse": d["RMSE"],
            "wape": d["WAPE"],
            "agreement_pct": d["agreement_pct"],
            "smape": d["sMAPE"],
            "mase": d["MASE"],
            "coverage": d["coverage_95"],
            "mean_width": d["mean_interval_width"],
        }

    by_model: dict[str, dict[str, dict[str, float]]] = {}
    for model, splits in metrics["metrics"]["test"].items():
        by_model[model] = {"test": _norm(splits["all"])}
    for model, splits in metrics["metrics"]["validation"].items():
        by_model.setdefault(model, {})["validation"] = _norm(splits["all"])

    by_horizon_wape: dict[str, dict[str, float]] = {}
    for model, splits in metrics["metrics"]["test"].items():
        by_horizon_wape[model] = {
            h: splits[h]["WAPE"] for h in ("1", "2", "3", "4") if h in splits
        }

    with (OUT / "ensemble_grid.csv").open(encoding="utf-8") as fh:
        ens = [r for r in csv.DictReader(fh) if r.get("selected") == "1.0"][0]
    qwen_sel = manifest["models"]["project_qwen_tuned"]["selected_params"]

    payload = {
        "schema_version": "kg.paper_benchmark.v2",
        "benchmark_id": "rsos_mpox_2024_method_transfer_sgp_dengue.v1",
        "is_primary_benchmark": True,
        "replication_type": "METHOD_TRANSFER_REPLICATION",
        "evaluation_identity": "RETROSPECTIVE_METHOD_TRANSFER_BENCHMARK_SHADOW_ONLY",
        "is_fresh_heldout": False,
        "paper": {
            "title": spans["work"]["title"],
            "authors": "Bleichrodt, Luo, Kirpich & Chowell",
            "year": 2024,
            "venue": spans["work"]["venue"],
            "volume": spans["work"]["volume"],
            "issue": spans["work"]["issue"],
            "article_number": spans["work"]["article_number"],
            "doi": spans["work"]["doi"],
            "publisher_url": spans["work"]["publisher_url"],
            "rank_label": "SCI JCR Q1 (2023) / Q2 (2024)，Multidisciplinary Sciences；非 CCF 会议",
            "pdf_identity": "正式发表版（VOR，Europe PMC PMC11285753 渲染，CC-BY 4.0）",
            "pdf_sha256": "589764fb45f9e40cb5cbbe5d7f554decdbb122f7960a94a33fce4dba468bfd7e",
        },
        "task": {
            "paper_disease": "mpox（论文原始对象，2022–2023，CDC/OWID 数据）",
            "evaluation_disease": "dengue（新加坡，2012-W01 至 2019-W48，方法迁移评估序列）",
            "metric": "cases_new",
            "frequency": "week",
            "horizons": "1-4 周 rolling-origin",
            "note_zh": "论文原始 mpox 实验与本新加坡 dengue 方法迁移是两个不同数据集上的不同实验，不得混为同一数据集。",
        },
        "dataset": {
            "description": "新加坡卫生部 data.gov.sg 周度登革热确诊病例（官方目录重建序列）",
            "range": "2012-W01 至 2019-W48",
            "rows": 412,
            "sha256": "5efbff843186392c86862361a47c9fb15f30090d0a379b46f9e44267060afac2",
        },
        "split": {
            "train_labels": 311,
            "validation_origins": 50,
            "test_origins": 50,
            "validation_pairs": 200,
            "test_pairs": 194,
            "note_zh": "与历史 benchmark 相同的 311/50/50 标签切分；horizon 1-4；测试配对 h=1..4 为 50/49/48/47",
        },
        "methods": {
            "paper_arima": {"role_zh": "论文 baseline（ARIMA，11 周校准窗）", "description_zh": "AICc 网格选阶，负值截 0"},
            "paper_gam": {"role_zh": "论文方法（GAM 时间样条）", "description_zh": "B 样条 + GCV 选 df"},
            "paper_slr": {"role_zh": "论文方法（时间线性回归）", "description_zh": "y~t OLS"},
            "paper_subepidemic_top": {"role_zh": "论文旗舰方法构造块（sub-epidemic top）", "description_zh": "GGM/GLM 非线性最小二乘 + AICc"},
            "paper_subepidemic_second": {"role_zh": "论文方法（AICc 第二名）", "description_zh": "含 Cthr 双波 GLM 候选"},
            "paper_subepidemic_ensemble_weighted": {"role_zh": "论文加权集成", "description_zh": "AICc 权重 wi∝exp(-Δi/2)"},
            "paper_subepidemic_ensemble_unweighted": {"role_zh": "论文不加权集成", "description_zh": "AICc 前 4 名等权"},
            "paper_prophet": {"role_zh": "论文方法（Prophet）", "description_zh": "prophet 1.4.0，11 周校准窗"},
            "project_kg_primary": {"role_zh": "项目 KG 推荐主模型", "description_zh": "lagged_count_boosting.v1 (50/0.1/10)"},
            "project_qwen_tuned": {"role_zh": "项目 Qwen 受控调优", "description_zh": f"白名单网格验证集选出 {qwen_sel}"},
            "project_kg_ensemble": {"role_zh": "项目组合模型", "description_zh": "验证集 0.05 单纯形选权"},
            "last_value": {"role_zh": "基线", "description_zh": "上周值"},
            "seasonal_naive": {"role_zh": "基线", "description_zh": "去年同期（52 周）"},
            "rolling_mean_4": {"role_zh": "基线", "description_zh": "近 4 周均值"},
        },
        "presentation_order": [
            "paper_arima", "paper_gam", "paper_slr", "paper_prophet",
            "paper_subepidemic_top", "paper_subepidemic_second",
            "paper_subepidemic_ensemble_weighted", "paper_subepidemic_ensemble_unweighted",
            "project_kg_primary", "project_qwen_tuned", "project_kg_ensemble",
            "last_value", "seasonal_naive", "rolling_mean_4",
        ],
        "selection": {
            "best_overall_model": "paper_arima",
            "best_project_model": "project_kg_ensemble",
            "project_beats_paper_arima": False,
            "project_beats_last_value": False,
            "project_advantage_note_zh": "项目方法的主要优势是更高的 95% 区间覆盖率，但必须结合平均区间宽度解读（见对照表宽度列）。",
            "best_test_model": min(by_model, key=lambda m: by_model[m]["test"]["wape"]),
            "best_baseline": metrics["best_baseline_by_test_wape"],
            "qwen_params": qwen_sel,
            "ensemble_weights": {
                "project_qwen_tuned": float(ens["w_project_qwen_tuned"]),
                "seasonal_naive": float(ens["w_seasonal_naive"]),
                "last_value": float(ens["w_last_value"]),
            },
            "note_zh": "全部选择仅使用验证集 200 个配对（WAPE→RMSE→字典序）；测试集冻结后恰好评估一次。",
            "test_evaluated_count": metrics["exactly_once_ledger"]["test_evaluated_count"],
        },
        "metrics": {
            "by_model_overall": by_model,
            "by_horizon_wape": by_horizon_wape,
            "skill_vs_paper_arima_test": metrics["skill_vs_paper_arima_test"],
            "relative_wape_improvement_vs_best_baseline_pct_test": metrics[
                "relative_wape_improvement_vs_best_baseline_pct_test"
            ],
        },
        "paired_proof": {
            "test_pairs": 194,
            "validation_pairs": 200,
            "predictions_csv_sha256": sha256_file(OUT / "rolling_origin_predictions.csv"),
            "paired_csv_sha256": sha256_file(OUT / "paired_comparison.csv"),
            "metrics_csv_sha256": sha256_file(OUT / "metrics_by_model_and_horizon.csv"),
            "determinism_identical": sentinel["determinism"]["identical"],
            "future_sentinel_pass": all(
                v for per in sentinel["future_sentinel"]["per_model_origin_pass"].values() for v in per.values()
            ),
        },
        "evidence": {
            "pdf_sha256": "589764fb45f9e40cb5cbbe5d7f554decdbb122f7960a94a33fce4dba468bfd7e",
            "spans_sha256": sha256_file(WORK / "evidence" / "kg_evidence_spans.json"),
            "n_spans": len(spans["spans"]),
        },
        "governance_note_zh": "本区块为正式同行评审论文（RSOS 2024, JCR Q1/Q2）的方法迁移复现：同数据、同切分、同 horizon、同配对样本。SHADOW_ONLY：不发布、不晋级、不修改 Trusted 数据与 Active Pointer。本测试集已在评估中消费，结果仅属历史对比（METHOD_TRANSFER_REPLICATION），不得称为 Fresh held-out。",
        "incomparability_factors": [
            "疾病不同：论文为 mpox 单次暴发，本实验为新加坡登革热多年季节序列",
            "周定义不同：论文周四-周三，本数据 ISO 周一-周日",
            "论文 11 周校准窗在多年季节序列上偏短，对论文方法不利",
        ],
        "implementation_gaps": [
            "ARIMA 选阶为确定性网格+AICc；与 pmdarima auto.arima 原生选阶一致率 9/100（KPSS 定 d 与 AICc 口径差异，已归因，见 autoarima_equivalence.json）",
            "GAM 为 OLS B 样条+GCV，非 mgcv 原生（pygam 与 scipy 版本冲突，已卸载恢复，见 gam_equivalence.json）",
            "sub-epidemic 为确定性 Python 实现，与官方 MATLAB 管线存在候选集/边界/优化器差异（Track 1 已量化归因）",
            "bootstrap 区间为 N=100 残差 bootstrap+观测噪声项，替代论文 300 次参数 bootstrap",
        ],
        "fidelity_note_zh": "Track 1（论文官方 mpox 数据+官方预测产物）忠实度验证：SLR 逐点完全一致、论文报告数字精确复现；ARIMA 与 sub-epidemic 存在已归因的方法学差异；另发现官方 MATLAB 产物中 US(OWID)/US(CDC) 序列互换（D6）。",
        "publication_state": "SHADOW_ONLY_NOT_PROMOTED",
    }
    REF.mkdir(parents=True, exist_ok=True)
    out_path = REF / "mpox_rsos2024_paper_benchmark.v1.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"written": str(out_path), "sha256": sha256_file(out_path),
                      "best_test_model": min(by_model, key=lambda m: by_model[m]["test"]["wape"])}))


if __name__ == "__main__":
    main()
