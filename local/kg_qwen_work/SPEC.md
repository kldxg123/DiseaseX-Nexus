# KG_QWEN_AUTONOMOUS_TUNING_AND_FEEDBACK_V1 — 设计合同 (SPEC)

本文件是本轮实现的唯一权威设计依据。目标：在 vm201 `USER_HOME/projects/openclaw`
平台上建立 Qwen 参与的自动调优 + 受控自学习闭环（Shadow only）。

## 0. 现场核验结论（2026-08-11，只读核验）

- Git HEAD = `205fb5f`（上轮结束态），dirty：仅未跟踪 `experiments/`（既有）。
- Alembic head = `0041_kg_parameter_tuning`；库 `epidemic_openclaw`，schema `epidemic_kg`。
- kg_pt 表 8 张存在；快照 AUT/legionella/month 240 obs 2005-01→2024-12，
  snapshot_sha256 前缀 `201b2add`；上轮 tuned 测试 WAPE 0.413637，seasonal_naive 0.306911
  （champion=seasonal_naive）——该测试窗已被查看，本轮不得再称其为 fresh。
- Dashboard：uvicorn 127.0.0.1:8020 运行中（runtime/dashboard.pid）。
- **Qwen/vLLM 实际不存在于本机**：`scripts/qwen_vllm/*` 指向已不存在的
  `USER_HOME/autodl-tmp`（另一台 autodl 机器）；无 vllm、无模型权重。
  GPU=Quadro RTX 4000 8GB，其中 ~3.6GB 被无关任务 guppy_basecall_server 常驻
  （不得结束）。Ebola venv（python3.13, torch 2.12.0+cu130, transformers 5.8.1,
  accelerate, modelscope）存在但 CUDA 驱动过旧 → **CPU 推理**。
- 决策：使用 ModelScope 官方源下载 **Qwen3-4B-Instruct-2507**（Apache-2.0，免费，
  固定文件清单+sha256 校验）到 `USER_HOME/models/Qwen3-4B-Instruct-2507`，
  用 Ebola venv 固定版本 torch/transformers 做 **CPU 推理**（62GB RAM 足够）。
  这偏离任务书"现有 vLLM"的字面前提（该前提在现场不成立），在报告中如实说明。
- **OpenClaw**：本机无独立 OpenClaw agent 框架；openclaw 即本平台代码库本身。
  OpenClaw=编排层（本平台 epidemic_core），Qwen=方案生成模型，两者职责分离实现。
- 备份：`runtime/kg_qwen_backup_20260811T081520Z/pre_kg_qwen.dump`，
  sha256 `416c4a5b5debf050fcc50a2f41de9052f296809cf0f4dc3f3bd535af43fec8ab`，
  pg_restore --list = 1448 项。
- Track B 候选：**FIN / hantavirus / puumala_virus_reports / week**，445 obs，
  2018-01-01→2026-07-06，source `epidemic.dashboard_history_series_v2`，
  从未参与任何调参。

## 1. 双轨道测试集治理

### Track A（历史回放演示轨道）
- AUT/legionella/month，240 obs。train 168 / val 36 / test 36（与上轮一致）。
- 标记 `REUSED_HISTORICAL_BENCHMARK` + `NOT_FRESH_HELDOUT`。
- 仅用于机制演示与嵌套 rolling-origin 优化；其测试窗指标不得称为前瞻准确率。

### Track B（独立认证轨道）
- FIN/hantavirus/puumala_virus_reports/week，445 obs。
- 冻结快照后：**调优窗 = 前 393 周**（2018-01-01→2025-06-30），
  **hidden eval = 最后 52 周**（2025-07-07→2026-07-06）。
- 在任何 Qwen 调用与任何调参之前锁定 hidden eval 窗口
  （kg_qwen_evaluation_sets 行，identity=FRESH_HELDOUT_LOCKED，locked_at）。
