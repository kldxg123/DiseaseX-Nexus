from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(r"F:\作业\aibola")
EVIDENCE = (
    ROOT
    / "reports"
    / "nc_submission_revision_20260826_wape_boxplot"
    / "Evidence"
)
OUTPUT = (
    ROOT
    / "reports"
    / "nc_submission_revision_20260826_wape_boxplot"
    / "COMPARISON_EXPERIMENT_RESULTS_AND_VECTOR_DATA.md"
)

FILES = {
    "aggregate": EVIDENCE / "FOUR_PATH_METRICS.csv",
    "horizon": EVIDENCE / "FOUR_PATH_HORIZON_METRICS.csv",
    "postcovid": EVIDENCE / "POSTCOVID_FOUR_PATH_METRICS.csv",
    "boxsummary": EVIDENCE / "Candidate_Fig_WAPE_Boxplots_Five_Number_Summary.csv",
    "predictions": EVIDENCE / "FOUR_PATH_PAIRED_PREDICTIONS_H0_H8.csv",
    "summary": EVIDENCE / "FOUR_PATH_SUMMARY.json",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def markdown_table(headers: list[str], body: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def pct(value: str | float, digits: int = 2) -> str:
    return f"{float(value) * 100:.{digits}f}%"


def num(value: str | float, digits: int = 4) -> str:
    return f"{float(value):.{digits}f}"


def csv_block(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig").strip()
    return f"```csv\n{text}\n```"


def main() -> None:
    missing = [str(path) for path in FILES.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError(missing)

    aggregate = rows(FILES["aggregate"])
    horizon = rows(FILES["horizon"])
    postcovid = rows(FILES["postcovid"])
    boxsummary = rows(FILES["boxsummary"])
    predictions = rows(FILES["predictions"])
    summary = json.loads(FILES["summary"].read_text(encoding="utf-8"))

    methods = ["Baseline", "DiseaseX", "Paper AWBE", "KG merged route"]
    aggregate_map = {
        (row["scope"], row["method"]): row for row in aggregate
    }
    post_map = {row["method"]: row for row in postcovid}

    primary = {m: float(aggregate_map[("H0-H1", m)]["wape"]) * 100 for m in methods}
    full = {m: float(aggregate_map[("H0-H8", m)]["wape"]) * 100 for m in methods}
    post = {m: float(post_map[m]["wape"]) * 100 for m in methods}

    origins = sorted({row["origin"] for row in predictions})
    targets = sorted({row["date"] for row in predictions})
    h01 = [row for row in predictions if int(row["week_ahead"]) <= 1]
    h01_mean_target = sum(float(row["true"]) for row in h01) / len(h01)
    true_values = [float(row["true"]) for row in predictions]
    missing_cells = sum(
        value == "" for row in predictions for value in row.values()
    )

    aggregate_table = []
    for scope in ["H0", "H1", "H0-H1", "H0-H8"]:
        for method in methods:
            row = aggregate_map[(scope, method)]
            aggregate_table.append(
                [
                    scope,
                    method,
                    row["n"],
                    pct(row["wape"]),
                    f"{float(row['agreement_pct']):.2f}%",
                    num(row["mae"], 4),
                    num(row["rmse"], 4),
                ]
            )

    horizon_map = {
        (int(row["horizon"]), row["method"]): row for row in horizon
    }
    horizon_table = []
    for h in range(9):
        horizon_table.append(
            [f"H{h}"]
            + [pct(horizon_map[(h, method)]["wape"]) for method in methods]
        )

    post_table = []
    for method in methods:
        row = post_map[method]
        post_table.append(
            [
                method,
                row["n"],
                pct(row["wape"]),
                f"{float(row['agreement_pct']):.2f}%",
                num(row["mae"], 4),
                num(row["rmse"], 4),
            ]
        )

    box_table = []
    for row in boxsummary:
        box_table.append(
            [
                row["panel"],
                row["method"],
                row["scope"],
                row["distribution_unit"],
                row["n"],
                num(row["minimum"], 6),
                num(row["q1"], 6),
                num(row["median"], 6),
                num(row["q3"], 6),
                num(row["maximum"], 6),
                num(row["aggregate_wape_pct"], 6),
            ]
        )

    source_hash_table = [
        [key, str(path.name), sha256(path)] for key, path in FILES.items()
    ]

    main_text = f"""# 对比实验结果总结与矢量图完整数据

## 1. 文档用途

本文档汇总香港流感四路径对比实验，并内嵌重绘论文矢量图所需的全部数值数据。文档末尾保留完整的 4,968 行逐预测记录，因此仅凭本文件即可重新计算 WAPE、MAE、RMSE、逐预测期指标和箱线图数据，无须从位图反推数值。

本文件记录的是固定实验结果，不代表临床或公共卫生有效性验证。

## 2. 实验对象与方法身份

- 数据结构：周度流感监测序列；552 个预测起点，每个起点包含 H0–H8，共 4,968 个共同配对行。
- 预测起点：{origins[0]} 至 {origins[-1]}。
- 目标日期：{targets[0]} 至 {targets[-1]}。
- 真实值范围：{min(true_values):.12g} 至 {max(true_values):.12g}；真实值为零的行数为 {sum(v == 0 for v in true_values)}。
- 空单元格数量：{missing_cells}。
- H0–H1：1,104 个共同配对行；用于短预测期主比较及箱线图 Panel A。
- H0–H8：4,968 个共同配对行；用于完整预测期比较及 Panel B。

| 路径 | 真实方法身份 | 解释边界 |
| --- | --- | --- |
| Baseline | 论文配套的常数基线 | 仅作基准，不是知识图谱推荐模型 |
| DiseaseX | 独立递归季节性 Poisson 梯度提升；只使用监测历史 | 完全独立于论文 AWBE 预测输出 |
| Paper AWBE | 作者公开、保持不变的论文预测结果 | 论文方法路径 |
| KG merged route | H0 使用 DiseaseX 自适应组合，H1–H8 使用 Paper AWBE | 不是完全独立、不是 paper-free，必须单独标注 |

## 3. 指标定义

- `WAPE = Σ|y - ŷ| / Σ|y|`，越低越好。
- `MAE = mean(|y - ŷ|)`，越低越好。
- `RMSE = sqrt(mean((y - ŷ)^2))`，越低越好。
- `预测吻合度 = max(0, 1 - WAPE) × 100%`。它只是 WAPE 的互补显示量，不是分类准确率。
- 所有方法必须在相同的 `origin + horizon + target` 行上比较；缺失预测不得补零。

## 4. 核心结论

1. **短预测期 H0–H1**：独立 DiseaseX 的 WAPE 为 {primary['DiseaseX']:.6f}%，论文 AWBE 为 {primary['Paper AWBE']:.6f}%，差距仅 {primary['DiseaseX'] - primary['Paper AWBE']:.6f} 个百分点。该范围支持“接近论文方法”，但不能写成严格优于论文。
2. **完整 H0–H8**：独立 DiseaseX 的 WAPE 增至 {full['DiseaseX']:.6f}%，论文 AWBE 为 {full['Paper AWBE']:.6f}%，相差 {full['DiseaseX'] - full['Paper AWBE']:.6f} 个百分点。独立方法的近似效果没有延续到长期预测期。
3. **KG 合并路径**：H0–H1 WAPE 为 {primary['KG merged route']:.6f}%，H0–H8 为 {full['KG merged route']:.6f}%；数值略低于论文 AWBE，但该路径在 H1–H8 直接使用论文 AWBE，因此不能作为独立超越论文的证据。
4. **外部 post-COVID 时期**：论文 AWBE 为 {post['Paper AWBE']:.6f}%，KG 合并路径为 {post['KG merged route']:.6f}%；KG 路径高出 {post['KG merged route'] - post['Paper AWBE']:.6f} 个百分点，没有优于论文。
5. 30% WAPE 线是项目展示门槛，不是统计显著性界限。箱线图展示的是误差分布或不同预测期的离散程度，不是置信区间。

## 5. 聚合结果（图 6、Supplementary Fig. 1 可直接使用）

{markdown_table(['范围', '方法', 'n', 'WAPE', '吻合度', 'MAE', 'RMSE'], aggregate_table)}

## 6. H0–H8 逐预测期 WAPE（Supplementary Fig. 2 与 Fig. 8 Panel B）

{markdown_table(['预测期', *methods], horizon_table)}

## 7. post-COVID 外部时期结果（Supplementary Fig. 3）

{markdown_table(['方法', 'n', 'WAPE', '吻合度', 'MAE', 'RMSE'], post_table)}

## 8. 箱线图五数概括（Supplementary Fig. 8）

{markdown_table(['Panel', '方法', '范围', '分布单位', 'n', '最小值', 'Q1', '中位数', 'Q3', '最大值', '聚合 WAPE(%)'], box_table)}

### Panel A 原始量的精确生成方法

固定归一化分母为 H0–H1 所有共同目标值的均值：

```text
common_mean_target = {h01_mean_target:.15f}
row_normalized_absolute_error_pct = abs(true - prediction) / common_mean_target * 100
```

每种方法均有 1,104 个值。箱体为 Q1–Q3，中线为中位数，箱须为 1.5×IQR；离群点保留。Y 轴使用对数刻度，建议范围 `0.008–520`。

### Panel B 原始量

每种方法的 9 个原始值就是第 6 节 H0–H8 的逐预测期 WAPE 百分数。箱体同样采用 Q1–Q3、中位数和 1.5×IQR；所有 9 个点均应显示。聚合 WAPE 用空心菱形单独标注，不能用九个预测期 WAPE 的算术均值替代。

## 9. 矢量图重绘规范

### 推荐配色

| 方法 | HEX |
| --- | --- |
| Baseline | `#7B8794` |
| DiseaseX | `#E69F00` |
| Paper AWBE | `#2F6FB0` |
| KG merged route | `#7A3DE2` |

### 图形对应关系

| 图 | 使用数据 | 推荐形式 |
| --- | --- | --- |
| Main Figure 6 | 第 5 节 H0–H1 与 H0–H8 WAPE | 分组横向条形图，四路径分组；显示精确百分数 |
| Supplementary Fig. 1 | 第 5 节 H0、H1、H0–H1 WAPE | 分面分组柱状图 |
| Supplementary Fig. 2 | 第 6 节 H0–H8 WAPE | 热力图或折线矩阵；同一预测期横向比较 |
| Supplementary Fig. 3 | 第 7 节 post-COVID WAPE | 四路径横向条形图 |
| Supplementary Fig. 8A | 末尾逐行预测数据按第 8 节公式派生 | 箱线图；对数 Y 轴；显示离群点与聚合 WAPE 菱形 |
| Supplementary Fig. 8B | 第 6 节九个预测期 WAPE | 箱线图；显示全部点、30% 门槛线与聚合 WAPE 菱形 |

### Python 矢量导出要求

```python
import matplotlib as mpl
mpl.rcParams["svg.fonttype"] = "none"   # SVG 保留可编辑文字
mpl.rcParams["pdf.fonttype"] = 42       # PDF 使用 TrueType 字体
fig.savefig("figure.svg", bbox_inches="tight")
fig.savefig("figure.pdf", bbox_inches="tight")
```

- 白色背景；建议 Arial/Helvetica；最终字号不低于 7 pt。
- 不从 PNG 截图或描摹数据。
- 单次固定实验不添加伪造误差线；箱线图只描述行级误差或预测期差异。
- 图注必须写明 KG merged route 在 H1–H8 使用 Paper AWBE。

## 10. 数据文件身份与 SHA-256

{markdown_table(['逻辑名称', '文件', 'SHA-256'], source_hash_table)}

## 11. 机器可读数据附录

以下数据均原样内嵌，数值未四舍五入。前四个小表便于直接复制；最后一个折叠区包含全部 4,968 行逐预测记录。

### A. 聚合指标原始 CSV

{csv_block(FILES['aggregate'])}

### B. 逐预测期指标原始 CSV

{csv_block(FILES['horizon'])}

### C. post-COVID 指标原始 CSV

{csv_block(FILES['postcovid'])}

### D. 箱线图五数概括原始 CSV

{csv_block(FILES['boxsummary'])}

<details>
<summary><strong>E. 完整逐预测记录（4,968 行，点击展开）</strong></summary>

字段说明：`origin` 为预测起点，`date` 为目标日期，`week_ahead` 为 H0–H8，`true` 为真实值，其余列为各方法预测值。`KG_ADAPTIVE_ENSEMBLE` 是原始实验工件中的附加中间列，保留用于审计；四路径主比较使用 `Baseline`、`DiseaseX`、`Paper AWBE` 与 `KG merged route`。

{csv_block(FILES['predictions'])}

</details>

## 12. 可支持与不可支持的论文表述

### 可支持

- 独立 DiseaseX 在 H0–H1 与论文 AWBE 接近，WAPE 差距约 0.21 个百分点。
- KG 合并路径在固定回放数据上的聚合 WAPE 最低。
- 长预测期中独立 DiseaseX 与论文 AWBE 的差距明显扩大。

### 不可支持

- 不得写成独立 DiseaseX 在 H0–H8 超过论文 AWBE。
- 不得把 KG merged route 描述成完全独立于论文方法。
- 不得把 `1-WAPE` 称为分类准确率。
- 不得把同一批固定回放结果表述为前瞻临床验证或 fresh blind certification。
"""

    OUTPUT.write_text(main_text, encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(OUTPUT),
                "bytes": OUTPUT.stat().st_size,
                "sha256": sha256(OUTPUT),
                "embedded_prediction_rows": len(predictions),
                "status": "PASS",
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
