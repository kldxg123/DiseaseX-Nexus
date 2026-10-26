# SPEC — KG_PARAMETER_TUNING_AND_CONTROLLED_SELF_LEARNING_DEMO (vm201 openclaw)

目标：在既有 KG 双闭环之上，为 AUT/legionella/legionnaires_reported_cases/month（240 个月观测，
2005-01-01..2024-12-01，来源 epidemic.dashboard_history_series_v2, latest_rank=1）建立
"数据画像→KG推荐→参数空间→时序切分→有界调参→最优选择→H1–H4预测→实际值反馈→性能账本→受控重训"
全闭环。全程 Shadow，禁止 promotion / active pointer / Trusted 变更。

## 0. 既有事实（已只读核验）

- Git HEAD e585652cfc5af030b0a099c3afca4a47b2840f34；Alembic head = 0040_kg_dual_loop_feedback。
- AUT 推荐 recommendation_id=c219de41-ea2e-58d9-86cd-339b03213401，rank1=negbin_dglm.v1（score 80.88）。
- 既有闭环 champion：seasonal_naive（WAPE 0.3067），KG 模型 EXECUTED_NOT_CHAMPION。
- 执行适配器在 src/epidemic_core/kg_r3c/execution.py：
  - fit_model(adapter_id, rows, parameters, seed, cutoff, frequency) / predict_model(fitted, horizons, issue_period)
  - negbin_dglm.v1 参数：discount∈[0.8,0.999] default 0.95；shape∈[0.5,500] default 10.0；
    prior_shape∈[0.01,100] default 1.0；prior_rate∈[0.01,100] default 1.0（代码内 clip）。
  - BASELINE_IDS = last_value, rolling_mean(window=4), drift, seasonal_naive(season_lag=12 month)。
  - 全部确定性（stdlib math），seed 只参与 hash；HORIZONS=[1,2,3,4]；区间 0.95。
- 既有闭环机器在 src/epidemic_core/forecast_model_kg/dual_loop.py（execute_shadow_run/reconcile_feedback/
  select_champion common-sample WAPE/period_add/dataset_sha256/load_series）。复用其 period_add、stable_uuid、
  sha256_json、TrainingRow、fit_model、predict_model、BASELINE_IDS。
- Dashboard：uvicorn epidemic_core.dashboard.app:create_app @127.0.0.1:8020；cookie 会话鉴权
  （_authorized/_unauthorized，401 JSON）；KG 页面 /forecast-model-knowledge-graph 的 HTML 在
  forecast_model_kg/dashboard_app.py（KG_HTML），JS 在 dashboard/static/forecast-model-kg.js（IIFE 模式，
  fetch credentials:same-origin，手绘 SVG 图表，中文 label 字典），路由安装器在 dashboard/app.py:747-753。
  写接口模式：Idempotency-Key 头 + epidemic_kg.kg_api_audit_log（endpoint+key 唯一）回放。
- 写库连接：kg_r3c/dashboard_app.py 的 write_conn()（unix socket trust，user epidemic_app）。
  迁移：MIGRATION_DATABASE_URL，本地可用 socket trust：postgresql+psycopg://REDACTED@/epidemic_openclaw?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432
- readyz：p1_10/diagnostics.py readiness()，migration 检查 = 代码唯一 head ∈ epidemic_preview.runtime_readiness_v1.alembic_revision。
  部署 0041 后须把 marker（environment='production' 行）更新为 0041_kg_parameter_tuning。
- 测试：pytest 8.3.5（PYTHONPATH=runtime/python-packages:src，/usr/bin/python3 = 3.10.12）；
  ruff 0.16.1、mypy 在 runtime/python-packages（目录形式，用 PYTHONPATH=runtime/python-packages python3 -m ruff / -m mypy）。
- Python 3.10：可用 X | None、zip(strict=False)。

## 1. 迁移 0041_kg_parameter_tuning（epidemic_kg schema，raw SQL op.execute，IF NOT EXISTS）

revision="0041_kg_parameter_tuning", down_revision="0040_kg_dual_loop_feedback"。
风格对齐 0039/0040。downgrade：按 FK 逆序 drop 新表。

表（全部 created_at timestamptz default now()；所有 id uuid pk 默认由代码生成 stable_uuid）：

1. kg_pt_demo_snapshots:
   snapshot_id uuid pk, demo_key text not null, country text not null, disease text not null,
   indicator text not null, frequency text not null, source_table text not null,
   n_obs int not null, first_period date not null, last_period date not null, data_through date not null,
   revision_max int not null, observation_keys jsonb not null, missing_periods jsonb not null,
   missing_semantics text not null, dataset_sha256 text not null, snapshot_sha256 text not null unique,
   unique(demo_key, snapshot_sha256)
