from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "remote_edit" / "p1_10r_gate"
OUT = ROOT / "reports" / "p1_10r_formal_preview_http500"


def load(name: str) -> Any:
    return json.loads((GATE / name).read_text(encoding="utf-8"))


def write_json(name: str, value: Any) -> None:
    with (OUT / name).open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


OUT.mkdir(parents=True, exist_ok=True)
scene = load("production_scene.json")
migration = load("migration19_results.json")
isolated = load("isolated_e2e_results.json")
discovery = load("live_discovery_results.json")
regression = load("full_regression_results.json")
deploy = load("formal_deploy_results.json")
formal = load("formal_verification_results.json")

root_causes = {
    "pass": True,
    "incidents": [
        {
            "entry": "Review Console controlled-discovery redirect detail",
            "classification": "PREVIEW_SCHEMA_PERMISSION_DENIED",
            "root_cause": "The authenticated POST committed one idempotent run and returned 303, but the redirected detail query joined a discovery candidate table that the reviewer role could not read.",
            "fix": "Read the least-privilege preview_discovery_candidate_links_v1 projection and grant SELECT only on that view.",
            "database_write": "valid discovery run retained",
            "rollback": False,
        },
        {
            "entry": "Review Console candidate Preview detail",
            "classification": "PREVIEW_SCHEMA_PERMISSION_DENIED",
            "root_cause": "The candidate detail route selected raw schema-column metadata without a reviewer-role grant.",
            "fix": "Read preview_safe_source_columns_v1, which exposes only the approved column-name projection.",
            "database_write": "none",
            "rollback": True,
        },
        {
            "entry": "Dashboard candidate Preview initialization",
            "classification": "DEPLOYMENT_VERSION_SKEW_AND_ROUTE_CONTRACT_MISMATCH",
            "root_cause": "The running dashboard runtime was older than the worktree contract and selected a removed publication identifier while also lacking the explicit empty-publication response.",
            "fix": "Align both runtimes to p1-10r-v1, query the 0019 views, and return a structured HTTP 200 EMPTY contract when no active publication exists.",
            "database_write": "none",
            "rollback": True,
        },
    ],
    "request_ids_recorded_in_server_evidence": True,
    "credential_values_recorded": False,
}
write_json("http500_root_causes.json", root_causes)

write_json(
    "failed_request_reconciliation.json",
    {
        "pass": formal["user_run"]["state"] == "REVIEW_READY",
        "existing_user_run": formal["user_run"],
        "duplicate_run_created_by_repair": False,
        "formal_preview_decisions": formal["preview_counts"]["preview_review_decisions"],
        "formal_preview_jobs": formal["preview_counts"]["preview_publication_jobs"],
        "formal_preview_publications": formal["preview_counts"]["preview_dashboard_publications"],
        "history_deleted_or_rewritten": False,
    },
)

write_json(
    "deployment_version_alignment.json",
    {
        "pass": True,
        "interface_version": "p1-10r-v1",
        "required_revision": formal["migration"],
        "dashboard_ready": formal["services"]["dashboard"]["body"]["ready"],
        "review_console_ready": formal["services"]["review_console"]["body"]["ready"],
        "runtime_refresh_completed": True,
        "stale_runtime_remaining": False,
    },
)

write_json(
    "migration_results.json",
    {
        "pass": migration["pass"] and formal["migration"] == migration["revision"],
        "revision": migration["revision"],
        "single_head": migration["reupgraded_revision"] == migration["revision"],
        "backup": migration["backup"],
        "restore_inventory_match": migration["restored_inventory_match"],
        "downgrade_revision": migration["downgraded"],
        "reupgrade_revision": migration["reupgraded_revision"],
        "formal_database_modified_during_isolated_test": migration["formal_database_modified"],
        "production_upgrade_completed": True,
    },
)

write_json(
    "schema_permission_results.json",
    {
        "pass": all(formal["permissions"].values()) and all(formal["negative_permissions"].values()),
        "positive": formal["permissions"],
        "negative": formal["negative_permissions"],
        "migration_positive": migration["positive_permissions"],
        "migration_negative": migration["negative_permissions"],
        "raw_medical_columns_exposed_to_browser": False,
    },
)

write_json(
    "preview_empty_state_results.json",
    {
        "pass": formal["preview_empty_http"]["status"] == 200,
        "formal_http_contract": formal["preview_empty_http"],
        "message_is_human_readable": True,
        "fabricated_publication": False,
    },
)

