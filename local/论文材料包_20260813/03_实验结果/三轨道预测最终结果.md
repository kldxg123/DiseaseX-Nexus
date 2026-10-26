# KG_QWEN_AUTOTUNING_FRESH_HELDOUT_GOVERNANCE_REPAIR — 最终报告

## 最终状态

**KG_QWEN_AUTOTUNING_GOVERNANCE_REPAIRED**
+ **TRACK_C_FRESH_BLIND_EXECUTED_ONCE_GATES_FAILED**（新鲜盲测真实执行且全局只消费一次，但 WAPE/吻合度目标未达成，诚实记录，不认证）
+ **AUTOTUNING_TARGET_NOT_MET_WITHIN_BUDGET**（Track C 验证集 3 轮预算内未达到 0.30）

明确**不**返回：KG_QWEN_AUTOTUNING_FRESH_HELDOUT_70_VERIFIED。

## 1. 七个已确认问题的修复证据

| 问题 | 修复 | 证据 |
|---|---|---|
| 同一窗口被 3 个 run 重复评估 | 全局注册表（身份不含 run_id）+ 原子消费 | 04_HELDOUT_REGISTRY.json, 09_CONCURRENCY.json |
| eval_id 含 run_id 无法跨 run 拦截 | registry_id = stable_uuid(track+snapshot+window+identity+split) | 同上 |
| Track B test_label perturb=0 | 哨兵必须传全量行；0 扰动=NOT_APPLICABLE | 08_SENTINELS.json（A:36/B:52/C:52 真实扰动）|
| qwen_context checked=0 | 自然 hidden-only 值 + 金丝雀不变性双重检查 | 08（A:6+36、B:2+52、C:30+52 真实检查）|
| 48 vs 47 非同样本 | (origin,horizon,target_period) 配对门禁，不一致即失败 | 10_PAIRED_BASELINE.json/.csv |
| 71.97% 冒充 fresh | 3 条历史行归类 RETROSPECTIVE_REPLAY_NOT_FRESH，指标原样保留 | 05_HISTORICAL_RECLASSIFICATION.json |
| 无全局消费门禁 | REGISTERED→LOCKED_UNOPENED→CONSUMED/REPLAY_ONLY 状态机，8 路并发 1 成功 | 09 |

## 2. Track C 新鲜盲测（一次性，已消费）

- 数据：CHL / influenza / influenza_positive_detections / week（FluNet 归一化层），尾部 445 周（2018-01-08..2026-07-13），无缺失无断档；DEU 候选被拒绝（已在 5 个主平台快照+预测中使用，见 06_BLIND_WINDOW_AUDIT.json）。
- 切分：train 321 / val 72（调优窗 393 周）/ hidden 52 周（2025-07-21..2026-07-13）。
- 冻结：独立事务在 Qwen 前提交（LOCKED_UNOPENED, 2026-08-12 14:33:54Z+8）。
- Qwen：3 轮真实 QWEN 计划全部 ACCEPTED（latency 246s/327s/316s），76 trials。
- 验证：最佳验证 WAPE 0.5836（吻合度 41.6%），三轮 0.6501→0.6083→0.5836，目标 0.30 未达成（BUDGET_EXHAUSTED）。
- Champion：lagged_count_boosting.v1（n_estimators=13, lr=0.2703, msl=25），TUNED_MODEL。
- 独立盲测（全局唯一消费，CONSUMED）：WAPE 0.7170，吻合度 28.30%，覆盖率 0.896。
- 同样本配对：paired_n=48 全模型一致；最佳基线 rolling_mean WAPE 0.8248；Champion 相对改善 +13.07%（≥1% 通过）；CSV SHA ea83eb43…。
- 认证：FRESH_HELDOUT_GATES_FAILED（wape_max_0.30 / agreement_min_70 / time_chain_ok 未过）。
- 时间链：locked 14:33:54 < first_qwen 14:33:54.8 < tuning_finished 14:54:46 成立；记录中 consumed_at=14:41:14 是事务开始时刻（now() 语义）的测量伪影——消费语句在程序顺序上于调参结束后执行，本提交已将 consumed_at/locked_at 改为 clock_timestamp()，未来窗口不再出现该伪影。

## 3. 安全边界

Shadow only：promotion=0、active pointer 变更=0、Trusted 表零写入、历史指标逐行原样（17_NON_MUTATION.json ALL_PASS）。

## 4. 验收

175 pytest / ruff / strict mypy / pip check / 迁移空库升降级 / 8 路并发消费 1 成功 7 拒绝 / API 10 项 / 浏览器 console=pageerror=failed_request=0 / DB-API-UI 对账一致 / clean clone f073229 复现通过。

## 5. 下一次达到 70% 应如何判断

只有在一条**从未被任何训练快照、预测运行、报告或人员打开过**的新序列/新窗口上，由全局注册表原子锁定并消费一次，且同时满足 WAPE≤0.30、吻合度≥70%、覆盖率≥0.85、严格同样本相对最佳基线改善≥1%、全部哨兵非空通过、时间链成立，才允许声明 KG_QWEN_AUTOTUNING_FRESH_HELDOUT_70_VERIFIED。Track C 窗口已 CONSUMED，永不可再次作为 fresh 认证窗口。
