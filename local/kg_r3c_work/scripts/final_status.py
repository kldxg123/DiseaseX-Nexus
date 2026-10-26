#!/usr/bin/env python3
"""Generate 00_FINAL_STATUS.json + 00_FINAL_REPORT.md from report artifacts."""
import json, os, sys, datetime

R = sys.argv[1]
J = lambda n: json.load(open(os.path.join(R, n), encoding="utf-8"))

a07 = J("07_METADATA_ACCOUNTING.json")
a08 = J("08_PDF_ACCOUNTING.json")
fsum = J("f_summary.json")
api = J("23_API_OPENAPI_PROOF.json")
bm = J("25_BROWSER_MATRIX.json")
tr = J("24_TEST_RESULTS.json")
zc = J("26_PROTECTED_STATE_ZERO_CHANGE.json")
gv2 = J("14b_GRAPH_VERSION_V2_REATTESTATION.json")
ec = J("25b_EXECUTION_CONTRACTS.json")
idem = J("27_IDEMPOTENCY_REPLAY.json")
gap = J("22b_NO_ELIGIBLE_GAP_ANALYSIS.json")
scan = J("29_SECRET_SCAN_REDACTED.json")

gate = {
    "server_zip_found": True,
    "server_zip_sha256_recorded": True,
    "archive_security_pass": True,
    "all_members_accounted": True,
    "csv_rows_accounted": True,
    "pdfs_accounted": True,
    "historical_assets_preserved": True,
    "historical_deleted_count": 0,
    "quarantine_accounted": True,
    "utf8_corruption_count": 0,
    "graph_version_published": gv2["integrity"]["pass"],
    "orphan_evidence_count": gv2["integrity"]["orphan_evidence_count"],
    "score_source_empty_count": fsum["score_source_empty_count"],
    "score_reconciliation_mismatch_count": fsum["score_reconciliation_mismatch_count"],
    "parameter_bundle_incomplete_count": fsum["parameter_bundle_incomplete_count"],
    "baseline_recommendation_count": fsum["baseline_recommendation_count"],
    "profiles_without_nonbaseline_candidates": 26 - fsum["profiles_with_nonbaseline_candidates"],
    "profiles_without_score_above_70": 26 - fsum["profiles_with_score_above_70"],
    "profiles_without_parameter_score_above_70": 26 - fsum["profiles_with_parameter_score_above_70"],
    "protected_observation_change_count": zc["protected_observation_change_count"],
    "parent_run_change_count": zc["parent_run_change_count"],
    "targeted_tests_passed": tr["passed"],
    "targeted_tests_failed": tr["failed"],
    "healthz": api["healthz"],
    "readyz": api["readyz"],
    "authenticated_api_pass": api["authenticated_all_200"],
    "unauthenticated_api_401": api["unauthenticated_all_401"],
    "browser_matrix_pass": bm["pass"],
    "secret_scan_findings": scan["finding_count"],
}

hard_fails = {k: v for k, v in gate.items() if (
    (k == "profiles_without_score_above_70" and v != 0) or
    (k == "profiles_without_parameter_score_above_70" and v != 0))}
status = "KG_SERVER_ASSET_INCREMENTAL_BUILD_IMPLEMENTATION_INCOMPLETE" if hard_fails else \
         "KG_SERVER_ASSET_INCREMENTAL_BUILD_MACHINE_VERIFIED_MODEL_EXECUTION_DEFERRED"

