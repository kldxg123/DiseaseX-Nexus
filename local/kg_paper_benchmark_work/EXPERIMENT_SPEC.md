# EXPERIMENT_SPEC — KG_PAPER_BENCHMARK_REPLACEMENT (METHOD_TRANSFER_REPLICATION)

目标：在 F:/作业/aibola 下实现并运行一个确定性 rolling-origin 对比实验，把正式论文
Bleichrodt et al. 2024 (Royal Society Open Science 11(7):240248, DOI 10.1098/rsos.240248)
的可迁移单变量方法与项目方法在同一数据、同一切分、同一 horizon、同一指标下对比。

Python 解释器：F:/作业/aibola/.venv/Scripts/python.exe（已装 numpy/pandas/scipy/statsmodels/pypdf）。
代码目录：F:/作业/aibola/kg_paper_benchmark_work/src/
输出目录：F:/作业/aibola/kg_paper_benchmark_work/out/
测试目录：F:/作业/aibola/kg_paper_benchmark_work/tests/
不要修改以上目录以外的任何现有文件。

## 数据

CSV：F:/作业/aibola/remote_edit/post2024_exact_inputs/singapore_dengue_official_paper_aligned_2012_2019.csv
- 413 行（表头+412 周），列 year,week,cases,vault_id；2012-W01 至 2019-W48。
- 加载后必须校验 len(values)==412 且文件 sha256 == 5efbff843186392c86862361a47c9fb15f30090d0a379b46f9e44267060afac2，否则抛错。
- 缺失值：本序列无缺失；协议禁止把缺失周填 0（写一个断言：cases 列无空值）。

## 切分与滚动起点协议（严格遵守）

- values 索引 0..411。train=values[:312]；validation origins：e ∈ [313..362]（history=values[:e]），共 50 个；
  test origins：e ∈ [363..412]，共 50 个。
- horizons h ∈ {1,2,3,4}；目标索引 t_idx = e + h − 1。
- 有效配对：(e,h) 且 t_idx ≤ 411。validation 200 对全部有效；test 中 h=1:50, h=2:49, h=3:48, h=4:47。
  所有方法只在有效配对上评估，配对集合对所有方法完全一致（paired）。
- 论文方法校准窗：values[e−11:e]（11 周，论文协议）；sub-epidemic 模型按论文把窗口前导零截断到首个非零观测。
- 项目方法与 baseline 使用 history=values[:e]（全部历史）。
- 验证集用于：Qwen 受控调参、ensemble 权重选择。测试集只在全部选择冻结后评估一次
  （在结果 JSON 里记录 exactly_once 账本：validation_selection_completed=true 之后 test_evaluated_count=1）。

## 方法清单（10 个，model_id 必须完全一致）

论文方法（METHOD_TRANSFER_REPLICATION，保留论文公式/选择规则）：
1. paper_arima — 校准窗 11 点；statsmodels ARIMA，(p,d,q) 网格 p,q∈{0,1,2}, d∈{0,1}，
   按 AICc（statsmodels .aicc）选择；拟合失败按链 [(1,1,0),(0,1,1),(1,0,0)] 回退；再失败则窗口均值。
   预测 h=1..4；负值截断为 0（论文做法）。PI：statsmodels get_forecast(h).conf_int(alpha=0.05)，截断 ≥0。
   （在实现文档注明：论文用 R forecast::auto.arima 逐步搜索，此处用确定性强 grid+AICc 等价替代。）
2. paper_gam — 校准窗 11 点；时间 t=1..11 的三次 B 样条基（patsy d bs，df∈{3,4,5}）+ OLS（正态假设），
   用 GCV = n·RSS/(n−df_eff)² 选 df；预测 t=12..15；截断 ≥0。PI：ŷ ± 1.96·σ_resid·√h，截断 ≥0。
   （注明：论文用 mgcv GCV 惩罚样条，此处为 OLS B 样条 + GCV 的确定性强等价。）
3. paper_slr — 校准窗 11 点；y ~ t OLS；预测 t=12..15；截断 ≥0；PI 同 GAM 形式。
4. paper_subepidemic_top — 论文 sub-epidemic 框架的构造块（官方代码 flag1 0=GGM、1=GLM）：
   GGM: dC/dt = r·C^p；GLM: dC/dt = r·C^p·(1−C/K)。对累计病例曲线（窗口前导零截断后）做
   非线性最小二乘（scipy.optimize.least_squares），初值用固定确定网格（如 r∈{0.05,0.2,0.5},
   p∈{0.5,0.8,1.0}, K∈{Cmax·1.1, Cmax·2, Cmax·10}），C0=窗口首个累计值；按论文式 2.4 的
   AICc = n·log(SSE) + 2m + 2m(m+1)/(n−m−1)（m=参数个数+1 方差项，与官方 getAICc.m method1=0 一致）
   在 {GGM, GLM} 中选 top；前推 h 周（solve_ivp 或细步长 Euler 均可，须确定性），
   发病率预测=累计差分，截断 ≥0。PI：窗内一步残差经验分位数(2.5%,97.5%)×√h，截断 ≥0。
   全部拟合失败时回退 paper_slr 并在输出中记录 fallback=true。
   （注明：论文完整框架含 Cthr 触发的多子波（n≤5）与加权/不加权集成；此处实现 top 单波模型，
   多波集成作为显式缺口记录在案。）