- Qwen 上下文、调参器、验证摘要中**结构性不含**该 52 周标签/指标。
- 方案冻结后 hidden eval **只评估一次**（eval_count 0→1，DB 唯一约束保证）。

## 2. 优化目标与门禁

- 目标：validation WAPE ≤ 0.30 且 agreement_pct = max(0,1-WAPE)×100 ≥ 70。
- 门禁：interval empirical coverage ≥ 0.85；有效 rolling origins ≥ MIN_VALID_FOLDS(=8)；
  H1–H4 均有有效指标；预测非负且有限；候选相对最佳基线 common-WAPE 改善 ≥ 1%。
- 70% 是优化目标而非保证。预算耗尽未达标 → `AUTOTUNING_TARGET_NOT_MET_WITHIN_BUDGET`。
- 预算：≤3 轮、每轮 ≤50 trials、总 ≤45 分钟、单 trial 超时 120s、固定 seed=20260811。

## 3. 新包 `src/epidemic_core/kg_qwen/`（纯 stdlib，无 DB import 副作用）

与 kg_pt 相同的工程纪律：DB 访问只在 pipeline；所有随机性经 `random.Random(seed)`；
重放同 seed+同输入 → 字节级相同内容哈希（elapsed_ms 不入哈希）。

### 3.1 `__init__.py` 常量
```
KQ_IMPL_VERSION = "kg_qwen.v1"
KQ_SEED = 20260811
TARGET_WAPE = 0.30
TARGET_AGREEMENT_PCT = 70.0
COVERAGE_MIN = 0.85
BASELINE_IMPROVEMENT_MIN = 0.01
MAX_ROUNDS = 3
MAX_TRIALS_PER_ROUND = 50
MAX_TOTAL_MINUTES = 45
TRIAL_TIMEOUT_SECONDS = 120
MIN_VALID_FOLDS = 8
HORIZONS = [1,2,3,4]
TRACK_A_KEY = "AUT:legionella:legionnaires_reported_cases:month"
TRACK_B_KEY = "FIN:hantavirus:puumala_virus_reports:week"
TRACK_B_TUNING_WEEKS = 393
TRACK_B_HIDDEN_WEEKS = 52
QWEN_MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
QWEN_MODEL_DIR = "USER_HOME/models/Qwen3-4B-Instruct-2507"
QWEN_WORKER_PYTHON = "USER_HOME/Ebola/venv/bin/python"
QWEN_RUNTIME_LABEL = "transformers-5.8.1+torch-2.12.0-cpu"
REPLAY_KIND = "DEMO_HISTORICAL_REPLAY"
```

### 3.2 `adapters_ext.py` — 4 个新白名单候选模型

注册进 execution 调度：通过 `kg_qwen.adapters_ext.register_extended_models()` 把
fit/predict 注入 `kg_r3c.execution.MODEL_FITS/MODEL_PREDICTS`（additive，不改基线）。
签名与 execution 完全一致：`fit(rows, parameters, seed, cutoff) -> FittedModelArtifact`，
`predict(fitted, horizons, issue_period) -> list[dict]`（键同 execution：
horizon/yhat/lower_bound/upper_bound/interval_level/interval_method）。
缺失观测：训练跳过（不填充）；预测所需 lag 为 None → NULL 预测。
纯 stdlib 线性代数（高斯消元带部分主元）。

1. **seasonal_negbin_glm.v1** — log-link NB GLM：截距 + 季节分类效应
   （month→11 哑变量；week→51 哑变量，基准类省略）+ 可选线性趋势。
   IRLS 拟合均值，shape 由参数给定（不调均值-方差联立）。
   参数：shape(log,0.5–500, default 10)、trend(discrete [0,1], default 0)、
   ridge(log,1e-6–10, default 1e-3)。区间：plug-in NB quantiles（复用 nb_quantiles）。
2. **fourier_negbin_glm.v1** — 截距 + K 对 Fourier（period=12 或 52，按 frequency）
   + 可选趋势 + ridge。参数：K(discrete [1,2,3], default 2)、trend[0,1]、
   ridge(log)、shape(log)。同样 IRLS + NB quantiles。
