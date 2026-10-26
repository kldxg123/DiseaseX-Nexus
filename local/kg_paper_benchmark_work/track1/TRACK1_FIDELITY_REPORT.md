# Track 1 — Bleichrodt et al. 2024 官方产物忠实度验证报告

- 论文：Bleichrodt et al. 2024, *R. Soc. Open Sci.* 11(7):240248, DOI 10.1098/rsos.240248（mpox 多模型短期预测比较）
- 被验证实现：`kg_paper_benchmark_work/src/benchlib.py` 中的 `forecast_slr_window` / `forecast_arima_window` / `forecast_subepidemic_window`（只读使用，未修改）
- 范围：只在论文官方 mpox 数据与官方预测产物上做忠实度验证；不与任何其它项目模型比较优劣
- 运行脚本：`track1/run_track1_fidelity.py`（主验证）、`track1/track1_diagnostics.py`（归因诊断）
- 机器可读结果：`track1/track1_fidelity.json`、`track1/track1_diagnostics.json`
- 运行环境：`.venv` Python 3.12（statsmodels / scipy / pandas / openpyxl 3.1.5）

## 总结判定

| 项目 | 结果 |
|---|---|
| 数据/代码版本核对 | 通过（本地快照 = GitHub main @ 1222ec8；Zenodo 11504600 = v1.0.0 标签 @ fe726df，两者仅 LICENSE/README 级别差异，见 D1 注） |
| 清洗复现 | 通过：重建周序列与官方 ARIMA Formatted 文件 270/270 完全一致；与 n-sub-epidemic ranked(1) 内嵌数据在除两个 US 序列外的 7 个地点完全一致（US 不一致是官方产物自身的标签互换，见 D6） |
| SLR 点预测 | **通过**：10 周与 11 周校准下 72+36 个预测点与官方逐点完全相同（median/max 相对误差 = 0） |
| ARIMA 点预测 | **未过 5% 中位相对误差线**（10 周：中位 11.03%；11 周：中位 5.38%）。归因：auto.arima 的选阶方法学差异（KPSS 定 d、stepwise 搜索、允许 d=2 与 drift），而非拟合实现错误——同一 (p,d,q,drift) 下 statsmodels 能逐点复现官方预测（15/18 origin 相对误差 <1e-8，见诊断） |
| sub-epidemic ranked(1) | **未过 20% 中位相对误差线**（10 周：中位 59.5%）。归因：benchlib 单波 {GGM,GLM} 候选集与官方 GLM-only 多波候选集结构性不同 + 参数边界/优化器差异（见 D3 与诊断） |
| 论文总体数字核对 | 通过：官方 metrics xlsx 重算的 France calibration-11 平均 MAE 与论文正文引用值逐项一致（ARIMA h2–h4 = 90.5/92.5/93.8，论文 "range: 90.5–93.8"；SE-Ranked(1) h1 = 80.9，论文 80.9） |

总体判定（ARIMA/SLR <5%、sub-epidemic <20% 口径）：**fail**——SLR 完全忠实；ARIMA 与 sub-epidemic 的差距已逐项归因，详见下文，未做任何粉饰。

## 1. 版本与数据出处核对

- 本地解压目录来自 `kg_paper_benchmark_work/official_code.zip`（24,851,365 字节，md5 `e5fb1895…`，sha256 `604093ee…`），zip 注释 = GitHub 提交 `1222ec8fc44c6c683cabd03474ea7d2483cd1ae0`（"Create LICENSE"，2024-06-06，main 分支快照）。
- Zenodo 记录 11504600（2026-08-19 在线核对）：版本 v1.0.0，指向 GitHub 标签提交 `fe726df742d2bd3effb76040d6c4b15513fe1079`，归档 zip 25,001,865 字节 / md5 `300aee96e4080da1e9fa452988452956`。**本地快照不是 Zenodo 归档**（体积/md5 不同）；1222ec8 是 v1.0.0 之后的 "Create LICENSE" 提交，数据与预测产物相同，差异在 LICENSE/README 层面。
- 关键文件 sha256 已存入 `track1_fidelity.json → version_provenance.file_sha256`（含 OWID 数据 `ba36bade…`、CDC 数据 `ab067165…`、两份 R 清洗/预测脚本、五个预测 zip、两份 metrics xlsx）。

## 2. 清洗复现（Data-Cleaning-and-Preparation.R → pandas）

复现规则（逐条对应官方 R 脚本）：

1. OWID：`date <= 2023-08-09`；地点限于 United States/Brazil/Spain/France/United Kingdom/Germany/Canada/World；United States 改名 `US(OWID)`；取 `new_cases`。
2. CDC：`Epi_date_V3`（m/d/Y）+ `Cases`，标记 `US(CDC)`。
3. 合并后按 `floor_date(week, week_start=4)` 聚合为**周四起始（周四–周三）**周和；宽表展开；删除 `2022-04-28` 周；NA→0。

