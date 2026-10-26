# KG Qwen 自动调优与受控自学习演示 — 最终报告

日期：2026-08-12（UTC）　提交：65b7878　Alembic：0042_kg_qwen_autotuning　状态：Shadow only

## 最终判定

- **KG_QWEN_AUTOTUNING_HELDOUT_70_VERIFIED**（Track B 满足全部 fresh 门禁，见下）
- 同时记录：**AUTOTUNING_TARGET_NOT_MET_WITHIN_BUDGET**（Track B 验证集 WAPE 未在 3 轮预算内达到 0.30 目标——优化目标未达成与闭环正确性是两回事，如实标注）
- Track A 标记 **REUSED_HISTORICAL_BENCHMARK / NOT_FRESH_HELDOUT**（机制演示，非前瞻证据）

## 任务书 13 问

1. **Qwen 实际生成了哪些调优建议**
   - Track A（1 轮即达标）：候选 negbin_dglm.v1 + seasonal_negbin_glm.v1 + fourier_negbin_glm.v1；中文理由"高过分散需大 shape / 趋势匹配 trend 参数 / 季节性用 K=2"；5 个参数空间（shape/discount/trend/K/shape），来源标注 IMPLEMENTATION_DEFAULT / DATA_PROFILE_RULE / OPTIMIZER_SELECTED 混合。
   - Track B（3 轮）：第 1 轮候选 seasonal_negbin_glm.v1 + fourier_negbin_glm.v1 + lagged_count_boosting.v1（boosting 三参数全空间 + ensemble weight）；第 2 轮切换到 seasonal_ensemble.v1 组合方案；第 3 轮回到 negbin_dglm.v1 收窄 shape/prior_shape。每轮 rationale_zh、known_risks、stop_conditions 均入库（04_QWEN_PLANS.json）。
2. **哪些建议被策略校验器拒绝及原因**：早期运行（已保留在库）中 Qwen 输出曾被拒：PLAN_JSON_PARSE（输出超 token 预算截断）、PLAN_SCHEMA_INVALID（参数空间缺 lower/upper 且未给 values）——分别通过紧凑协议与 normalize_compact_plan 默认值补全（normalization_notes 逐条记录）解决；最终两轨道 4 个 QWEN 计划全部 ACCEPTED。注入/未注册/越界样本拒绝见哨兵报告。
3. **执行了哪些模型和参数**：调优候选 5 个白名单模型（negbin_dglm.v1、seasonal_negbin_glm.v1、fourier_negbin_glm.v1、lagged_count_boosting.v1、seasonal_ensemble.v1）+ 4 基线恒参对照；Track A 最优 trial：seasonal_negbin_glm.v1 {shape=10, trend=1, ridge=0.001}；Track B 最优 trial：lagged_count_boosting.v1 {n_estimators=87, learning_rate=0.1097, min_samples_leaf=15}。全部 trial（208 行）含参数、来源、origin sha、artifact sha、逐 horizon 指标（07_TRIALS.csv）。
4. **每轮验证 WAPE 和吻合度**：Track A R1：WAPE 0.26190 / 吻合度 73.81% / 覆盖率 0.979；Track B R1 0.31889/68.11%、R2 0.39320/60.68%（ensemble）、R3 0.45457/54.54%（06_TUNING_ROUNDS.csv；吻合度=max(0,1-WAPE)×100，非分类准确率）。
5. **是否达到 70%**：验证集目标——Track A 达到（73.81% ≥70 且 WAPE 0.262 ≤0.30 且覆盖率 0.979 ≥0.85 且相对最佳基线改善 29.6%）；Track B 未在 3 轮预算内达到（最佳验证 0.31889 > 0.30），如实停止 BUDGET_EXHAUSTED。
6. **区间覆盖率是否达标**：Track A 验证 0.979、held-out 1.000；Track B 验证（冠军 trial）达标、fresh held-out 0.979 —— 均 ≥0.85。
7. **是否优于 seasonal_naive（及最佳基线）**：Track A held-out：champion 0.22703 vs seasonal_naive 0.30052（改善 24.5%）；Track B fresh held-out：champion 0.28031 vs 最佳基线 last_value 0.29696（改善 5.6%，同窗口同 origin 几何，基线为事后只读复算、未参与选择）。
8. **fresh held-out 是否真正未被使用**：是。Track B 最后 52 周在任何 Qwen 调用与调参前锁定（locked_at 10:19:51 早于全部 trial），eval_count 原子 0→1 只评估一次；qwen_context_sentinel 证明上下文数据子树 0 泄漏；test-label/future-observation/parameter-selection/ensemble-weight 哨兵全过（14_LEAKAGE_SENTINELS.json）。
9. **最终 Champion**：Track A = seasonal_negbin_glm.v1（KG_RECOMMENDED_MODEL / EXECUTED_CHAMPION）；Track B = lagged_count_boosting.v1（KG_RECOMMENDED_MODEL / EXECUTED_CHAMPION）。注：Track B 调优目标未达成但冠军判定合法——冠军门禁是"相对基线改善≥1%"而非"达到 70% 优化目标"。
10. **是否产生正式 promotion**：否。promotion_applied=0、promotion_eligible=0、active_pointer_changed=0、trusted_rows_changed=0（18_NON_MUTATION.json）。
11. **自学习实际完成到哪一步**：实际值回放成熟（Track A 32、Track B 48 条 MATURED，DEMO_HISTORICAL_REPLAY）→ 性能账本聚合（MAE/覆盖率）→ 重训触发判定（matured_feedback 触发，performance_drop/revision/drift 未触发）→ 生成 AUTOTUNING_FEEDBACK_RECONCILE 学习周期并保留 incumbent（不覆盖旧 run/参数/预测，追加式 lineage）。未做自动 promotion。
12. **页面测试地址**：http://127.0.0.1:8020/forecast-model-knowledge-graph → "Qwen 自动调优与学习闭环"区块（需登录；浏览器验收经本地隧道 127.0.0.1:18130，17_BROWSER_ACCEPTANCE.json：console/pageerror/failed-request 全 0）。
13. **报告 ZIP 本地路径**：F:\作业\aibola\reports\kg_qwen_autotuning_v1_20260812T035231Z.zip

## 关键偏离与诚实声明

- 任务书前提"服务器现有 OpenClaw + Qwen/vLLM"在 vm201 不成立：scripts/qwen_vllm 指向已下线的 autodl 主机，本机无 vLLM/无模型权重，GPU 8GB 中 3.6GB 被无关 guppy 进程常驻。处置：从 ModelScope 官方源下载固定版本 Qwen3-4B-Instruct-2507（免费 Apache-2.0），用机器上已有固定版本 Ebola venv（torch 2.12.0/transformers 5.8.1）做 CPU 推理（greedy，12 线程）；OpenClaw 编排层=本平台 epidemic_core 代码库，forecast-tuning-agent 以独立模块+工具白名单实现。无任何外部 LLM 调用、无版本升级。
- Qwen 输出采用紧凑协议：objective/预算等固定字段由平台确定性补齐；参数边界缺省时按合同全宽补全并逐条记录 normalization_notes；三次失败回退确定性计划并如实标注 QWEN_FALLBACK_DETERMINISTIC（早期 Track A run 433807ea 与 Track B run a62869f0 即为回退计划运行，保留在库作 lineage）。
- 预测吻合度为 WAPE 换算易读指标，不是分类准确率，不保证未来准确。全部结果 Shadow，不构成医疗或公共卫生结论。
- 既有遗留（非本轮引入）：迁移 0034-0037 的 4 张表缺 grants；上轮已记录。