3. **lagged_count_boosting.v1** — 梯度提升 stumps（depth≤1），Poisson deviance
   loss on log-scale（预测 log1p(y) 的 MSE 提升，输出 expm1 截断 ≥0）。
   特征：lag 1/2/3/6/12 + rolling_mean 3/6/12（全部仅用 origin 及之前数据）。
   参数：n_estimators(int,10–100, default 50)、learning_rate(log,0.01–0.5,
   default 0.1)、min_samples_leaf(int,5–30, default 10)。固定 seed。
   多步预测：递归（预测值回填 lag/rolling 特征）；特征缺失 → NULL。
   区间：in-sample 1-step 残差经验分位数 × sqrt(h)（EMPIRICAL_RESIDUAL 方法）。
4. **seasonal_ensemble.v1** — weight × seasonal_naive + (1-weight) × 最佳计数模型
   （component adapter 由参数 component_adapter 指定，离散候选
   [negbin_dglm.v1, seasonal_negbin_glm.v1, fourier_negbin_glm.v1]）。
   参数：weight(0–1, default 0.5，仅验证集选择)、component_adapter(discrete)。
   fit 内部对两个组件分别 fit；预测 yhat/区间为加权组合；组件 NULL → 退化为另一组件。

每个模型 artifact state 记录全部参数与拟合统计，artifact_sha 经 make_artifact。

### 3.3 `contracts_ext.py` — 新模型参数合同

沿用 kg_pt.contracts.ParameterContract。所有参数 source 只能标
IMPLEMENTATION_DEFAULT / DATA_PROFILE_RULE（KG 无论文证据，禁止 PAPER_EXTRACTED）。
`all_contracts_for(adapter_id, frequency)` 覆盖 4 新模型 + 复用 kg_pt 的
negbin_dglm.v1/seasonal_naive 合同。WHITELIST_ADAPTERS =
{negbin_dglm.v1, seasonal_negbin_glm.v1, fourier_negbin_glm.v1,
lagged_count_boosting.v1, seasonal_ensemble.v1} + 基线 4 个（不作为调优候选，
但永远参与比较）。

### 3.4 `plan_contract.py` — Qwen 输出合同 + 确定性校验器

- `PLAN_JSON_SCHEMA`（手写校验，无 jsonschema 依赖）：
  顶层必填 plan_version(=="v1"), dataset_snapshot_sha256(str), objective{…},
  candidate_models(list[str]), parameter_spaces(list[object]), feature_recipes(list),
  max_rounds(int), max_trials_per_round(int), max_total_minutes(number),
  rationale_zh(list[str]), kg_evidence_ids(list[str]), known_risks(list[str]),
  stop_conditions(list[str])。
- parameter_spaces[i] 必填：adapter_id, parameter_name,
  lower/upper（数值）或 values（离散候选），transform ∈ {none,log},
  parameter_source ∈ {IMPLEMENTATION_DEFAULT, DATA_PROFILE_RULE, OPTIMIZER_SELECTED},
  rationale(str, 为什么适合当前数据), evidence_ref(str, KG evidence 或代码来源)。
- `validate_plan(plan, registry) -> list[PlanError]`；PlanError(code, message, path)。
  错误码：PLAN_SCHEMA_INVALID / PLAN_UNREGISTERED_MODEL / PLAN_UNREGISTERED_PARAMETER /
  PLAN_BOUND_VIOLATION（范围越出合同）/ PLAN_DISCRETE_VIOLATION /
  PLAN_FORBIDDEN_CONTENT / PLAN_TEST_LABEL_REFERENCE / PLAN_BUDGET_VIOLATION /
  PLAN_SNAPSHOT_MISMATCH。
