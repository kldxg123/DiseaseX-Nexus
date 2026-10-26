from __future__ import annotations
import csv, hashlib, json, zipfile
from pathlib import Path

ROOT=Path("USER_HOME/projects/openclaw-fresh-ukhsa-20260814")
NAME="who_official_fresh_trials_20260814T104500Z"
OUT=ROOT/"reports"/NAME
ZIP=ROOT/"reports"/f"{NAME}.zip"
if OUT.exists() or ZIP.exists():
    raise SystemExit("OUTPUT_ALREADY_EXISTS")
OUT.mkdir(parents=True)

rows=[
 {"track":"ZMB SARI rate","track_key":"ZMB:influenza:who_fluid_sari_rate_per_1000_inpatients:week","run_id":"d9bc833c-d0fd-5290-b567-f8dd3042fa7a","registry_id":"e41645dd-286c-5506-9ce3-7cbedae9205e","champion":"seasonal_delta_blend.v1","wape":0.25963188718458236,"agreement_pct":74.03681128154176,"coverage":0.8974358974358975,"paired_n":39,"paired_sample_match":False,"best_baseline_id":None,"best_baseline_wape":None,"relative_improvement":None,"failed_gates":["paired_baseline_improvement_min_0.01","paired_sample_match"],"status":"FRESH_HELDOUT_GATES_FAILED","reason_zh":"末52周存在缺失，项目模型仅39条可比较预测，而基线有47条；样本集合不一致，不能认证。共同39条上项目模型也未胜过 last-value。"},
 {"track":"LAO ILI rate","track_key":"LAO:influenza:who_fluid_ili_rate_per_1000_outpatients:week","run_id":"6733e40d-7601-5075-aee5-219ebc5ddf02","registry_id":"5c5cfdc5-1174-55ce-bd5b-c6c08badffd5","champion":"seasonal_ensemble.v1","wape":0.33982796625698214,"agreement_pct":66.01720337430179,"coverage":0.875,"paired_n":48,"paired_sample_match":True,"best_baseline_id":"last_value","best_baseline_wape":0.3247596915894859,"relative_improvement":-0.04639822939154451,"failed_gates":["wape_max_0.30","agreement_min_70","paired_baseline_improvement_min_0.01"],"status":"FRESH_HELDOUT_GATES_FAILED","reason_zh":"验证窗口表现良好，但新鲜盲测期分布变化；项目模型 WAPE 33.98%，比 last-value 基线更差4.64%。"},
 {"track":"ZAF SARI reported cases","track_key":"ZAF:influenza:who_fluid_sari_cases_reported:week","run_id":"fa5516c5-6d0a-5f89-a913-234519b86834","registry_id":"63ea663a-0a47-53b3-a14a-ce033c9fecb9","champion":"seasonal_delta_blend.v1","wape":0.24875920729312773,"agreement_pct":75.12407927068723,"coverage":1.0,"paired_n":48,"paired_sample_match":True,"best_baseline_id":"seasonal_naive","best_baseline_wape":0.20140252454417953,"relative_improvement":-0.2351345041782734,"failed_gates":["paired_baseline_improvement_min_0.01"],"status":"FRESH_HELDOUT_GATES_FAILED","reason_zh":"WAPE、吻合度、覆盖率、同样本和一次性消费全部通过，但季节朴素基线 WAPE 20.14% 更好；项目模型相对劣化23.51%，不可认证。"},
]
summary={
 "status":"FRESH_THREE_OFFICIAL_TRIALS_COMPLETED_GATES_FAILED_ACTIVE_UNCHANGED",
 "source":{"publisher":"World Health Organization","dataset":"fluID","official_url":"https://xmart-api-public.who.int/FLUMART/VIW_FID_EPI?%24format=csv","raw_sha256":"99fe45c41db35bcede7153308c4a4a3ed43369909709618bbe9bd34af567e165","raw_bytes":74725339,"raw_rows":588524},
 "certification_target":{"wape_max":0.30,"agreement_pct_min":70.0,"coverage_min":0.85,"baseline_improvement_min":0.01,"paired_sample_match":True,"single_consumption":True,"time_chain_ok":True},
 "trials":rows,"certified_count":0,"numeric_wape_agreement_coverage_pass_count":2,
 "active_head":"cc4a1b26786319b315a7480a828bb80b0a0e873c","isolated_head":"c780ba5bdfb7afd2b55025f278553a3da4a885bb",
 "active_healthz":200,"active_readyz":200,
 "protected_counts":{"trusted_observations_before":366082,"trusted_observations_after":366082,"forecast_active_runs_before":6,"forecast_active_runs_after":6,"promotion_events_before":0,"promotion_events_after":0,"active_pointer_delta":0},
 "deployment":{"isolated_branch_only":True,"active_code_changed":False,"active_service_restarted":False,"fresh_visualization_added":False},
 "tests":{"relevant_pytest_passed":87,"failed":0,"compileall":"PASS","diff_check":"PASS"},
}
(OUT/"01_FRESH_TRIALS_SUMMARY.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
with (OUT/"02_FRESH_TRIALS_COMPARISON.csv").open("w",encoding="utf-8-sig",newline="") as f:
 w=csv.DictWriter(f,fieldnames=["track","track_key","run_id","champion","wape","agreement_pct","coverage","paired_n","paired_sample_match","best_baseline_id","best_baseline_wape","relative_improvement","failed_gates","status"]);w.writeheader()
 for r in rows:
  q={k:r.get(k) for k in w.fieldnames};q["failed_gates"]=";".join(r["failed_gates"]);w.writerow(q)
gates=["wape_max_0.30","agreement_min_70","coverage_min_0.85","paired_sample_match","paired_baseline_improvement_min_0.01","single_consumption","time_chain_ok"]
with (OUT/"03_FRESH_GATE_MATRIX.csv").open("w",encoding="utf-8-sig",newline="") as f:
 w=csv.writer(f);w.writerow(["track",*gates,"certified"])
 for r in rows:
  failed=set(r["failed_gates"]);w.writerow([r["track"],*[g not in failed for g in gates],False])
(OUT/"04_NON_MUTATION_ATTESTATION.json").write_text(json.dumps(summary["protected_counts"]|summary["deployment"],ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
prefix={"hidden_values_parsed_during_selection":False,"rate_candidate_count":39,"rate_strict_pre_gate_count":1,"count_candidate_selected":"ZAF:influenza:who_fluid_sari_cases_reported:week","zaf_prefix":{"selection_median_wape":0.2064337618030651,"selection_worst_wape":0.28890858245696954,"pseudo_blind_wape":0.16716331716331717,"pseudo_blind_improvement_vs_baseline":0.32526806526806523,"paired_n":52,"hidden_start":"2025-08-04","hidden_end":"2026-07-27","hidden_present_weeks":52},"khm_extra_screen":{"hidden_values_read":False,"candidate_count":3,"eligible_count":0}}
(OUT/"05_PREFIX_ONLY_SELECTION_EVIDENCE.json").write_text(json.dumps(prefix,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
report="""# WHO 官方 fluID Fresh 路径优化最终报告

## 最终状态

FRESH_THREE_OFFICIAL_TRIALS_COMPLETED_GATES_FAILED_ACTIVE_UNCHANGED

本轮真实新增并消费了三条互不相同、此前未打开的 WHO fluID 末 52 周留出窗口。没有任何一条同时通过全部严格认证门禁，因此未新增 Fresh 认证链路、未部署新页面、未改线上功能。

## 核心结果

| 轨道 | Champion | Fresh WAPE | 吻合度 | 覆盖率 | 最佳基线 WAPE | 结论 |
|---|---|---:|---:|---:|---:|---|
| ZMB SARI rate | seasonal_delta_blend.v1 | 25.96% | 74.04% | 89.74% | 不可认证同样本 | 样本不一致；FAIL |
| LAO ILI rate | seasonal_ensemble.v1 | 33.98% | 66.02% | 87.50% | 32.48% | 分布漂移且落后基线；FAIL |
| ZAF SARI cases | seasonal_delta_blend.v1 | 24.88% | 75.12% | 100.00% | 20.14% | 数值过线但落后季节基线；FAIL |

“吻合度”定义为 max(0, 1-WAPE)×100%，不是分类准确率。ZAF 的 75.12% 不能单独作为认证，因为基线在完全相同的 48 条样本上达到 79.86%。

## 为什么不继续打开更多 Fresh 窗口

1. 已在同一官方数据集中进行了 39 条 rate 序列的无隐藏值筛选、病例数序列的严格筛选，以及 KHM 的额外稳健性筛选。
2. 三次真正 Fresh 打开分别暴露了缺失样本、分布漂移和强季节基线三类不同失败机制。
3. 继续反复打开国家窗口并只报告成功者会形成多重试验/择优报告风险，削弱论文结论。
4. 更合理的下一步是预注册新模型和来源条件，再等待未来从未到达的数据做前瞻评估，而不是复用已消费窗口。

## 安全与非变更

- Trusted observations：366082 → 366082。
- ACTIVE/PUBLISHED forecast runs：6 → 6。
- promotion events：0 → 0。
- active pointer delta：0。
- 线上 HEAD 保持 cc4a1b26786319b315a7480a828bb80b0a0e873c。
- 线上 healthz/readyz 均为 200。
- 隔离分支 HEAD：c780ba5bdfb7afd2b55025f278553a3da4a885bb，未合并、未 push、未部署。
- 相关测试：87 passed，0 failed。

## 论文应如何表述

可表述为：“系统完成了三条全局一次性消费的 Fresh held-out 实验；其中两条在 WAPE、易读吻合度和区间覆盖率上达标，但均未通过相对强基线改进或同样本门禁，因此系统拒绝认证并保持线上不变。”

不得表述为：“已经新增三条 70% 以上 Fresh 正式预测链路”。
"""
(OUT/"00_FINAL_REPORT.md").write_text(report,encoding="utf-8")
(OUT/"06_NO_DEPLOYMENT_DECISION.md").write_text("# 不部署决定\n\n三条 run 均为 Shadow 审计结果，认证状态均为 FRESH_HELDOUT_GATES_FAILED。按照用户边界，失败不得影响现有功能，因此未修改 active 分支、未重启 Dashboard、未添加 Fresh 可视化、未 promotion、未变更 active pointer。隔离分支和运行时资产仅用于复现与审计。\n",encoding="utf-8")
prompt="""# Fresh 三轨实验结果图重绘提示词

1. 推荐方案：分面横向条形图 + 门禁矩阵热力图
2. 核心理由：横向条形图同时呈现项目模型 WAPE、最佳可认证基线 WAPE 与 30% 阈值；门禁热力图说明“数值达到70%仍可能因基线或样本一致性失败”，避免把吻合度误写为分类准确率。
3. 视觉设计规范：
   - 坐标轴：左侧分面 X 轴为 WAPE（%），Y 轴为 ZMB/LAO/ZAF；项目模型使用深蓝，基线使用灰色；30% 用红色虚线。
   - 尺度处理：统一 0–40%，不使用断裂轴。
   - 统计要素：标注 WAPE、吻合度=max(0,1-WAPE)×100%、覆盖率；单次锁定盲测不添加虚假误差线。
   - 配色与样式：项目模型 #2563EB，基线 #64748B，通过 #16A34A，失败 #DC2626，白底、中文可读字体。
4. 图表数据：
   ZMB: model WAPE=25.9632, baseline=NA(certification sample mismatch), agreement=74.0368, coverage=89.7436
   LAO: model WAPE=33.9828, baseline WAPE=32.4760, agreement=66.0172, coverage=87.5
   ZAF: model WAPE=24.8759, baseline WAPE=20.1403, agreement=75.1241, coverage=100.0
5. 需要强调的结论：三条 Fresh 均未完成严格认证；ZAF 虽吻合度超过70%，但没有优于季节基线。
"""
(OUT/"07_CHART_RECONSTRUCTION_PROMPT.txt").write_text(prompt,encoding="utf-8")
(OUT/"08_TEST_AND_RUNTIME_EVIDENCE.md").write_text("# 测试与运行证据\n\n- pytest 相关 87 项：PASS。\n- compileall：PASS。\n- git diff --check：PASS。\n- ZMB/LAO/ZAF registry 均为 CONSUMED，single_consumption=true，time_chain_ok=true。\n- Qwen 方案均由本地 Qwen3-4B-Instruct-2507 生成；预测执行处于 Shadow。\n- 不含训练/预测正式发布，不构成医疗或公共卫生建议。\n",encoding="utf-8")
(OUT/"09_CHANGESET_AND_ROLLBACK.md").write_text("# 变更与回滚\n\n- active 项目未变；无需线上回滚。\n- 隔离 worktree：USER_HOME/projects/openclaw-fresh-ukhsa-20260814。\n- 隔离 commits：ea3dbeb、e1dcfe8、c780ba5。\n- 若无需继续研究，可保留隔离 worktree 和 CONSUMED 审计记录；不得删除或伪装为未消费。\n- 正式数据、promotion、active pointer 无变更。\n",encoding="utf-8")
members=sorted(p for p in OUT.iterdir() if p.is_file())
(OUT/"MANIFEST.sha256").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in members),encoding="utf-8")
with zipfile.ZipFile(ZIP,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
    for p in sorted(OUT.iterdir()):
        if p.is_file(): z.write(p,arcname=f"{NAME}/{p.name}")
print(json.dumps({"report_dir":str(OUT),"report_zip":str(ZIP),"zip_sha256":hashlib.sha256(ZIP.read_bytes()).hexdigest(),"file_count":len(list(OUT.iterdir()))},sort_keys=True))