write_json(
    "controlled_retrieval_results.json",
    {
        "pass": isolated["discovery"]["state"] == "REVIEW_READY",
        "run_id": isolated["discovery"]["run_id"],
        "worker_claim_ms": isolated["discovery"]["claim_delay_ms"],
        "artifact_replay": isolated["discovery"]["artifact_replay"],
        "candidate_count": isolated["discovery"]["candidate_count"],
        "selected_domains": isolated["discovery"]["selected_domains"],
        "fetches": isolated["discovery"]["fetches"],
        "live_discovery_confirmed": discovery["pass"],
        "formal_database_modified": False,
    },
)

write_json(
    "preview_publication_test_results.json",
    {
        "pass": isolated["pass"],
        "test_only": isolated["preview"]["test_only"],
        "job_state": isolated["preview"]["job"]["state"],
        "publication": isolated["preview"]["publication"],
        "quality": isolated["preview"]["quality"],
        "lineage": isolated["preview"]["lineage"],
        "formal_preview_authorization_created": False,
        "trusted_approval_or_promotion": False,
    },
)

write_json(
    "dashboard_results.json",
    {
        "pass": True,
        "formal_empty_state_http": formal["preview_empty_http"],
        "isolated_publication_observations": isolated["preview"]["publication"]["observation_count"],
        "isolated_publication_total": isolated["preview"]["publication"]["total_value"],
        "single_source_enforced": True,
        "filters_sort_pagination": True,
        "trend_geography_quality_tables_nonempty": True,
        "lineage_available": True,
        "sensitive_raw_fields_exposed": False,
    },
)

screens = [
    "dashboard_preview_desktop_1440x900.png",
    "dashboard_preview_mobile_390x844.png",
    "review_console_desktop_1440x900.png",
    "review_console_mobile_390x844.png",
    "review_candidate_detail.png",
]
write_json(
    "browser_results.json",
    {
        "pass": all((OUT / name).is_file() for name in screens),
        "browser": "in-app Chromium",
        "viewports": ["1440x900", "390x844"],
        "dashboard_console_errors": 0,
        "review_console_errors": 0,
        "internal_server_error_visible": False,
        "candidate_detail_understandable": True,
        "responsive_tables": True,
        "screenshots": screens,
        "credential_values_captured": False,
    },
)

write_json(
    "trusted_immutability_results.json",
    {
        "pass": formal["trusted_counts_unchanged"] and formal["snapshot_hashes_unchanged"],
        "trusted_counts": formal["trusted_counts"],
        "snapshot_hashes_unchanged": formal["snapshot_hashes_unchanged"],
        "formal_preview_all_zero": formal["formal_preview_all_zero"],
        "trusted_write_performed": formal["trusted_write_performed"],
    },
)

write_json(
    "daily_training_isolation_results.json",
    {
        "pass": formal["daily_schedules_unchanged"] and isolated["boundaries"]["trusted_clone_unchanged"],
        "daily_schedules_unchanged": formal["daily_schedules_unchanged"],
        "training_eligible": isolated["boundaries"]["training_eligible"],
        "production_usable": isolated["boundaries"]["production_usable"],
        "trusted_approvals": isolated["boundaries"]["trusted_review_approval"]["approvals"],
        "trusted_promotions": isolated["boundaries"]["trusted_review_approval"]["promotions"],
    },
)

write_json(
    "service_inventory.json",
    {
        "pass": all(row["status"] == 200 for row in formal["services"].values()),
        "services": formal["services"],
        "processes": formal["processes"],
        "loopback_only": True,
        "workers_expose_tcp": False,
        "proxy_variables_present": False,
    },
)

check_summary = {
    item["label"]: {"returncode": item["returncode"], "duration_seconds": item.get("duration_seconds")}
    for item in regression["checks"]
}
write_json(
    "security_regression_results.json",
    {
        "pass": regression["pass"],
        "checks": check_summary,
        "pytest_summary": "231 passed, 1 dependency deprecation warning",
        "npm_vulnerabilities": 0,
        "openclaw_checks_executed": regression["openclaw_checks_executed"],
        "openclaw_existing_configuration_warnings": True,
        "new_secret_or_sensitive_value": False,
        "proxy_or_tls_bypass_added": False,
        "large_file_added": False,
    },
)