- FORBIDDEN 模式（对 adapter_id/parameter_name/transform/source/evidence_ref 等
  结构化字段做全匹配白名单；对 rationale/rationale_zh/known_risks/stop_conditions
  自由文本做正则扫描）：SQL 关键字（select|insert|update|delete|drop|alter|grant
  + from/where 组合）、shell（; && || ` $() | > < chmod sudo rm ）、
  路径（/、\、~、.py、.sh）、URL（http://、https://、www.）、
  promotion/approve/active.?pointer、test.?label、ignore (all )?previous instructions、
  "忽略之前/以上指令" 等注入语句。
- 预算字段 > SPEC 上限 → PLAN_BUDGET_VIOLATION（拒绝而非静默截断；
  执行层再防御性 clamp）。
- dataset_snapshot_sha256 必须等于当前冻结快照 sha → 否则 PLAN_SNAPSHOT_MISMATCH。
- `PLAN_VALIDATOR_VERSION = "kq_plan_validator.v1"`，校验结果入
  kg_qwen_tuning_plans.validation_status/validation_errors。

### 3.5 `qwen_client.py` + `qwen_worker.py` — 本地 Qwen 推理

- worker（独立脚本，由 Ebola venv python 执行）：
  加载 QWEN_MODEL_DIR（local_files_only，torch_dtype=bfloat16，device_map=cpu），
  stdin 逐行读 JSON {request_id, messages, max_new_tokens, do_sample:false}，
  stdout 逐行写 {request_id, text, prompt_tokens, completion_tokens, latency_ms,
  finish_reason}。greedy（do_sample=false）保证确定性。
- client：每轮 run 启动一次持久 worker 子进程（避免重复加载），
  调用超时 20 分钟；进程崩溃自动重启一次；最终失败 → QWEN_UNAVAILABLE。
- 模型指纹：sha256(config.json + tokenizer_config.json + index + 各 shard 前 1MB)
  记入 run（qwen_model_fingerprint）；manifest 记录 model_id、源(modelscope)、
  下载文件清单 sha256。
- prompt 模板（常量字符串，sha256 入 kg_qwen_agent_runs.prompt_template_sha256）：
  system 声明角色=疫情预测调优方案规划器、只能输出严格 JSON、禁止命令/SQL/路径；
  user 注入 sanitized context（见 3.6）+ 输出合同 JSON 骨架 + 中文说明要求。
- 响应解析：提取第一个配平 {…} JSON；解析失败/校验失败 → 重试 ≤2 次
  （附带错误码反馈让 Qwen 修正）；仍失败 → 确定性 fallback plan
  （plan_source=QWEN_FALLBACK_DETERMINISTIC，如实记录）。

### 3.6 `agent_tools.py` — forecast-tuning-agent 工具层

Agent manifest：name="forecast-tuning-agent", version, allowed_tools 恰为：
- `get_dataset_profile(track_key)` → 画像：n_obs、时间范围、缺失率、零占比、
  均值/方差/过离散比、季节强度（lag-12/52 自相关）、近期趋势；**只含调优窗统计**。
- `get_kg_model_candidates(track_key)` → KG 推荐 + 白名单候选与合同摘要。
- `get_parameter_contracts(adapter_id)` → 合同（含 source 与边界）。
- `get_experiment_history(track_key)` → 本 run 之前各轮聚合摘要（无逐行标签）。
- `get_validation_summary(run_id, round_index)` → 该轮验证聚合（per model
  WAPE/agreement/coverage/每 horizon）；**绝无 hidden eval 任何信息**。
- `submit_tuning_plan(plan_json)` → 走 3.4 校验器；返回 accepted/errors。
- `TOOL_NOT_PERMITTED`：其余一切工具名（exec/shell/file/postgres/web/approval/
  promotion…）统一拒绝并记录。
- context 构建器 `build_qwen_context(...)`：白名单字段拼装，结构性不含测试标签。
  qwen-context sentinel 直接对该函数输出做扫描。

### 3.7 `loop.py` — 多轮调优循环（纯逻辑，DB 由 pipeline 注入回调）

```
run_autotuning_loop(track, snapshot_rows, split, eval_window, qwen, persist, seed)
```
流程：round=1..MAX_ROUNDS：
1. context = agent_tools 白名单上下文（画像+KG候选+合同+历史摘要）
2. plan = qwen.propose(context)（含重试与 fallback）
3. errors = validate_plan；非空 → 记录 REJECTED，重试/fallback
4. 对每个 candidate_model 执行有界搜索：复用 kg_pt.tuner 的机制
   （origin 集合、门禁、complexity penalty、select_best_trial），
   但参数空间取自 plan（合同范围内的子空间）；trial 0 = 合同默认。
   trials 预算 = plan.max_trials_per_round（clamp 到 50）按候选数分配。
5. 汇总该轮验证指标 → persist(round, trials, plan)
6. 达标判定（目标+门禁+基线改善）→ TARGET_MET 停；
   否则把验证摘要交 Qwen 进入下一轮；预算尽 → BUDGET_EXHAUSTED。
checkpoint：每轮结束 persist 后 loop 状态可重建（pipeline 重放时跳过已完成轮）。
最终：champion 选择 = 全部轮次中 gate_pass 且 objective 最优者 vs 基线最佳，
改善 <1% → 基线保持 champion（如实记录 kg_model_status=NOT_CHAMPION）。

### 3.8 `sentinels.py` — 防泄漏哨兵（全部真实执行）

- test_label_sentinel(rows, ...)：改 hidden eval 标签 → 选中参数/trial sha 不变。
- future_observation_sentinel：改 origin 之后观测 → origin 训练 sha/预测不变。
- parameter_selection_sentinel：select 只读验证目标（结构性断言+变异测试）。
- preprocessing_leakage_sentinel：GLM/boosting 的统计量逐 origin 拟合；
  改 origin 后数据 → artifact sha 不变。
- qwen_context_sentinel：扫描 build_qwen_context 输出：不含 hidden eval 窗口任何
  标签值与指标键（递归比对数值集合+键名黑名单）。
- ensemble_weight_sentinel：改测试标签 → ensemble weight 不变。
- checkpoint_replay_sentinel：同幂等键重放 → trial 内容 sha 完全一致。
- snapshot_immutability_sentinel：同 sha 行集合不可变（重复插入不同值被拒）。
- prompt_injection_sentinel：5 个注入样本（ignore previous instructions、
  SQL、shell、路径、promotion 请求）→ 校验器全拒。
- 未注册模型/参数、越界参数 → 拒绝（单测覆盖）。

### 3.9 `pipeline.py` — DB 编排

`execute_autotuning(conn, actor, idempotency_key, track_key)`：
1. 冻结快照（复用 kg_pt.pipeline 快照机制；Track B 新建 FIN 快照）
2. Track B：锁定 hidden eval（insert kg_qwen_evaluation_sets, identity
   FRESH_HELDOUT_LOCKED, eval_count=0）——必须先于任何 Qwen/调参
3. 数据画像 + 合同 upsert（kg_pt_parameter_contracts 复用表，additive）
4. 创建 kg_qwen_agent_runs（幂等：同 idempotency_key → 返回既有 run）
5. run_autotuning_loop（qwen client 真实推理；Track A 同样流程）
6. 验证冠军 → 隐藏窗一次性评估（Track B：eval_count 原子 0→1；
   Track A：标记 REUSED/NOT_FRESH）
7. Shadow 发布：kg_forecast_shadow_runs/values（H1–H4 未来 + 回放窗，actual=NULL，
   AWAITING_ACTUAL）
8. 反馈成熟：回放窗实际值释放 → MATURED（DEMO_HISTORICAL_REPLAY）→
   性能账本聚合 → kg_learning_cycles + kg_champion_decisions
9. target_status：TARGET_MET / AUTOTUNING_TARGET_NOT_MET_WITHIN_BUDGET
所有写入仅在 epidemic_kg 的 kg_qwen_* / kg_pt_* / kg_forecast_* / kg_learning_*
/ kg_champion_* Shadow 表；不动 trusted、不 promotion、不动 active pointer。

### 3.10 `api.py` — FastAPI 路由

复用 kg_pt.api 模式（authorized/unauthorized/json_response 注入）：
- GET  /api/v1/kg/autotuning/overview?track_key=
- GET  /api/v1/kg/autotuning/plans?run_id=
- GET  /api/v1/kg/autotuning/rounds?run_id=
- GET  /api/v1/kg/autotuning/trials?run_id=&round_index=
- GET  /api/v1/kg/autotuning/evaluation?run_id=
- GET  /api/v1/kg/autotuning/feedback?run_id=
- POST /api/v1/kg/autotuning/run   {track_key} + Idempotency-Key
- POST /api/v1/kg/autotuning/reconcile {run_id} + Idempotency-Key
POST：pg_advisory_xact_lock + kg_api_audit_log 幂等回放；未认证 401。

### 3.11 migration `0042_kg_qwen_autotuning.py`（additive，epidemic_kg）

表（含 created_at 默认 now()，全部带 sha/uuid 内容寻址）：
- kg_qwen_agent_runs(run_id pk, idempotency_key unique, track_key, status,
  stop_reason, target_status, qwen_model_id, qwen_model_fingerprint,
  qwen_runtime, prompt_template_sha256, snapshot_id, snapshot_sha256, split_sha256,
  eval_window jsonb, budgets jsonb, seed, code_version, plan_validator_version,
  created_at, finished_at)
- kg_qwen_tuning_plans(plan_id pk, run_id fk, round_index, plan_source,
  plan_json jsonb, plan_sha256, prompt_sha256, response_sha256, qwen_latency_ms,
  validation_status, validation_errors jsonb, created_at, unique(run_id,round_index,plan_sha256))
- kg_qwen_tuning_rounds(round_id pk, run_id fk, round_index, status,
  trials_executed, best_adapter_id, best_trial_id, best_val_wape,
  best_val_agreement_pct, best_val_coverage, baseline_best_wape,
  improvement_vs_baseline, target_met, stop_reason, created_at, finished_at,
  unique(run_id,round_index))
- kg_qwen_trials(trial_id pk, run_id fk, round_index, trial_index, adapter_id,
  params jsonb, params_sha256, parameter_source jsonb, origin_sha256s jsonb,
  per_horizon_metrics jsonb, common_wape, agreement_pct, coverage,
  avg_interval_width, complexity_penalty, objective_value, gates jsonb, gate_pass,
  failure_reason, artifact_sha256, dataset_sha256, trial_sha256, elapsed_ms,
  created_at, unique(run_id,round_index,trial_index,adapter_id))
- kg_qwen_evaluation_sets(eval_id pk, run_id fk, track, identity, snapshot_sha256,
  window jsonb, locked_at, evaluated_at, eval_count int default 0, metrics jsonb,
  wape, agreement_pct, coverage, mae, rmse, smape, mase)
- 复用：kg_forecast_objective_feedback / kg_learning_cycles / kg_champion_decisions。
- grants：与 0041 相同的 SELECT/INSERT 授权模式（对照 0041 迁移）。

### 3.12 Dashboard

`forecast-model-kg.js` 增加 `kg-qwen-loop` 自包含区块（同 kg-param-loop 模式，
guard by #kg-qwen-loop，失败不影响其他区块）；KG_HTML 增加对应 <section>。
中文展示 SPEC 任务书 §12 全部要素；明确区分 KG证据分数/验证吻合度/独立测试
吻合度/基线吻合度；含 Shadow/历史回放/非医疗警告与 WAPE 换算免责声明。
运行按钮 POST /api/v1/kg/autotuning/run（Idempotency-Key=ui 固定键）。

## 4. 验收命令（远端）

```
cd USER_HOME/projects/openclaw
PYTHONPATH=runtime/python-packages:src python3 -m pytest tests/kg_qwen -q
PYTHONPATH=runtime/python-packages python3 -m ruff check src/epidemic_core/kg_qwen tests/kg_qwen
PYTHONPATH=runtime/python-packages:src python3 -m mypy --strict src/epidemic_core/kg_qwen
```

## 5. 硬性边界

不修改 trusted、不 promotion、不动 active pointer、不 push、不 reset/amend、
不杀无关进程（guppy）、不改 SSH/隧道配置、不输出任何密钥。

## 6. 模块间接口合同（精确签名，所有实现必须遵守）

### contracts_ext.py
```python
WHITELIST_TUNABLE_ADAPTERS: tuple[str, ...] = (
    "negbin_dglm.v1", "seasonal_negbin_glm.v1", "fourier_negbin_glm.v1",
    "lagged_count_boosting.v1", "seasonal_ensemble.v1",
)
BASELINE_ADAPTER_IDS: tuple[str, ...] = ("last_value", "rolling_mean", "drift", "seasonal_naive")
def all_contracts_for(adapter_id: str, frequency: str) -> list[ParameterContract]  # kg_pt.contracts.ParameterContract
def registered_adapters() -> list[str]  # tunable + baselines
def adapter_registry_payload() -> dict[str, Any]  # 给 Qwen 上下文的只读注册表摘要
```

### adapters_ext.py
```python
def register_extended_models() -> None  # 注入 execution.MODEL_FITS/MODEL_PREDICTS（幂等）
# fit/predict 签名与 kg_r3c.execution 完全一致
```

### plan_contract.py
```python
@dataclass(frozen=True)
class PlanError:
    code: str
    message: str
    path: str