2. kg_pt_demo_snapshot_rows（冻结行；只授 select/insert，不授 update/delete → DB 级不可变）:
   snapshot_id uuid not null references kg_pt_demo_snapshots, row_index int not null,
   period_start date not null, period_end date, value_numeric numeric, observation_id text,
   revision int not null, row_sha256 text not null, pk(snapshot_id, row_index)
3. kg_pt_temporal_splits:
   split_id uuid pk, snapshot_sha256 text not null references kg_pt_demo_snapshots(snapshot_sha256),
   train_start date, train_end date not null, validation_start date not null, validation_end date not null,
   test_start date not null, test_end date not null, train_rows int not null, validation_rows int not null,
   test_rows int not null, split_sha256 text not null unique
4. kg_pt_parameter_contracts:
   contract_id uuid pk, adapter_id text not null, parameter_name text not null, data_type text not null,
   default_value numeric, lower_bound numeric, upper_bound numeric, discrete_candidates jsonb,
   transform text not null default 'none', parameter_source text not null
     check (parameter_source in ('PAPER_EXTRACTED','IMPLEMENTATION_DEFAULT','DATA_PROFILE_RULE','OPTIMIZER_SELECTED')),
   evidence_locator text, code_source text, applicable_profile jsonb not null default '{}',
   missing_reason text, safety_constraint text, tunable boolean not null default false,
   contract_sha256 text not null unique, unique(adapter_id, parameter_name, contract_sha256)
5. kg_pt_tuning_runs:
   tuning_run_id uuid pk, shadow_run_id uuid references epidemic_kg.kg_forecast_shadow_runs(run_id),
   recommendation_id uuid, snapshot_sha256 text not null, split_sha256 text not null, adapter_id text not null,
   cycle_index int not null default 1, seed int not null, max_trials int not null, max_seconds int not null,
   objective text not null, code_version text not null, status text not null default 'RUNNING',
   idempotency_key text not null unique, best_trial_id uuid, started_at timestamptz, completed_at timestamptz
6. kg_pt_tuning_trials:
   trial_id uuid pk, tuning_run_id uuid not null references kg_pt_tuning_runs, trial_index int not null,
   params jsonb not null, params_sha256 text not null, parameter_source jsonb not null,
   n_origins int not null, n_labeled int not null, common_wape numeric, per_horizon_metrics jsonb,
   coverage numeric, avg_interval_width numeric, complexity_penalty numeric, objective_value numeric,
   gates jsonb, gate_pass boolean, failure_reason text, elapsed_ms int, artifact_sha256 text,
   code_version text not null, dataset_snapshot_sha256 text not null, trial_sha256 text not null unique,
   unique(tuning_run_id, trial_index)
7. kg_pt_trial_origins（每个 origin 的审计行）:
   origin_id uuid pk, trial_id uuid not null references kg_pt_tuning_trials, origin_index int not null,
   origin_period date not null, issue_period date not null, data_through date not null,
   feature_max_period date not null, label_periods jsonb not null, train_row_count int not null,
   validation_row_count int not null, dataset_sha256 text not null, seed int not null,
   artifact_sha256 text not null, origin_sha256 text not null, unique(trial_id, origin_index)
8. kg_pt_test_evaluations（独立测试只评一次：unique(tuning_run_id, adapter_id)）:
   eval_id uuid pk, tuning_run_id uuid not null references kg_pt_tuning_runs, adapter_id text not null,
   is_tuned_model boolean not null, params jsonb not null default '{}', params_sha256 text not null,
   split_sha256 text not null, n_origins int not null, per_horizon_metrics jsonb not null,
   mae numeric, rmse numeric, wape numeric, smape numeric, mase numeric, coverage numeric,
   avg_interval_width numeric, agreement_pct numeric, eval_sha256 text not null unique,
   unique(tuning_run_id, adapter_id)

索引：各表 (tuning_run_id)/(trial_id)/(snapshot_sha256) 常规索引。
Grants（对齐 0040）：
- grant select on all tables in schema epidemic_kg to epidemic_dashboard_ro;（迁移末尾重跑 blanket）
- grant select, insert on 8 张新表 to epidemic_app;
- grant update on kg_pt_tuning_runs(status, completed_at, best_trial_id) to epidemic_app;
- kg_pt_demo_snapshot_rows 只 grant select, insert（不可变证据）。

## 2. 新包 src/epidemic_core/kg_pt/