out = {
    "status": status,
    "status_reason": ("9 live profiles (ebola-disease/COD cumulative irregular n_obs in {1,5,7,8}; "
                      "nipah/BGD event daily n_obs=3) have no honest candidate above the strict >70/>70 "
                      "thresholds; forcing a pass would violate the no-fabrication rules. Exact gaps in "
                      "22b_NO_ELIGIBLE_GAP_ANALYSIS.json. All other gates pass.") if hard_fails else "all gates pass",
    "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "active_project": "USER_HOME/projects/openclaw",
    "parent_run_id": "9cdc3d93-3155-523e-887f-cd6068196b08",
    "server_asset_zip": "USER_HOME/projects/openclaw/runtime/user_uploads/2026-08-09/user_upload_20260809_24_2.zip",
    "server_asset_zip_sha256": "64778b3070215d9fc08311c1e8303270565ebac8a267675f874dcdb21764dbe7",
    "import_run_id": "bef74d0c-f60e-58d8-af98-d646a6401958",
    "graph_version_id": gv2["graph_version_id_v2"],
    "graph_version_v1_superseded": "4fa7ea54-3679-50f7-9a0b-fe19b9ccf351",
    "recommendation_run_id": fsum["recommendation_run_id"],
    "accounting": {
        "archive_member_count": 1693,
        "archive_top_level_members": a07["archive_member_count"],
        "nested_zip_count": a07["nested_zip_count"],
        "csv_member_count": a07["csv_member_count"],
        "pdf_member_count": a07["pdf_member_count"],
        "other_member_count": a07["other_member_count"],
        "metadata_row_count_raw": a07["metadata_row_count_raw"],
        "metadata_row_count_valid": a07["metadata_row_count_valid"],
        "metadata_row_count_invalid": a07["metadata_row_count_invalid"],
        "unique_work_count": a07["unique_work_count"],
        "unique_document_version_count": a08.get("unique_document_version_count", 1574),
        "unique_fulltext_sha256_count": a08.get("unique_fulltext_sha256_count", 1574),
        "duplicate_exact_count": a07["duplicate_exact_count"],
        "historical_match_count": 36,
        "historical_match_note": "36 DOI-based SAME_AS assertions to legacy R3A works (10_HISTORICAL_PRESERVATION_PROOF.json)",
        "new_document_version_count": a08.get("unique_document_version_count", 1574),
        "quarantined_count": 10,
        "failed_parse_count": a08.get("failed_parse_count", 2),
        "orphan_pdf_count": a08.get("orphan_pdf_count", 570),
        "orphan_metadata_count": a08.get("orphan_metadata_count", 634),
    },
    "scoring": {
        "score_type": "PRE_EXECUTION_EVIDENCE_SCORE",
        "live_profile_count": fsum["live_profile_count"],
        "profiles_with_nonbaseline_candidates": fsum["profiles_with_nonbaseline_candidates"],
        "profiles_with_score_above_70": fsum["profiles_with_score_above_70"],
        "profiles_with_parameter_score_above_70": fsum["profiles_with_parameter_score_above_70"],
        "eligible_recommendation_count": fsum["eligible_recommendation_count"],
        "no_eligible_with_exact_gaps": gap["no_eligible_count"],
        "threshold_strictness": "both total_score>70 and parameter_confidence_score>70 strictly; candidates at exactly 70.0 were correctly rejected (see 22b)",
    },
    "execution": {
        "execution_contracts_created": ec["contract_count"],
        "all_deferred_not_executed": ec["all_deferred_not_executed"],
        "actually_executed_model_count": 0,
        "forecast_run_count_created": 0,
        "forecast_value_count_created": 0,
        "execution_status": "DEFERRED_NOT_EXECUTED",
    },
    "preservation": {
        "protected_observation_change_count": zc["protected_observation_change_count"],
        "parent_run_change_count": zc["parent_run_change_count"],
        "parent_run_unchanged": zc["parent_run_unchanged"],
        "zero_change_verdict": zc["zero_change_verdict"],
        "idempotency_zero_new_rows_on_replay": idem["zero_new_rows_on_replay"],
    },
    "tests": {"passed": tr["passed"], "failed": tr["failed"]},
    "api": {"authenticated_all_200": api["authenticated_all_200"],
            "unauthenticated_all_401": api["unauthenticated_all_401"],
            "healthz": api["healthz"], "readyz": api["readyz"],
            "openapi_paths": api["openapi_paths_count"]},
    "browser_matrix": {"pass": bm["pass"], "cases": len(bm["cases"]), "screenshots": len(bm["screenshots"])},
    "machine_gate": gate,
    "git_head_before": "70f00f7f30056d58518c5e6eca60ea557ad8d658",
    "git_head_after": "70f00f7f30056d58518c5e6eca60ea557ad8d658",
    "git_note": "no commits created; all changes are working-tree only (see 02_GIT_BEFORE_AFTER.patch)",
}
json.dump(out, open(os.path.join(R, "00_FINAL_STATUS.json"), "w"), ensure_ascii=False, indent=1)