PLAN_VALIDATOR_VERSION = "kq_plan_validator.v1"
def parse_plan_json(text: str) -> dict[str, Any]  # 提取配平 JSON；失败抛 ValueError("PLAN_JSON_PARSE:…")
def validate_plan(plan: dict[str, Any], *, expected_snapshot_sha256: str, frequency: str) -> list[PlanError]
def narrowed_contracts(plan: dict[str, Any], frequency: str) -> dict[str, list[ParameterContract]]
    # plan parameter_spaces → 每个 adapter 的收窄合同（未提及参数保持默认，不可调）
def deterministic_fallback_plan(snapshot_sha256: str, frequency: str, reason: str) -> dict[str, Any]
```

### qwen_client.py
```python
class QwenUnavailable(RuntimeError): ...
@dataclass(frozen=True)
class QwenResponse:
    text: str
    prompt_sha256: str
    response_sha256: str
    latency_ms: int
    prompt_tokens: int | None
    completion_tokens: int | None
class QwenClient:
    def __init__(self, model_dir: str = QWEN_MODEL_DIR, worker_python: str = QWEN_WORKER_PYTHON,
                 timeout_seconds: int = 1200) -> None: ...
    def complete(self, messages: list[dict[str, str]], max_new_tokens: int = 2048) -> QwenResponse: ...
    def close(self) -> None: ...
    def __enter__/__exit__