PT_IMPL_VERSION = "kg_pt.v1"; PT_SEED = 20260811
DEMO_KEY = "AUT:legionella:legionnaires_reported_cases:month"
TRAIN_N=168, VAL_N=36, TEST_N=36
TUNING_OFFSETS = [48,44,...,4]（12 个 origin，相对于 validation_end idx）
TEST_OFFSETS = [36,32,...,4]（9 个 origin，相对于 test_end idx）
MAX_TRIALS=24, MAX_SECONDS=600, MIN_VALID_FOLDS=8, COVERAGE_FLOOR=0.5,
CHAMPION_MIN_IMPROVEMENT=0.01（相对最佳基线 test common WAPE 至少改善 1% 才可夺冠）
RETRAIN_MIN_MATURED=12

### contracts.py（纯）
ParameterContract dataclass（字段与表 4 对齐）。
NEGBIN_CONTRACTS：discount/shape/prior_shape/prior_rate，全部 parameter_source=IMPLEMENTATION_DEFAULT，
evidence_locator=null，missing_reason='NO_PAPER_EVIDENCE_IN_KG'，
code_source='src/epidemic_core/kg_r3c/execution.py:217-220'，
safety_constraint=clip 区间文字，tunable=True，transform：discount=none、shape/prior=log。
另有 seasonal_naive season_lag=12（DATA_PROFILE_RULE，tunable=False，applicable_profile={'frequency':'month'}）。
functions: contracts_for(adapter_id, frequency), contract_sha256(list),
sample_params(contracts, rng) -> dict（log 变换采样）, clip_params(contracts, params)。

### split.py（纯）
TemporalSplit dataclass（索引+日期）。make_split(periods, train_n, val_n, test_n)：校验
train_end < validation_start <= validation_end < test_start，不足则 raise SPLIT_INFEASIBLE。
split_sha256。tuning_origin_indices(n_rows, split) -> list[int]：origin_idx = (train_n+val_n-1) - offset，
要求 origin_idx >= 59 且 origin_idx+4 <= train_n+val_n-1。test_origin_indices 同理相对 n_rows-1。
返回每个 origin 的 (origin_index, origin_period, issue_period, data_through, feature_max_period, label_periods)。
硬校验：feature_max_period == origin_period <= origin（即 label_periods[0] 之前）。

### metrics.py（纯）
mase_denominator(train_values, season_lag=12)：仅由训练切片计算（mean |y_t - y_{t-12}|，不足则 naive-1）。
cell_metrics(pairs) -> {mae, rmse, wape, smape, mase, coverage, avg_width, n}（pairs=(yhat, actual, lo, hi)，
跳过 yhat/actual None）。common_cells(preds_a, preds_b) 共有 (origin,horizon) 键。
wape_common(cells, pred_map, actual_map)。agreement_pct(wape)=max(0,1-wape)*100。
一律不允许 NaN/Inf（math.isfinite 校验，违规计入 gate 失败）。

### tuner.py（纯；只接收 train+validation 切片，永远看不到 test 行）
run_tuning(trainval_rows, split, adapter_id, contracts, seed, max_trials, max_seconds, frequency)
-> TuningResult{trials: [TrialResult], best_trial_index}
TrialResult: trial_index, params, params_sha256, parameter_source(每参数来源 map), origins:[OriginRecord],
per_horizon_metrics, common_wape（全部 origin×horizon 标注单元上的 WAPE）, coverage, avg_interval_width,
complexity_penalty, objective_value, gates{non_negative, finite, min_folds, coverage_floor}, gate_pass,
failure_reason, elapsed_ms, artifact_sha256（逐 origin 拟合的 artifact sha 列表的聚合 sha）,
dataset_snapshot_sha256（由 trainval_rows 计算）。
trial 0 = 默认值（调参前基线）。trial 1..N = random.Random(seed) 采样。
objective = common_wape * (1 + 0.001 * Σ z_i)，z_i = 参数在 [lower,upper] 内相对 default 的归一化偏差。
gate 失败 trial 不参与 best；全失败 → best=None, status FAILED_ALL_TRIALS。
单 trial 异常 → failure_reason 记录并继续（恢复：同一 seed/inputs 重跑产生逐字节相同 trials）。
超时：每 trial 前检查 elapsed > max_seconds 则停止（记录 stopped_early）。

### pipeline.py（DB 编排；所有写 ON CONFLICT DO NOTHING + stable_uuid）
- ensure_demo_snapshot(conn) -> snapshot dict：从 epidemic.dashboard_history_series_v2 读 240 行，
  冻结进表 1/2；snapshot_sha256 = sha256_json(冻结行); missing_periods=[]; 
  missing_semantics='MISSING_VALUE_KEPT_NULL_NEVER_IMPUTED'; data_through=last_period。
  已存在同 sha → 直接复用（幂等）。绝不写 trusted。