产出：9 个地点 × 66 周（2022-05-05 至 2023-08-03）周序列，写入 `track1_reconstructed_series/weekly_*.csv`，哈希见 `SHA256SUMS.txt`。

验证（用官方预测文件内嵌的校准期观测值反查）：

- ARIMA Formatted（calibration-10, h=1）：**270/270 文件、全部校准行与重建序列逐值一致**（9 地点 × 30 origin）。校准窗与文件名日期完全对齐（文件名日期 = 最后一个校准周周四）。
- n-sub-epidemic ranked(1)（calibration-10, h=1）：261 文件、2856 行按日期键逐值比对；Brazil/Canada/France/Germany/Spain/United Kingdom/World **全部一致**；US(CDC) 28/30 与 US(OWID) 30/30 不一致，且全部 58 个不一致文件与对方 US 序列**交叉一致** → 官方 MATLAB 工具箱产物中两个 US 序列标签互换（D6）。同时验证了工具箱的前导零截断规则（文件内嵌 `data` 列从窗口内首个非零周开始，与论文 "initial zeros … were dropped" 一致）。

## 3. 保真度对比（与官方文件相同的校准窗）

方法：3 个地点（World、Brazil、US(OWID)）× 6 个 origin（早 07 月/08 月初、中 10/11 月、晚 12 月末/01 月末，calibration-10 官方 origin 范围 2022-07-07…2023-01-26 内）× h=1..4，共 72 点/模型；补充 11 周校准 3 origin × 3 地点 = 36 点/模型。官方值取自 `Forecasts/ARIMA/…/All-Quantiles`（`means` 列）、`SLR.zip`（同结构）、`n-sub-epidemic.zip` 的 `ranked(1)-…` 文件（`median` 列，预测行）。相对误差分母 `max(|official|, 1)`。注意：官方 n-sub 产物缺 Brazil 前三个 origin（论文承认低计数期未产出），Brazil 从 07-28 起，三模型 origin 集保持一致。US(OWID) 的 SE 对比使用输入对齐窗口（官方实际用 US(CDC) 数据拟合，D6）。

| 模型 | 校准窗 | 点数 | 中位相对误差 | 最大相对误差 | 判定线 | 判定 |
|---|---|---|---|---|---|---|
| SLR | 10 周 | 72 | 0.0000% | 0.0000% | <5% | **pass** |
| ARIMA | 10 周 | 72 | 11.03% | 182100%（官方截 0 我方非 0 的极端点） | <5% | fail |
| SE-Ranked(1) | 10 周 | 72 | 59.55% | 365% | <20% | fail |
| SLR | 11 周（补充） | 36 | 0.0000% | 0.0000% | <5% | **pass** |
| ARIMA | 11 周（补充） | 36 | 5.38% | 115600% | <5% | fail |
| SE-Ranked(1) | 11 周（补充） | 36 | 59.05% | 357% | <20% | fail |

ARIMA 的 72 点中 28 点与官方逐点完全相同（<1e-6）、32 点在 5% 以内；偏差集中在官方选了 d=2 或 drift 模型的 origin。SE 的 72 点中 15 点在 20% 以内。逐点明细见 `track1_fidelity.json → per_point_detail`。

## 4. 归因诊断（track1_diagnostics.py / track1_diagnostics.json）

**ARIMA**：官方用 `forecast::auto.arima`（KPSS 检验定 d、stepwise 搜索、max.d=2、允许 drift）；benchlib 用穷举网格 (p,q∈{0,1,2}, d∈{0,1}) 按 AICc 选阶、无 drift。诊断在扩展网格（d∈{0,1,2} + drift 变体）上重拟合：

- 18 个 origin 中 15 个存在网格内模型与官方预测逐点一致（相对误差 <1e-3，其中绝大多数 <1e-8），中位相对误差 ≈ 0；即 **statsmodels 的 ARIMA 拟合/预测实现本身忠实**，差距完全来自选阶方法学。
- 例：Brazil 2022-08-04，官方预测 983/1103/1223/1343（等差 +120），statsmodels `ARIMA(0,2,0)` 逐点复现；该阶在 auto.arima 搜索空间（max.d=2）内、在 benchlib 网格（d≤1）外。
- 但扩展网格的 AICc 最优解并不总等于 auto.arima 的选择（18 个中只有 4 个 AICc-best 精确命中），因为 auto.arima 的 d 由 KPSS 检验而非 AICc 决定——这是方法学差异，不是可通过加大网格消除的实现偏差。