def model_fingerprint(model_dir: str) -> str  # sha256
def build_messages(context: dict[str, Any], round_index: int, prior_errors: list[str] | None = None) -> list[dict[str, str]]
PROMPT_TEMPLATE_SHA256: str  # 模块加载时计算
```

### agent_tools.py
```python
ALLOWED_TOOLS: tuple[str, ...] = ("get_dataset_profile", "get_kg_model_candidates",
    "get_parameter_contracts", "get_experiment_history", "get_validation_summary", "submit_tuning_plan")
def dispatch_tool(name: str, args: dict[str, Any], ctx: "ToolContext") -> dict[str, Any]
    # 未列入 ALLOWED_TOOLS -> {"error": "TOOL_NOT_PERMITTED", "tool": name}
@dataclass
class ToolContext:  # 纯数据，无 DB
    track_key: str
    frequency: str
    trainval_rows: Sequence[TrainingRow]
    snapshot_sha256: str
    split: TemporalSplit
    validation_summaries: list[dict[str, Any]]  # 已完成轮次的聚合摘要
def build_qwen_context(ctx: ToolContext, round_index: int) -> dict[str, Any]  # 白名单上下文
def dataset_profile(rows: Sequence[TrainingRow], frequency: str) -> dict[str, Any]  # 纯统计
```

### loop.py
```python
@dataclass
class QwenProposal:
    plan: dict[str, Any]
    plan_source: str  # 'QWEN' | 'QWEN_FALLBACK_DETERMINISTIC'
    prompt_sha256: str | None
    response_sha256: str | None
    latency_ms: int | None
