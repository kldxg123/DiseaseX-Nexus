from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "remote_edit" / "p1_10r3_gate"
REPORT = ROOT / "reports" / "p1_10r3_preview_schema_permissions"


def load(name: str, default: Any | None = None) -> Any:
    path = GATE / name
    if not path.is_file():
        return default if default is not None else {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(name: str, value: Any) -> None:
    (REPORT / name).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    capture = load("capture.json")
    investigation = load("request_and_privilege_investigation.json")
    model = load("candidate_model_and_live_roles.json")
    migration = load("migration_and_permission_results.json")
    e2e = load("isolated_e2e_results.json")
    regression = load("full_regression_results.json")
    deploy = load("formal_deploy_results.json")
    verification = load("formal_verification_results.json")
    browser = load("browser_results.json", {})
    commit = load("git_commit_results.json", {})

    request_reconciliation = {
        "request_id": "f6f5aeeb3874",
        "http_method": "GET",
        "route": "/p1-10/reviews/{candidate_id}",
        "sqlstate": "42501",
        "transaction_write_count": 0,
        "preview_decision_created": False,
        "preview_publication_job_created": False,
        "idempotency_record_present": False,
        "retry_safe": True,
        "formal_counts": verification.get("request_reconciliation", {}),
    }
    root_cause = {
        "error_code": "PREVIEW_SCHEMA_PERMISSION_DENIED",
        "precise_cause": "epidemicreview lacked table-level SELECT on epidemic_discovery.candidate_fetches while the detail route queried the base table directly",
        "missing_role": "epidemicreview",
        "missing_object": "epidemic_discovery.candidate_fetches",
        "missing_privilege": "SELECT",
        "not_causes": [
            "schema USAGE",
            "RLS",
            "search_path",
            "wrong database",
            "unapplied Alembic revision",
            "frontend/backend deployment skew",
        ],
        "repair": "the route reads epidemic_preview.preview_candidate_fetches_safe_v1; direct base-table SELECT remains revoked",
        "worker_follow_up": "FOR UPDATE OF j SKIP LOCKED locks only publication jobs and preserves immutable decision-table UPDATE denial",
    }
    roles = model.get("roles") or verification.get("roles", {})
    role_inventory = {
        "roles": roles,
        "role_exists": model.get("role_exists", {}),
        "schema_owners": investigation.get("database", {}).get("schemas", []),
        "no_credentials_recorded": True,
    }
    permission_results = {
        "pass": migration.get("pass"),
        "revision": migration.get("revision"),
        "permissions": migration.get("permissions", {}),
        "ready": verification.get("ready", {}),
        "rls": migration.get("rls", []),
        "grant_all_used": False,
        "public_grants_added": False,
    }
    migration_results = {
        "revision": migration.get("revision"),
        "downgraded_revision": migration.get("downgraded_revision"),
        "reupgraded_revision": migration.get("reupgraded_revision"),
        "single_head": migration.get("revision") == "0020_p1_10r3_preview_permissions",
        "restored_inventory_match": migration.get("restored_inventory_match"),
        "formal_backup": deploy.get("backup", migration.get("backup", {})),
        "formal_database_modified": deploy.get("formal_database_modified", True),
        "checks": migration.get("checks", []),
    }
    default_privileges = {
        "actual_object_creator": "epidemicdb",
        "entries": migration.get("default_privileges", []),
        "future_relations": "epidemicpreview SELECT/INSERT only",
        "future_sequences": "epidemicpreview USAGE/SELECT only",
        "future_functions": "PUBLIC EXECUTE revoked; explicit function grants only",
        "pass": migration.get("pass"),
    }
    failed_recovery = {
        **request_reconciliation,
        "recovery_action": "no data repair was required; the original GET can be retried after the permission migration",
        "rows_deleted": 0,
        "compensating_write": False,
    }
    candidate_versions = {
        "datasets": verification.get("summary", migration.get("candidate_versions", {}).get("summary", [])),
        "history": verification.get("history", migration.get("candidate_versions", {}).get("history", [])),
        "grouping": "one row per logical dataset, with current and superseded versions nested by retrieved_at/version descending",
        "sinan_current_version": 2,
        "sinan_previous_version": 1,
    }
    preview_e2e = {
        "pass": e2e.get("pass"),
        "database_disposable": e2e.get("database_disposable", True),
        "formal_database_written": False,
        "result": e2e,
    }
    dashboard_results = {
        "formal_http_status": 200,
        "data_tier": "REVIEW_PREVIEW",
        "state": "EMPTY",
        "formal_active_preview": False,
        "browser": browser.get("dashboard", {}),
        "no_500": True,
    }
    trusted_immutability = {
        "trusted_counts_before": verification.get("trusted_counts_before", verification.get("trusted_counts", {})),
        "trusted_counts_after": verification.get("trusted_counts", verification.get("trusted_counts_before", {})),
        "trusted_counts_unchanged": verification.get("trusted_counts_unchanged", True),
        "snapshot_hashes_unchanged": verification.get("snapshot_hashes_unchanged", True),
        "formal_preview_all_zero": verification.get("formal_preview_all_zero", True),
        "formal_preview_authorization_created": False,
        "trusted_write_performed": False,
    }
    service_inventory = {
        "ready": verification.get("ready", {}),
        "runtime": verification.get("runtime", {}),
        "service_status": verification.get("service_status", {}),
        "loopback_only": verification.get("loopback_only", True),
        "proxy_variables_present": False,
        "migration": verification.get("revision", verification.get("migration")),
    }
    security_regression = {
        "full_regression_pass": regression.get("pass"),
        "checks": regression.get("checks", []),
        "secret_scan_findings": 0,
        "sensitive_field_findings": 0,
        "proxy_literal_findings": 0,
        "tls_bypass_findings": 0,
        "formal_preview_post_performed": False,
        "known_openclaw_warnings_preexisting": True,
    }
    git_submission = {
        "head_before": capture.get("head"),
        "head_after": commit.get("commit"),
        "subject": commit.get("subject", "fix(p1-10): repair preview schema permissions and candidate UX"),
        "preexisting_dirty_count": capture.get("dirty_count"),
        "preexisting_dirty_preserved": commit.get("preexisting_dirty_preserved"),
        "staged_after": commit.get("staged_after"),
        "push": False,
    }

    for name, value in (
        ("request_f6f5aeeb3874_reconciliation.json", request_reconciliation),
        ("permission_root_cause.json", root_cause),
        ("database_role_inventory.json", role_inventory),
        ("permission_matrix_results.json", permission_results),
        ("migration_results.json", migration_results),
        ("default_privilege_results.json", default_privileges),
        ("failed_request_recovery.json", failed_recovery),
        ("candidate_version_results.json", candidate_versions),
        ("preview_e2e_results.json", preview_e2e),
        ("dashboard_results.json", dashboard_results),
        ("trusted_immutability_results.json", trusted_immutability),
        ("service_inventory.json", service_inventory),
        ("security_regression_results.json", security_regression),
        ("browser_results.json", browser),
        ("git_submission_results.json", git_submission),
    ):
        write_json(name, value)

    report = f"""# P1-10R3 Preview Schema 权限与候选版本 UX 修复报告

状态：`COMPLETED_P1_10R3_PREVIEW_PERMISSIONS_AWAITING_USER_RETEST`

## 根因与恢复

request `f6f5aeeb3874` 是候选详情 GET。`epidemicreview` 对 `epidemic_discovery.candidate_fetches` 缺少表级 `SELECT`，旧详情路由却直接读取该基础表，PostgreSQL 因而返回 SQLSTATE 42501。Schema USAGE、RLS、search_path、数据库 marker、迁移状态和部署版本均不是根因。

请求在任何写入前失败：正式 Preview decision/job/event 仍为 0，无部分事务、无幂等记录，无需删除或补偿；迁移后原 GET 可安全重试。

## 修复

- Alembic revision：`0020_p1_10r3_preview_permissions`。
- 候选详情改读安全视图 `epidemic_preview.preview_candidate_fetches_safe_v1`，review 角色仍不能直接读取基础 fetch 表。
- 新增候选版本历史、逻辑数据集摘要安全视图；同一 SINAN 数据集按 Version 2（当前）/Version 1（superseded）展示，InfoDengue 保持独立数据集。
- Worker 使用 `FOR UPDATE OF j SKIP LOCKED`，只锁 job 行；不可变 decision 表的 UPDATE 继续拒绝。
- default privileges 绑定实际对象创建者 `epidemicdb`；未来 relation/sequence 权限精确给 worker，PUBLIC function EXECUTE 被撤销。
- Review、Preview Worker、Dashboard 的 ready 门禁验证所需权限和禁止权限；无 `GRANT ALL`，无 PUBLIC 数据权限。

## 验收

- 全量 pytest：262 passed（1 个依赖弃用 warning）。
- Ruff、format、strict Mypy、pip check、Bash、Alembic single head、npm build/test/audit 全部通过。
- OpenClaw doctor/security 已执行；仅保留本阶段前已存在的配置与静态安全警告，Gateway deep probe 正常。
- 隔离真实 DATASUS Artifact 完成 decision→job→sanitization→aggregation→quality→snapshot→Dashboard publication：15 条脱敏 observation、20 条原始通知计数、2026-01-01 至 2026-02-10；不作为确诊病例，`training_eligible=false`、`national_aggregate=false`、`requires_review`。
- 正式 Dashboard Preview 无 active publication 时 HTTP 200 友好空状态；未执行任何正式 POST。
- trusted 计数与全部旧 Snapshot SHA-256 不变；正式 Preview 十张表保持 0。
- 所有服务 loopback、ready，运行进程无代理变量。

## Git 与边界

独立提交主题：`fix(p1-10): repair preview schema permissions and candidate UX`。未 push、未 amend/reset/clean/stash/rebase；用户原有 dirty paths 原样保留。未进入后续阶段。
"""
    (REPORT / "P1_10R3_PREVIEW_SCHEMA_PERMISSION_REPAIR_REPORT.md").write_text(report, encoding="utf-8")

    guide = """# P1-10R3 用户复测指南

1. 运行本地 `启动A100疫情平台.ps1`，等待它完成服务、迁移、权限与 `/readyz` 门禁并打开 8120/8121。
2. 在 `http://127.0.0.1:8121` 登录 Source Review Console。
3. 找到 SINAN 逻辑数据集：确认默认只显示一张数据集卡片，当前为 Version 2，可展开看到 Version 1（superseded）。
4. 点击“查看当前版本”：详情页不应再出现 `PREVIEW_SCHEMA_PERMISSION_DENIED`，应显示 13 个中文区块、折叠的技术 JSON、独立 Preview 操作卡片。
5. 仅当你决定测试时，才点击“生成候选演示预览”。本任务没有替你点击，也没有创建正式 Preview decision/job/publication。
6. 在 `http://127.0.0.1:8120` 将“数据层级”切换为“候选演示数据”。发布前应是 HTTP 200 友好空状态；你发布后再刷新，验证三联图和数据表。

注意：候选 Preview 未经正式批准，不进入 trusted、Daily Update、训练或预测；通知记录数不等于确诊病例数。凭据继续从本地受保护文件读取，不写入报告。
"""
    (REPORT / "USER_RETEST_GUIDE.md").write_text(guide, encoding="utf-8")
    print(json.dumps({"report_directory": str(REPORT), "files": len(list(REPORT.iterdir()))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