- ensure_split / ensure_contracts：内容寻址幂等 upsert。
- execute_pt_demo(conn, actor, idempotency_key) -> dict：
  cycle 1：
  1) snapshot+split+contracts；
  2) tuning run（status RUNNING→COMPLETED；persist trials+origins）；
  3) best trial = 最优参数（OPTIMIZER_SELECTED，evidence=trial_id）；
  4) 建 shadow run 行（kg_forecast_shadow_runs，run_key='pt-demo:{DEMO_KEY}:{snapshot_sha[:12]}:c1',
     mode='SHADOW', promotion_eligible=false, active_pointer_changed=false, trusted_rows_changed=0）；
  5) tuned 模型 + 4 baselines 在 validation origins 上回测 → 复用 0040 表
     kg_shadow_backtest_folds/predictions/model_metrics（MASE 分母必须 per-origin 仅训练切片）；
  6) test-window 预测：test origins，fit rows[:origin+1]，predict H1–H4，actual=NULL 存
     kg_forecast_shadow_values(FUTURE-like, properties.feedback_maturity='AWAITING_ACTUAL') +
     kg_forecast_objective_feedback AWAITING 行；此阶段不读 test 标签；
  7) 单次测试评估：同一函数内先把 5 个模型全部 test 预测存完，再一次 join 冻结快照的 test 标签，
     写入 kg_pt_test_evaluations（5 行；unique 防重复评估）；同时把 AWAITING 反馈行 mature
     （新行 kind='DEMO_HISTORICAL_REPLAY'，actual_available_at=now，含 interval_hit/abs_error）；
  8) champion 决策：tuned test common WAPE vs 最佳 baseline（相同 origin×horizon 单元）；
     需 ≥1% 改善且 gate 全过才 KG_RECOMMENDED_MODEL，否则 EMPIRICAL_BASELINE_OVERRIDE；
     kg_learning_cycles(cycle_type='PARAMETER_TUNING_EVALUATION', evidence=jsonb 含 replay 标注) +
     kg_champion_decisions(promotion_applied=false)；
  9) 未来 H1–H4：champion 在全 240 行拟合，issue_period=2024-12-01，预测 2025-01..04，
     AWAITING_ACTUAL（真实未知）。
  cycle 2（受控重训，历史回放触发）：
  10) 重训触发评估：newly_matured=36 ≥ RETRAIN_MIN_MATURED → 触发（同时记录 revision/drift/性能下降各项判定）；
  11) 新 snapshot v2（同 240 行但 data_through=2024-12-01 语义：全部已成熟；若行集合相同则 snapshot_sha 相同，
      直接复用并在 evidence 注明）+ 新 split（train=0..203, val=204..239, test=空——demo 末端，如实记录
      'NO_REMAINING_HELDOUT'）；
  12) 第二个 tuning run（cycle_index=2，同 seed 策略）在 train=0..203/val=204..239 上调参；
  13) Challenger(cycle2 best) vs 当前 Champion 在同一窗口（origins rel idx 239, offsets 48..4）共同评估；
  14) kg_learning_cycles(cycle_type='CONTROLLED_RETRAIN_HISTORICAL_REPLAY') + champion decision
      （promotion_applied=false；若挑战者未显著更优则 champion 留任，reason 如实）。
  幂等：execute_pt_demo 先查 kg_pt_tuning_runs.idempotency_key；存在 → {'status':'IDEMPOTENT_REPLAY', ...既有 ids}。
- reconcile / ledger 聚合查询函数供 API 使用。

### api.py
install_pt_routes(app, engine, authorized, unauthorized, json_response)：
- GET  /api/v1/kg/pt/demo/overview?demo_key — 快照/切分/KG推荐+理由/参数合同/最优参数/调参前后验证指标/
  独立测试指标/基线对比/champion/反馈成熟进度/learning cycles/限制说明（聚合 JSON；无数据时 404 PT_DEMO_NOT_FOUND）
- GET  /api/v1/kg/pt/demo/trials?tuning_run_id — 全部 trial（含失败）
- GET  /api/v1/kg/pt/demo/forecast?run_id — test 窗口逐 origin H1–H4 预测 vs 实际 + 未来 H1–H4 + 历史序列（图表）
- POST /api/v1/kg/pt/demo/run — 头 Idempotency-Key 必填；body {"demo_key": ...}；
  pg_advisory_xact_lock(hashtext(idem)) 串行化 + audit log 回放 → 8 路并发同 key 只有一个语义 run；
  返回显式 run ids；未认证 401。