@dataclass
class RoundResult:
    round_index: int
    proposal: QwenProposal
    validation_status: str  # 'ACCEPTED' | 'FALLBACK_AFTER_REJECTION'
    validation_errors: list[dict[str, str]]
    trials: list[TrialResult]           # 该轮全部 trials（含基线评估 trial）
    best_adapter_id: str | None
    best_trial_sha256: str | None
    best_val_wape: float | None
    best_val_agreement_pct: float | None
    best_val_coverage: float | None
    baseline_best_wape: float | None
    improvement_vs_baseline: float | None
    target_met: bool
    stop_reason: str | None
    trials_executed: int
@dataclass
class LoopResult:
    track_key: str
    rounds: list[RoundResult]
    champion_adapter_id: str
    champion_params: dict[str, Any]
    champion_source: str  # 'TUNED_MODEL' | 'BASELINE'
    target_met: bool
    stop_reason: str  # 'TARGET_MET' | 'BUDGET_EXHAUSTED'
    best_val_wape: float | None
    best_val_agreement_pct: float | None
    best_val_coverage: float | None
    baseline_metrics: dict[str, dict[str, Any]]
def run_autotuning_loop(
    *,
    track_key: str,
    frequency: str,
    trainval_rows: Sequence[TrainingRow],
    split: TemporalSplit,
    snapshot_sha256: str,
    planner: Callable[[dict[str, Any], int], QwenProposal],  # (context, round_index) -> proposal
    tool_context: ToolContext,
    on_round_complete: Callable[[RoundResult], None] | None = None,  # checkpoint 回调
    seed: int = KQ_SEED,
    max_rounds: int = MAX_ROUNDS,
    max_trials_per_round: int = MAX_TRIALS_PER_ROUND,
    max_total_seconds: int = MAX_TOTAL_MINUTES * 60,
) -> LoopResult
```

### sentinels.py
```python
@dataclass(frozen=True)
class SentinelResult:
    name: str
    passed: bool
    detail: str
    evidence_sha256: str | None