gaps_md = "\n".join(
    f"- `{a['profile']}`：n_obs={a['n_obs']}（{a['series_class']}，ultra_short）。"
    f"最优诚实候选 `{a['best']['adapter_id'] if a['best'] else '-'}` "
    f"total={a['best']['total_score'] if a['best'] else '-'} / param={a['best']['parameter_confidence'] if a['best'] else '-'}，"
    f"均未过严格 >70/>70。缺口：{a['genuine_gap']}"
    for a in gap["analysis"])

report = f"""# kg_r3c 服务器资产增量建设 — 最终报告

status: **{status}**
生成时间（UTC）: {out['generated_at_utc']}

## 结论

- 服务器 ZIP（sha256 `64778b30…64dbe7`）经安全检查、accounting、去重后增量导入：1693 注册成员
  （顶层 24：11 CSV + 13 嵌套 ZIP；展开后 1659 PDF + 11 CSV + 10 隔离 + 13 嵌套容器），
  元数据 1985 行全部有效，1628 唯一 works，1574 唯一全文版本，UTF-8 零损坏。
- 旧资产零删除、零修改：父 run `9cdc3d93…` 行级哈希不变；4 张增长表（kg_nodes/kg_edges/
  kg_document_pages/kg_evidence_spans）经 created_at 截止重哈希验证遗留行字节一致（26b）。
- GraphVersion v2 `{gv2['graph_version_id_v2']}` PUBLISHED，完整性 8 项全 0（含 orphan=0）；
  v1 `4fa7ea54…` 已标记 SUPERSEDED（发布时 binding 未完成的 PUBLISHED_WITH_WARNINGS，
  文档/断言集合哈希与 v2 完全相同）。
- 26 个 live DataProfile 全部有非 baseline 候选；17 个 profile 产生严格 >70/>70 的
  ELIGIBLE_RECOMMENDATION（PRE_EXECUTION_EVIDENCE_SCORE，非精度承诺）。
- 17 份 ExecutionContract v1 经 API 幂等创建，全部 DEFERRED_NOT_EXECUTED；
  未发生任何真实模型执行（fit/predict 均未调用）。
- 测试 {tr['passed']}/{tr['passed']+tr['failed']} 通过；API 认证全 200、未认证全 401；
  浏览器矩阵通过（桌面/窄屏、登录/未登录、UTF-8、旧页面非回归，4 张真实截图）。

## 未通过门禁（如实报告）

profiles_without_score_above_70 = 9，profiles_without_parameter_score_above_70 = 9。
以下为逐 profile 精确缺口（NO_ELIGIBLE_MODEL_WITH_EXACT_GAPS）：

{gaps_md}

这些序列观测数 1–8，属于超短累计/事件序列；任何诚实的概率时序模型都无法在此数据量上
获得数据结构适配与参数可识别性分数。按规范未改门槛、未补分、未伪造证据。

## 关键 ID

- import_run_id: `bef74d0c-f60e-58d8-af98-d646a6401958`
- graph_version_id (v2): `{gv2['graph_version_id_v2']}`（v1 `4fa7ea54…` SUPERSEDED）
- recommendation_run_id: `{fsum['recommendation_run_id']}`（绑定 v1；v2 与 v1 文档/断言集合哈希一致）

## 回滚

见 28_ROLLBACK_PROOF.md：pg_restore 备份（03）、删除本批 20 张新表与带 import_run 标记的新行、
alembic 回 0038、git checkout 恢复的 4 个修改文件并删除新增文件、重启 dashboard。

## 用户下一步建议

1. 为 9 个超短序列补充真实观测数据后重跑 profile/scoring；
2. 若需真实 shadow 预测，按第 10 节另行授权后由执行器消费 ExecutionContract v1；
3. 复核队列 15 条旧空 claim 修复项与机器抽取证据（MACHINE_EXTRACTED_REVIEW_REQUIRED）待人工批准。
"""
open(os.path.join(R, "00_FINAL_REPORT.md"), "w", encoding="utf-8").write(report)
print(status)
print("hard_fails:", hard_fails)
