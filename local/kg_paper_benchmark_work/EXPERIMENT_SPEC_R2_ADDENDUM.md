# EXPERIMENT_SPEC_R2_ADDENDUM — 方法缺口补齐轮（2026-08-19）

本文件是 EXPERIMENT_SPEC.md 的补充，记录第二轮（gap-closure round）对论文
Bleichrodt et al. 2024 (RSOS 11(7):240248) 方法缺口的补齐实现。所有不变量
（切分、配对集合、qwen 参数、ensemble 权重、旧 10 模型逐字节一致）不变。

## 新增模型（MODEL_IDS 追加在末尾，旧 10 模型顺序不动）

1. **paper_subepidemic_second** — 候选集 {单波 GGM, 单波 GLM, 双波 GLM × Cthr ∈
   {30%,40%,50%,60%,70%}}，按论文式 2.4 AICc 选第二名。候选数 < 2 时回退
   paper_slr 并记 fallback。
2. **paper_subepidemic_ensemble_weighted** — 全部候选按 Akaike 权重
   w_i ∝ exp(−Δ_i/2) 加权平均。
3. **paper_subepidemic_ensemble_unweighted** — AICc 前 4 名等权平均。
4. **paper_prophet** — 11 周校准窗，Prophet 默认参数 + weekly_seasonality=True，
   区间用 Prophet 自带 95%（interval_width=0.95），截断 ≥0。确定性：
   `fit(df, seed=SEED)`（透传 cmdstanpy optimize），并在 fit 与 predict 前
   `np.random.seed(SEED)`（Prophet 不确定性模拟用 numpy 全局 RNG）。
   拟合失败回退 paper_slr 并记 fallback。

## 双波 GLM 构造（对应官方 modifiedLogisticGrowthPatch.m）

- 参数 (r1, p1, K1, r2, p2, q)，K2 = K1·exp(−q)，q ≥ 0，m = 7（6 参数 + 方差项）。
- 触发：模型自身第一波累计首次 ≥ Cthr ×（窗口末累计值）的 Euler 子步激活第二波
  （官方代码 invasions 判定的离散化对应）。
- 第二波初始累计 = 触发时刻第一波累计（官方 Cinvasions 约定），总累计
  C(t) = c1 + (c2 − c2_at_trigger)，触发点连续。
- 初值网格固定确定：(r1,p1) 复用单波 9 点网格 × r2 ∈ {0.1, 0.4}，
  p2=0.8，q0=0.25，K1_0 = 2×窗口末累计。
- 前导零仍截断；AICc 需 n−m−1 > 0，故截断后不足 9 点的窗口自动排除双波候选。

## Bootstrap 区间（替代论文 300 次参数 bootstrap）

确定性残差 bootstrap：每个 origin 用全新 `np.random.default_rng(20260819)`，
对模型组合拟合的窗内一步发病率残差有放回抽样，加到拟合发病率（截 ≥0）得伪窗口，
同结构候选以原参数暖启动重拟合并前推 h=1..4；weighted 模式在每个 replicate 上
重算 Akaike 权重，second/unweighted 保持原结构集（structure-conditional）。
每条 replicate 前推再加一个重抽样的一步残差 × √h（观测噪声项，与本基准其他
模型的 PI 构造一致）；取 2.5%/97.5% 分位数，截 ≥0。

**偏差记录**：
1. 协议要求 300 次；实测 300 次下单 origin 三模型 bootstrap ≈63s，
   100 个 origin 单次运行 ≈105 分钟，远超 20 分钟预算，按协议降为 **100 次**
   （BOOTSTRAP_REPLICATES = 100），已在 parameter_and_seed_manifest.json 记录。
2. 纯"重拟合分布"区间只含参数不确定性，validation 覆盖率仅 0.27–0.37，
   低于协议 0.5–1.0 的合理带；加入观测噪声项后（抽样实测 10 个 validation
   origin）覆盖率 0.65–0.725，满足要求。该项为本轮对协议 bootstrap 描述的
   显式扩展，已在代码 docstring 与本文件记录。

## 最终实测（v2 产物，2026-08-20 写出）

新模型 coverage_95 / mean_interval_width（validation | test）：

| model | val coverage | val width | test coverage | test width |
|---|---|---|---|---|
| paper_subepidemic_second | 0.670 | 81.9 | 0.639 | 362.8 |
| paper_subepidemic_ensemble_weighted | 0.620 | 62.8 | 0.603 | 251.4 |
| paper_subepidemic_ensemble_unweighted | 0.690 | 52.6 | 0.603 | 206.1 |
| （参照）paper_subepidemic_top | 0.465 | 47.9 | 0.479 | 190.4 |
| （参照）paper_prophet | 0.575 | 40.1 | 0.330 | 131.3 |

如实发现：11 点校准窗上 sub-epidemic 类模型的区间天然偏窄（不加观测噪声时
val 覆盖率 0.27–0.37，旧单波 top 模型也仅 0.465），属于该类方法在短窗协议下的
真实经验属性；加入观测噪声项后 bootstrap 区间进入协议合理带。test 期（2019
大暴发）区间宽度显著大于 validation 期，反映残差尺度随疫情强度增大。

## 等价验证（只读，不替换任何现有模型）

- **autoarima_equivalence.json**（C）：pmdarima.auto_arima（stepwise、AICc、
  seasonal=False、max_p=2/max_d=1/max_q=2 与网格同箱、random_state=SEED）对全部
  100 个 origin 的 11 周窗与 paper_arima 网格 AICc 阶数选择比较。
  **结论：阶数一致率 9/100 = 9%**。主要原因是 auto_arima 的差分阶 d 由 KPSS
  检验决定（11 点短窗上多选 d=0），且其 AICc 由自带状态空间后端计算，与
  statsmodels .aicc 的数值口径不同；两侧搜索策略（stepwise vs 穷举）在短窗上
  也会发散。paper_arima 保持不变（协议批准的确定性强等价）。
- **gam_equivalence.json**（D）：**未运行**。pygam 0.12.0 依赖约束
  scipy<1.17,>=1.11.1，`pip install pygam` 把 scipy 1.18.0 降到 1.16.3，导致
  paper_subepidemic_top 的 least_squares 结果在 748 行预测上发生字节级漂移，
  违反"旧 10 模型逐字节不变"的最高优先级不变量。已卸载 pygam（及附带的
  progressbar2、python-utils）并恢复 scipy==1.18.0，恢复后旧行逐字节一致，
  pip check 干净。失败命令与原因记录于 gam_equivalence.json 与 manifest 的
  new_gap_attempts。

## 环境变更（均在 .venv 内）

- 新增：pmdarima 2.1.1、scikit-learn 1.9.0、prophet 1.4.0、cmdstanpy 1.3.0
  （及依赖 Cython/joblib/threadpoolctl/matplotlib/holidays/stanio 等）。
- 曾安装后卸载：pygam 0.12.0（见上）。
- scipy 保持 1.18.0；pip check 干净。

## 回归锁定

tests/test_old_models_row_regression.py 把新 out/rolling_origin_predictions.csv
中旧 10 模型的每一行与 out_prev_snapshot/（round-1 快照，sha256 =
2b69749a…f336f）逐字节比较。