def run_all_sentinels(*, track_key: str, frequency: str, rows: Sequence[TrainingRow],
    split: TemporalSplit, snapshot_sha256: str, seed: int = KQ_SEED) -> list[SentinelResult]
```

### pipeline.py
```python
def execute_autotuning(conn: Any, *, actor: str, idempotency_key: str, track_key: str) -> dict[str, Any]
def autotuning_overview(conn: Any, track_key: str) -> dict[str, Any] | None
def autotuning_plans(conn: Any, run_id: str) -> list[dict[str, Any]]
def autotuning_rounds(conn: Any, run_id: str) -> list[dict[str, Any]]
def autotuning_trials(conn: Any, run_id: str, round_index: int | None) -> list[dict[str, Any]]
def autotuning_evaluation(conn: Any, run_id: str) -> dict[str, Any] | None
def autotuning_feedback(conn: Any, run_id: str) -> dict[str, Any] | None
def reconcile_feedback(conn: Any, *, actor: str, idempotency_key: str, run_id: str) -> dict[str, Any]
```
conn 为 psycopg dict_row 连接（与 kg_pt.pipeline 相同风格）；模块可无 DB import。

### api.py
```python
def install_autotuning_routes(app: FastAPI, engine: Engine, authorized: Callable[[Request], bool],
    unauthorized: Callable[[], Response], json_response: Callable[[Any, int], Response]) -> None
```