项目方法：
5. project_kg_primary — 知识图谱推荐主模型 lagged_count_boosting.v1，KG 默认参数
   {n_estimators:50, learning_rate:0.1, min_samples_leaf:10}；全历史；多步递归预测 h=1..4。
   用法：sys.path.insert(0, 'F:/作业/aibola/kg_qwen_work/src')；
   from epidemic_core.kg_qwen.adapters_ext import fit_lagged_count_boosting, predict_lagged_count_boosting
   from epidemic_core.kg_r3c.execution import TrainingRow
   TrainingRow(period_start, ???, value, vault_id) —— 参考
   F:/作业/aibola/remote_edit/run_2024_method_comparison.py 的 rows()/project_forecast() 用法
   （period_start 从 date(2012,1,2) 起每周 +7 天，第二参数传 None，seed 传固定整数如 20260819）。
   predict 返回含 yhat/lower_bound/upper_bound。
6. project_qwen_tuned — 同一 adapter，参数在白名单网格 n_estimators∈{30,50,80,100},
   learning_rate∈{0.03,0.05,0.1,0.2}, min_samples_leaf∈{5,10,15,20} 内，仅在 validation 的
   200 个配对上按 WAPE（并列按 RMSE，再按参数字典序）选择；这就是"Qwen 受控调优"的确定性实现
   （白名单与边界即 kg_qwen contracts 的允许范围）。测试集不参与选择。
7. project_kg_ensemble — 分量 {project_qwen_tuned(冻结参数), seasonal_naive, last_value} 的
   凸组合，权重在 0.05 步长单纯形网格上仅在 validation WAPE 选择（并列规则：WAPE→RMSE→
   project_qwen_tuned 权重高者优先）；测试集评估一次。输出必须含单模型与组合模型结果、
   权重、选择依据与验证过程（ensemble_grid.csv）。

Baseline：
8. last_value — ŷ(e,h)=values[e−1]。
9. seasonal_naive — ŷ(e,h)=values[e+h−1−52]。
10. rolling_mean_4 — ŷ(e,h)=mean(values[e−4:e])。
baseline PI：历史最近 52 周一步残差（对 last_value：values[i−1] vs values[i]；seasonal_naive 与
rolling_mean_4 同理）的经验 2.5%/97.5% 分位数 ×√h，截断 ≥0。

## 指标

对全部配对、以及分 horizon、分方法计算：MAE, RMSE, MSE, WAPE,
agreement_pct = max(0, 1−WAPE)×100（注明：不是分类 Accuracy）, sMAPE,
MASE（scaling Q_e = mean(|values[i]−values[i−52]|), i∈[52, e−1]，按 origin 计算后取窗口均值作分母）,
95% PI coverage, mean interval width,
对论文 baseline（paper_arima）的 skill score = (Baseline−Model)/Baseline×100（MAE 与 MSE），
以及对最优 baseline（last_value/seasonal_naive/rolling_mean_4 中 test WAPE 最低者）的相对改善率。

## 输出文件（写到 out/，每个文件附 sha256 清单）

- rolling_origin_predictions.csv（长表）:
  origin_index,origin_week,horizon,target_index,target_week,actual,model,yhat,lower,upper,fallback
- paired_comparison.csv（宽表）: origin_index,horizon,target_index,target_week,actual, 每模型一列 yhat
  以及每模型 interval_hit 列。
- metrics_by_model_and_horizon.csv
- metrics_overall.json（含 above all、skill scores、改善率、exactly_once 账本、split 定义、数据 sha256）
- qwen_grid.csv、ensemble_grid.csv（验证集选择过程全记录）
- leakage_and_sentinel_results.json：
  * future-sentinel：把 values[e:] 改成 999999 后 origin=e 的所有模型预测逐位一致（对若干抽样 origin 验证；
    注意 baseline/paper 方法同理）。
  * split_integrity：断言任何方法在 origin e 不读取索引 ≥ e 的值（通过 sentinel 即可证明，另加代码注释断言）。
  * no_zero_fill：断言数据无缺失且未填 0。
- parameter_and_seed_manifest.json：所有模型参数、网格、种子、代码版本(git 或目录 sha)、依赖版本
  (numpy/scipy/statsmodels 版本)。
- determinism：整个实验 main 跑两遍，比较 rolling_origin_predictions.csv 的 sha256 一致，
  结果写入 leakage_and_sentinel_results.json.determinism。

## 测试（写到 tests/，pytest 可跑）

- test_determinism.py：小规模重跑关键方法两次结果一致。
- test_no_leakage_sentinel.py：future-sentinel。
- test_paired_samples.py：paired_comparison 中所有模型列在同一 (origin,horizon) 集合上无缺失（fallback 除外需标记）。
- test_metrics_recompute.py：从 paired_comparison.csv 重算 WAPE/MAE 与 metrics 文件一致。
- test_exactly_once.py：协议对象账本断言。

## 硬性要求

- 完全确定性：禁止无种子随机；禁止 wall-clock 进入任何输出。
- 每个 origin 的预测只能用 values[:e]；论文方法只能用 values[e−11:e]。
- 代码 typed、清晰、带 docstring；ruff (line-length 100) 与 mypy --strict 尽量通过
  （第三方 stub 缺失可用 # type: ignore 精确豁免并注释原因）。
- 运行入口：src/run_experiment.py，执行
  F:/作业/aibola/.venv/Scripts/python.exe src/run_experiment.py 完成全部输出。
- 最后把运行结果摘要（各模型 test WAPE/MAE/coverage、选择到的参数与权重、sentinel 结果）
  打印为 JSON 到 stdout。