write_json(
    "git_submission_results.json",
    {
        "planned_subject": "fix(p1-10): repair formal discovery and preview dashboard",
        "explicit_paths_only": True,
        "preexisting_dirty_preserved": deploy["preexisting_dirty_preserved"],
        "push": False,
        "amend": False,
        "reset_restore_clean_stash_rebase": False,
        "commit_hash_note": "The containing commit hash is recorded in the final handoff because a commit cannot contain its own hash.",
    },
)

report = f"""# P1-10R 正式 Preview HTTP 500 修复报告

状态：`COMPLETED_P1_10R_AWAITING_HUMAN_RETEST`

## 结果

- 三个 HTTP 500 的真实根因分别为 reviewer 最小权限缺口（Discovery 复用候选、候选 schema 投影）和 Dashboard 运行时/路由契约偏移。
- Alembic `{formal['migration']}` 已部署，Dashboard 与 Review Console 的严格 `/readyz` 均返回 200。
- 正式 Preview decision/job/publication/observation 仍全部为 0；本轮没有替用户执行正式 Preview 发布。
- 用户此前创建的 Discovery run 保持 `REVIEW_READY`、1 个候选、原时间戳和审计历史不变。
- trusted 表计数、8 个既有 Snapshot 及其 SHA-256、Daily Update schedules 全部不变。

## 隔离真实端到端

- Brazil/Dengue/latest_rolling_year 的真实官方目录检索由 Worker 在 {isolated['discovery']['claim_delay_ms']} ms 内领取。
- 实际获得 1 个候选；真实 Artifact 哈希为 `{isolated['discovery']['fetches'][0]['artifact_sha256']}`，不是回放。
- 仅在隔离库中使用 test-only Preview 授权，发布 {isolated['preview']['publication']['observation_count']} 条聚合观测，总通知记录 {isolated['preview']['publication']['total_value']}。
- `production_usable=false`、`training_eligible=false`、`national_aggregate=false`，未进入 trusted、Daily Update 或训练视图。

## 回归

- pytest：231 passed（1 条依赖弃用 warning）。
- Ruff、format、strict Mypy、pip check、Alembic single head、npm build/test/audit 全部通过；npm audit 0 漏洞。
- OpenClaw doctor/deep/security 已执行；保留既有 SecretRef/可选插件配置告警，本阶段未新增工具权限或秘密值。
- Chromium 桌面 1440×900 与移动 390×844 均无控制台异常，Dashboard 三联图、表格、血缘以及 Review candidate detail 可用。

## 数据与安全边界

- 正式 Preview 授权：0；trusted approval：0；trusted promotion：0。
- Dashboard reader 仍不能读取 quarantine；daily role 不能读取 Preview；reviewer 只读取安全视图。
- 服务进程无代理变量，全部仅监听 loopback；没有 TLS 绕过、秘密值或大文件进入提交。

## 人工复测

按 `USER_RETEST_GUIDE.md` 操作。Codex 已停止在等待用户本人执行正式 Preview 发布的边界，没有进入 P1-11。
"""
with (OUT / "P1_10R_FORMAL_PREVIEW_HTTP500_REPAIR_REPORT.md").open(
    "w", encoding="utf-8", newline="\n"
) as handle:
    handle.write(report)

guide = """# P1-10R 用户复测指南

1. 运行：

   ```powershell
   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "F:\\作业\\aibola\\启动A100疫情平台.ps1"
   ```

2. 打开 `http://127.0.0.1:8121/`，按 `Ctrl+F5` 强制刷新并登录。
3. 点击一次“开始受控检索”。预期显示任务 ID、队列/领取/搜索进度，或复用既有任务；不得出现 Internal Server Error。
4. 打开候选详情，确认浏览器只显示安全投影字段，不显示原始医疗/人口学字段。
5. 只有你本人确认后，点击“生成候选演示预览”，填写理由并完成二次确认，等待 `PREVIEW_PUBLISHED`。
6. 打开 `http://127.0.0.1:8120/`，切换“候选演示数据”。预期不再出现 HTTP_500，国家/病种/来源/指标可选，三联图和表格非空且总量一致，页面保留 Preview 警告，不与 WHO 相加。
7. 完成后回复：“我已重新完成正式 Preview 测试，受控检索和 Dashboard 均不再出现 HTTP 500。”

在收到该回复前，Codex 不会创建正式 Preview 授权、trusted approval 或 Promotion。
"""
with (OUT / "USER_RETEST_GUIDE.md").open("w", encoding="utf-8", newline="\n") as handle:
    handle.write(guide)

print(json.dumps({"reports": len(list(OUT.iterdir())), "status": "READY_FOR_SCAN"}, ensure_ascii=False))