KG 推荐理由：从 kg_recommendations/kg_recommendation_components/kg_preexecution_* 读真实评分组件，
overview 里输出中文理由（模型为何被推荐、哪些参数来自论文=无、哪些默认值、哪些可调、数据画像约束）。

### Dashboard 区块（KG 页内，不新建页面）
forecast_model_kg/dashboard_app.py 的 KG_HTML 在 #kg-dual-loop 之后加 <section id="kg-param-loop">：
13 个要素：选择器回显/时间轴 SVG（train/val/test 三段着色）/KG 推荐+中文理由/参数表（参数名·原默认·
搜索范围·最优值·来源）/调参过程 SVG（trial index vs common WAPE）/调参前后+基线指标表（MAE RMSE WAPE
sMAPE MASE 覆盖率 区间宽 相对基线改善）/独立测试预测吻合度（紧邻免责声明："该数值是基于WAPE换算的易读指标，
不是分类准确率，也不代表未来保证准确。"）/H1–H4 实际 vs 预测区间 SVG/当前 Champion/胜负原因/反馈成熟进度条/
learning cycle 时间线/Shadow+历史回放+非医疗结论警告。
forecast-model-kg.js 加第 4 个 IIFE（guard if(!root)return）；forecast-model-kg.css 加样式
（表格 table-layout:fixed + word-break，长文本 <details> 折叠，禁止横向溢出）。

## 3. 测试 tests/kg_pt/

纯单测（无 DB）：test_contracts.py（schema/边界/来源合法性/无论文证据不得标 PAPER_EXTRACTED）；
test_split.py（严格时序/非法切分 raise）；test_origins.py（feature_max_period<=origin，标签在窗口内）；
test_metrics.py（含缺失/NaN 防护/MASE 仅训练切片）；test_tuner.py（复现性：同 seed 两次逐字节相同；
失败 trial 恢复；超时中断；gate 生效；default trial 0 存在）；
test_sentinels.py（5 个哨兵，真实执行 tuner/pipeline 纯函数）：
 1. test-label：篡改 test 标签 → tuning 结果 sha 不变（tuner 只收 trainval 切片，结构性+行为性双重断言）
 2. future-observation：篡改 origin 之后观测 → train/artifact/预测 sha 不变
 3. parameter-selection：选择函数不接触 test 指标；投毒 test 评估 → 选中 trial 不变
 4. snapshot immutability：同输入重冻结 sha 相同；（DB 集成：epidemic_app UPDATE snapshot_rows → 42501）
 5. preprocessing leakage：篡改 post-origin 值 → 该 origin 的 MASE 分母/artifact 不变
集成测试 test_pt_db.py（pytest.mark.integration，socket DSN）：迁移表存在；execute_pt_demo 两次 →
IDEMPOTENT_REPLAY 同 ids；8 线程并发 → 单 run；反馈 36 条 MATURED(DEMO_HISTORICAL_REPLAY) +
4 条 AWAITING（未来）；2 个 learning cycles + champion decisions promotion_applied=false；
trusted 计数不变（366082 normalized_observations / 295 kg_recommendations / 3 kg_graph_versions /
7 forecast_runs）；kg_pt_test_evaluations 恰 5 行/调参 run 且重复执行不增。
test_pt_api.py：TestClient + 临时会话 cookie：未认证 401；overview/trials/forecast 200；
POST 幂等回放头 Idempotent-Replay。

## 4. 部署与验收（后续阶段）

- scp 推送新文件+修改文件 → 远端 pytest（纯+集成）、ruff、mypy（touched modules strict）、pip check。
- alembic upgrade head（socket migrator DSN）→ 更新 runtime_readiness_v1 marker 为 0041 → readyz 200。
- 重启 dashboard（kill PID + nohup start_vm201_dashboard.sh），curl 冒烟 + 8 路并发 curl 幂等。
- 浏览器验收（本地 Playwright + ssh tunnel）：console/pageerror/failed-request 全空。
- clean clone（git clone 本地路径到 /tmp）+ 空库 createdb + alembic upgrade head + 最小闭环冒烟。
- 提交：把既有未跟踪 KG 基础文件（0039/kg_r3c/tests 等，上轮遗留）与本轮新增一起 commit（本地，不 push，
  不 amend/reset），使 clean clone 可复现。
- 报告 reports/kg_parameter_tuning_self_learning_demo_<UTC>/（22 文件 + MANIFEST.sha256）zip 拉本地。