**sub-epidemic ranked(1)**：官方框架（论文 2.3 节与工具箱代码）每个子波都是**三参数广义 logistic（GLM）**（`flag1=1`），候选集含多波模型且 Cthr 为**待估参数**，fmincon(sqp) 优化，参数边界随数据变化（r∈[mean(|d1:2|)/200, max(|d1:2|)×50]，p∈[0,1]，K∈[20,1e10]）。benchlib 的 `forecast_subepidemic_window` 在 {GGM, GLM} 单波内按 AICc 选，scipy least_squares，r≤5、p≤3 固定边界。诊断：

- 换成「GLM-only + 官方边界 + 官方起点」的单波实现后，对官方 ranked(1) 的中位相对误差降到 **10.0%**（18 origin），13/18 在 15% 以内 → benchlib 与官方差距的大头来自候选集含 GGM、边界过窄（多处拟合 r 顶到 5.0 上界）这两处实现设定。
- 剩余大偏差点（如 Brazil 08-04：官方 1001/969/815/604 衰减、任何单波拟合都难复现）对应官方 top 模型很可能是**多波（Cthr 待估）**结构；benchlib 的固定网格双波 glm2 全候选 top 中位相对误差 63%，同样复现不了官方多波拟合。
- 结论：benchlib 的 sub-epidemic 是论文方法的简化近似，不是官方实现的忠实复刻；20% 容差线下判 fail，原因如上（结构性为主）。

**区间形式差异（只记录、不评分）**：官方 SLR 的 "95% PI" 是 `fit ± z·se.fit`（拟合均值的标准误，非预测区间）；benchlib 用 `±1.96·σ_resid·√h`。本验证只比点预测。

## 5. 论文数字核对

用官方 `Additional Metrics/mpox-metrics-forecast-periods.xlsx`（38,000 行逐 origin 指标）重算 France、calibration-11、按模型×horizon 的平均 MAE：

- ARIMA：h1–h4 = 87.8 / 90.5 / 92.5 / 93.8；论文正文 "the ARIMA model performed best across most forecasting horizons (range: 90.5–93.8)"（指其最优的 h2–h4）→ **一致**。
- SE-Ranked(1) h1 = **80.9**；论文 "albeit the one-week horizon, where the n-sub-epidemic top-ranked (80.9) … performed best" → **一致**。

该核对同时证实：论文正文报告的主结果基于 **11 周校准**（xlsx 的 Calibration=11 切片与正文数字精确吻合），仓库同时发布 9/10/11 周三种校准的全部产物。

## 6. 发现的差异清单

- **D1 校准窗表述**：论文正文 "We primarily employed 11-week calibration periods"；仓库产物包含 9/10/11 周**三种**校准（`calibration-{9,10,11}`、`Calibration-{9,10,11}-Weeks`），主结果对应 11 周。任务书所称"仓库产物是 10 周"不准确——三种都在；本验证以 10 周为主口径（任务指定）并附 11 周补充。
- **D2 ARIMA 选阶方法学**：auto.arima（KPSS 定 d、stepwise、d≤2、drift）vs benchlib 小网格 AICc（d≤1、无 drift）；同阶下预测逐点一致（诊断证实），差距来自选阶。
- **D3 sub-epidemic 结构差异**：官方 GLM-only、多波、Cthr 待估、fmincon、数据自适应边界；benchlib {GGM,GLM} 单波、固定边界（r≤5, p≤3）、scipy/Euler(dt=0.05)。
- **D4 SLR 区间定义**：官方用 se.fit（均值置信区间），benchlib 用残差预测区间；点预测不受影响（逐点一致）。
- **D5 官方缺失产物**：n-sub-epidemic（及 spatial-wave）缺 Brazil 2022-07-07/07-14/07-21 三个 origin（低计数，论文已声明）。
- **D6 官方 US 标签互换（新发现）**：MATLAB 工具箱（n-sub-epidemic 与 spatial-wave，均已逐日期验证）把 US(OWID) 与 US(CDC) 两个序列**互换**拟合——"US-OWID" 文件内嵌并用以拟合的是 CDC 序列，反之亦然；预测行的未来观测值标签却是正确的。R 侧模型（按列名取数）无此问题（ARIMA/SLR 逐点验证通过）。**论文报告的 US(OWID)/US(CDC) 的 sub-epidemic 与 spatial-wave 指标很可能被对调。**

## 7. 产物清单

- `track1/run_track1_fidelity.py` — 主验证脚本（ruff 通过）
- `track1/track1_diagnostics.py` — 归因诊断脚本（ruff 通过）
- `track1/track1_fidelity.json` — 机器可读主结果（哈希、逐点对比、判定、差异清单）
- `track1/track1_diagnostics.json` — 归因诊断明细
- `track1/track1_reconstructed_series/weekly_{Brazil,Canada,France,Germany,Spain,US(CDC),US(OWID),United_Kingdom,World}.csv` + `SHA256SUMS.txt`
- `track1/TRACK1_FIDELITY_REPORT.md` — 本报告
