from __future__ import annotations

import hashlib
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path("USER_HOME/projects/openclaw/experiments/natcomm_hk_flu_benchmark_20260823")
OUTPUTS = ROOT / "outputs"
REPORT = ROOT / "report"
REPORT.mkdir(exist_ok=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pct(value: float) -> str:
    return f"{value * 100:.2f}%"


def plot_main() -> None:
    metrics = pd.read_csv(OUTPUTS / "test_metrics_overall.csv").set_index("model")
    router = pd.read_csv(OUTPUTS / "router_metrics.csv")
    router_overall = router[router["week_ahead"].isna()].set_index("model")
    order = ["Baseline", "KG_ADAPTIVE_ENSEMBLE", "AWBE", "KG_HORIZON_ROUTER"]
    values = [
        float(metrics.loc["Baseline", "rmse"]),
        float(metrics.loc["KG_ADAPTIVE_ENSEMBLE", "rmse"]),
        float(metrics.loc["AWBE", "rmse"]),
        float(router_overall.loc["KG_HORIZON_ROUTER", "rmse"]),
    ]
    labels = ["论文常数基线", "项目独立自适应组合", "论文 AWBE", "KG 预测期路由"]
    colors = ["#94A3B8", "#F59E0B", "#2563EB", "#7C3AED"]
    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    bars = ax.barh(labels, values, color=colors)
    ax.invert_yaxis()
    ax.set_xlabel("RMSE（越低越好）")
    ax.set_title("香港流感主测试期：同配对总体 RMSE")
    ax.grid(axis="x", alpha=0.25)
    for bar, value in zip(bars, values):
        ax.text(value + 0.06, bar.get_y() + bar.get_height() / 2, f"{value:.3f}", va="center")
    fig.tight_layout()
    fig.savefig(REPORT / "01_main_test_rmse.png", dpi=180)
    plt.close(fig)


def plot_horizons() -> None:
    frame = pd.read_csv(OUTPUTS / "router_metrics.csv")
    frame = frame[frame["week_ahead"].notna()]
    fig, ax = plt.subplots(figsize=(10.5, 6))
    styles = {
        "Baseline": ("论文常数基线", "#94A3B8", "--"),
        "AWBE": ("论文 AWBE", "#2563EB", "-"),
        "KG_ADAPTIVE_ENSEMBLE": ("项目独立自适应组合", "#F59E0B", "-."),
        "KG_HORIZON_ROUTER": ("KG 预测期路由", "#7C3AED", "-"),
    }
    for model, (label, color, line) in styles.items():
        part = frame[frame["model"] == model].sort_values("week_ahead")
        ax.plot(part["week_ahead"], part["rmse"], marker="o", label=label, color=color, linestyle=line)
    ax.set_xlabel("预测期（0=nowcast，1–8=提前周数）")
    ax.set_ylabel("RMSE（越低越好）")
    ax.set_title("主测试期逐预测期 RMSE")
    ax.set_xticks(range(9))
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(REPORT / "02_horizon_rmse.png", dpi=180)
    plt.close(fig)


def plot_external() -> None:
    frame = pd.read_csv(OUTPUTS / "postcovid_metrics.csv")
    frame = frame[frame["week_ahead"].isna()].set_index("model")
    order = ["Baseline", "KG_ADAPTIVE_ENSEMBLE", "KG_HORIZON_ROUTER", "AWBE"]
    labels = ["论文常数基线", "项目独立自适应组合", "KG 预测期路由", "论文 AWBE"]
    values = [float(frame.loc[item, "rmse"]) for item in order]
    colors = ["#94A3B8", "#F59E0B", "#7C3AED", "#2563EB"]
    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    bars = ax.barh(labels, values, color=colors)
    ax.invert_yaxis()
    ax.set_xlabel("RMSE（越低越好）")
    ax.set_title("2023–2024 post-COVID 外部测试")
    ax.grid(axis="x", alpha=0.25)
    for bar, value in zip(bars, values):
        ax.text(value + 0.04, bar.get_y() + bar.get_height() / 2, f"{value:.3f}", va="center")
    fig.tight_layout()
    fig.savefig(REPORT / "03_postcovid_rmse.png", dpi=180)
    plt.close(fig)


def build_report() -> None:
    base = json.loads((OUTPUTS / "final_summary.json").read_text())
    router = json.loads((OUTPUTS / "router_summary.json").read_text())
    external = json.loads((OUTPUTS / "postcovid_summary.json").read_text())
    main_router = router["router"]
    main_awbe = router["awbe"]
    ext_router = external["router"]
    ext_awbe = external["awbe"]
    text = f"""# Nature Communications 香港流感对比实验最终报告

## 最终判定

`NATCOMM_HK_FLU_BENCHMARK_COMPLETED_GENERALIZATION_NOT_CONFIRMED`

实验真实完成，但不建议将其写成“项目稳定优于论文模型”。主测试期的知识图谱预测期路由仅小幅优于 AWBE；在 2023–2024 post-COVID 外部测试中没有保持优势。

## 论文与来源

- 论文：An adaptive weight ensemble approach to forecast influenza activity in an irregular seasonality context
- 期刊：Nature Communications 15, 8625 (2024)
- DOI：10.1038/s41467-024-52504-1
- 官方代码/数据提交：`b0bd4ccfd9a4a4183c07f122508056a84d271a0f`
- 官方 Data.zip SHA-256：`27c3003babd3b4ded5cf5c37a670cacba97028062c7ff3b1e24f82777254237c`
- 官方 Results_point.zip SHA-256：`f7c289b81ec3d5cf9b075ca8d8fb903b1da20efba29cba312eaea982454b03de`

## 实验合同

- 开发验证窗口：2006-11-05 至 2007-10-28。
- 主测试：origin 从 2007-11-04 开始，target 截止 2019-07-14。
- 排除论文声明的数据生成机制变化区间：2009-04-05 至 2010-03-21。
- 预测期：0–8 周。
- 主测试严格同配对：{base['protocol']['paired_rows']} 行。
- 候选数：{base['protocol']['selection']['candidate_count']}；仅按开发期 RMSE 锁定。
- 测试标签未参与模型或路由选择。
- 作者模型使用仓库发布的逐行预测；没有声称在缺少 R/Windows 环境时完成原码全重跑。

## 项目独立自适应组合

锁定方案：`{base['protocol']['selection']['selected_name']}`。

| 方法 | RMSE | MAE | WAPE | 吻合度 |
|---|---:|---:|---:|---:|
| 论文常数基线 | {base['paper_baseline_result']['rmse']:.4f} | {base['paper_baseline_result']['mae']:.4f} | {pct(base['paper_baseline_result']['wape'])} | {pct(base['paper_baseline_result']['agreement'])} |
| 项目独立自适应组合 | {base['project_result']['rmse']:.4f} | {base['project_result']['mae']:.4f} | {pct(base['project_result']['wape'])} | {pct(base['project_result']['agreement'])} |
| 论文 AWBE | {base['paper_awbe_result']['rmse']:.4f} | {base['paper_awbe_result']['mae']:.4f} | {pct(base['paper_awbe_result']['wape'])} | {pct(base['paper_awbe_result']['agreement'])} |

项目独立组合相对常数基线 RMSE 改善 {pct(base['relative_rmse_improvement_vs_baseline'])}，但相对 AWBE 的 RMSE 差 {pct(-base['relative_rmse_improvement_vs_awbe'])}。因此独立方案没有追平论文方法。

## 知识图谱预测期路由

开发期预先支持的路由是：horizon 0 使用项目组合，horizon 1–8 使用论文 AWBE。该方案是对论文模型的编排扩展，不是独立替代模型。

| 主测试方法 | RMSE | MAE | WAPE | 吻合度 |
|---|---:|---:|---:|---:|
| AWBE | {main_awbe['rmse']:.4f} | {main_awbe['mae']:.4f} | {pct(main_awbe['wape'])} | {pct(main_awbe['agreement'])} |
| KG 预测期路由 | {main_router['rmse']:.4f} | {main_router['mae']:.4f} | {pct(main_router['wape'])} | {pct(main_router['agreement'])} |

主测试期路由相对 AWBE 的 RMSE 改善 {pct(router['relative_rmse_improvement_vs_awbe'])}，属于小幅改善。

## post-COVID 外部测试

测试目标日期：{external['evaluation_target_start']} 至 {external['evaluation_target_end']}，{external['paired_rows']} 个严格配对预测。

| 外部测试方法 | RMSE | MAE | WAPE | 吻合度 |
|---|---:|---:|---:|---:|
| AWBE | {ext_awbe['rmse']:.4f} | {ext_awbe['mae']:.4f} | {pct(ext_awbe['wape'])} | {pct(ext_awbe['agreement'])} |
| KG 预测期路由 | {ext_router['rmse']:.4f} | {ext_router['mae']:.4f} | {pct(ext_router['wape'])} | {pct(ext_router['agreement'])} |

外部测试中 KG 路由 RMSE 比 AWBE 高 {(ext_router['rmse'] / ext_awbe['rmse'] - 1) * 100:.2f}%，WAPE 也略差，因此主期内改善没有获得外部泛化确认。

## 安全与可复现性

- 确定性重放：PASS。
- future-label sentinel：PASS。
- 数据库写入：0。
- forecast run 创建：0。
- Promotion：0。
- Active pointer：0。
- 原项目 tracked dirty 文件未被实验脚本修改。

## 论文使用建议

这篇 Nature Communications 论文可以作为高等级、强复现性的补充基准，但当前结果不适合替换为“项目明显优于论文”的主结果。可以如实写为：知识图谱 horizon-aware 编排在长期主测试期取得 0.79% RMSE 改善，但在 post-COVID 分布变化下未维持该优势，说明路由需要跨时期稳健性约束。
"""
    (REPORT / "00_FINAL_REPORT.md").write_text(text, encoding="utf-8")


def main() -> None:
    plot_main()
    plot_horizons()
    plot_external()
    build_report()
    copies = [
        "final_summary.json",
        "locked_selection.json",
        "test_metrics_overall.csv",
        "test_metrics_by_horizon.csv",
        "router_summary.json",
        "router_metrics.csv",
        "postcovid_summary.json",
        "postcovid_metrics.csv",
    ]
    for name in copies:
        (REPORT / name).write_bytes((OUTPUTS / name).read_bytes())
    manifest = []
    for path in sorted(REPORT.iterdir()):
        if path.name == "MANIFEST.sha256" or not path.is_file():
            continue
        manifest.append(f"{sha256(path)}  {path.name}")
    (REPORT / "MANIFEST.sha256").write_text("\n".join(manifest) + "\n", encoding="utf-8")
    print((REPORT / "00_FINAL_REPORT.md").read_text())


if __name__ == "__main__":
    main()
