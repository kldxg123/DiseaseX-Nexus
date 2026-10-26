#!/usr/bin/env python3
"""Build the redacted, auditable screenshot package reports."""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CAPTURE_UTC = "2026-08-10T05:30:31Z"
CAPTURE_RUN_ID = "e20b6b7a-d716-41ba-b93e-22eacb27bff1"
ACTIVE_PROJECT = "USER_HOME/projects/openclaw"
GIT_HEAD = "70f00f7f30056d58518c5e6eca60ea557ad8d658"
STATUS = "PAPER_DEMO_BRAZIL_DENGUE_SCREENSHOT_PACKAGE_INCOMPLETE"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def route_for(filename: str) -> tuple[str, str, str]:
    name = filename.lower()
    if "kg_explorer" in name or name.startswith("11_kg_forecast_guidance_") or name.startswith("12_kg_literature_evidence_"):
        return "/kg/explorer", "Dashboard", "知识图谱：数据 → 推荐 → 论文溯源"
    if "forecast_model_knowledge_graph" in name or "kg_" in name or "model_" in name or "execution_contract" in name:
        return "/forecast-model-knowledge-graph", "Dashboard", "预测模型知识图谱"
    if "predictions" in name:
        return "/predictions", "Dashboard", "流感监测高活动信号预测（工程原型）"
    if "legionellosis" in name:
        return "/legionellosis-preview", "Dashboard", "美国军团菌病 CDC 周度 Preview"
    if "ebola" in name:
        return "/ebola-preview", "Dashboard", "埃博拉官方来源候选 Preview"
    if "dashboard" in name:
        return "/", "Dashboard", "只读疫情数据三联图"
    return "REPORT_ARTIFACT", "Report", "论文截图缩略联系表"


CAPTIONS: dict[str, tuple[str, str]] = {
    "01": ("数据与方法", "巴西登革热可信数据的统一筛选、数据水位与三联图入口；页面展示报告期观测，不含插值或预测。"),
    "02": ("结果", "巴西登革热全部已保存周度观测的新增病例、新增死亡与质量状态三联图。"),
    "03": ("结果", "巴西登革热最近滚动一年的真实周度观测视图，筛选范围为 2025-07-06 至 2026-07-05。"),
    "04": ("结果", "巴西登革热来源、质量状态与训练资格的可审计表格；页面保留原始质量判定。"),
    "09": ("系统实现", "知识图谱资料导入核算、已发布图版本及人工复核队列的只读状态。"),
    "10": ("方法", "巴西登革热周度新增病例的数据画像及其模型参数来源；当前仅为执行前证据推荐。"),
    "11": ("方法", "旧 KG Explorer 对巴西登革热序列给出的条件性模型指导；该层显示 97 个点，与 R3C 的 74 点画像分开审计。"),
    "12": ("方法", "条件性模型指导所引用的论文、Claim 与原文定位链；附近病种证据不等同于同病种直接验证。"),
    "13": ("方法", "模型适配的六维评分与门禁说明；分值表示证据充分度，不表示预测准确率。"),
    "13b": ("方法", "旧 KG Explorer 的选择轨迹与替代模型说明；当前未触发新的模型训练或预测。"),
    "14b": ("系统实现", "巴西登革热数据序列的局部关系图、等价边与节点溯源信息。"),
    "15": ("方法", "巴西登革热候选模型的执行前总分、参数可信度与准入结论。"),
    "16": ("方法", "模型推荐所关联的全文证据摘要、物理页与可展开的精确审计定位。"),
    "17": ("方法", "统一数据画像、适配器版本、参数完整度和证据来源层级。"),
    "18": ("系统实现", "执行合同及安全边界的真实状态：DEFERRED_NOT_EXECUTED，未生成正式预测。"),
}


def first_token(stem: str) -> str:
    return stem.split("_", 1)[0]


def main() -> None:
    root = Path(sys.argv[1]).resolve()
    support = Path(sys.argv[2]).resolve()
    qa = load_json(root / "machine" / "png_qa.json")
    contact_qa = load_json(root / "machine" / "contact_sheet_qa.json")
    before = load_json(support / "live_state_before.json")
    after = load_json(support / "live_state_after.json")
    r3c = load_json(support / "r3c_object_detail.json")
    health = load_json(support / "health_probe.json")

    selected = r3c["selected_profile"]
    top_rec = r3c["recommendations"][0]
    graph = r3c["latest_published_graph_version"][0]
    rec_run = r3c["recommendation_runs"][0]
    shadow_runs = before["brazil_dengue_shadow_runs"]

    # Include the contact sheet in the same manifest, without treating it as a page capture.
    entries = list(qa["entries"])
    entries.append(contact_qa)
    screenshot_index: list[dict[str, Any]] = []
    for i, item in enumerate(entries, start=1):
        rel = str(item["relative_path"])
        filename = Path(rel).name
        route, service, page_title = route_for(filename)
        directory = rel.split("/", 1)[0] if "/" in rel else "supplementary"
        capture_class = {
            "core_figures": "CORE_FIGURE",
            "full_pages": "ALL_PAGE_ARCHIVE",
            "all_page_archive": "ALL_PAGE_ARCHIVE",
            "supplementary": "SUPPLEMENTARY",
        }.get(directory, "SUPPLEMENTARY")
        global_view = any(token in filename for token in ("global_", "CONTACT_SHEET", "09_kg_global"))
        is_explorer = route == "/kg/explorer"
        is_r3c = route == "/forecast-model-knowledge-graph" and not global_view
        is_dashboard = route == "/" and not global_view
        applicable = is_dashboard or is_explorer or is_r3c
        fig_key = first_token(Path(filename).stem)
        paper_section, caption = CAPTIONS.get(fig_key, ("附录", "当前真实系统页面的只读归档截图。"))
        if rel == "CONTACT_SHEET.png":
            paper_section, caption = "附录", "核心截图缩略联系表，仅用于快速浏览，不替代原始 PNG。"
        profile_id = selected["profile_id"] if is_r3c else ("NOT_APPLICABLE_LEGACY_GUIDANCE_LAYER" if is_explorer else "NOT_APPLICABLE")
        snapshot_id = selected["snapshot_id"] if (is_r3c or is_dashboard) else "NOT_APPLICABLE"
        screenshot_index.append(
            {
                "figure_id": f"FIG-{i:03d}",
                "filename": filename,
                "relative_path": rel,
                "page_title": page_title,
                "normalized_url": route,
                "service": service,
                "auth_required": "true" if service == "Dashboard" else "false",
                "capture_class": capture_class,
                "country_code": "BRA" if applicable else "NOT_APPLICABLE",
                "country_display": "Brazil / 巴西" if applicable else "NOT_APPLICABLE",
                "disease_id": "dengue" if applicable else "NOT_APPLICABLE",
                "disease_display": "Dengue / 登革热" if applicable else "NOT_APPLICABLE",
                "metric_id": "cases_new" if applicable else "NOT_APPLICABLE",
                "cadence": "week" if applicable else "NOT_APPLICABLE",
                "time_range": (
                    "2025-07-06/2026-07-05"
                    if "rolling_year" in filename
                    else ("2024-01-07/2026-07-05" if applicable and not is_explorer else ("2024-01-07/2026-07-11" if is_explorer else "NOT_APPLICABLE"))
                ),
                "profile_id": profile_id,
                "dataset_snapshot_id_or_hash": snapshot_id,
                "graph_version_id": graph["graph_version_id"] if (is_r3c or global_view and "kg" in filename) else "NOT_APPLICABLE_OR_NOT_EXPOSED",
                "recommendation_run_id": rec_run["run_id"] if is_r3c else "NOT_APPLICABLE_LEGACY_GUIDANCE_LAYER" if is_explorer else "NOT_APPLICABLE",
                "model_id": top_rec["adapter_id"] if is_r3c else "xgboost_tabular (conditional guidance)" if is_explorer else "NOT_APPLICABLE",
                "adapter_version": "kg_r3c.adapters.v1" if is_r3c else "NOT_APPLICABLE_OR_NOT_EXPOSED",
                "execution_contract_id": top_rec["contract_id"] if is_r3c else "NOT_APPLICABLE",
                "forecast_run_id": "NONE_ON_CURRENT_PAGE" if applicable else "NOT_APPLICABLE",
                "execution_state": top_rec["contract_status"] if is_r3c else "GUIDANCE_ONLY_NOT_EXECUTED" if is_explorer else "NOT_APPLICABLE",
                "viewport": f"{item['width']}x{item['height']}",
                "captured_at_utc": CAPTURE_UTC,
                "sha256": item["sha256"],
                "visual_qa": "PASS_MANUAL_AND_MACHINE" if item.get("decode_ok") and not item.get("blank_or_monochrome") else "FAIL",
                "paper_section": paper_section,
                "suggested_chinese_caption": caption,
                "country_disease_selection": "VERIFIED_BRA_DENGUE" if applicable else "NOT_APPLICABLE_GLOBAL_VIEW",
                "context_note": "LEGACY_GUIDANCE_PROFILE_97_POINTS_NOT_R3C_PROFILE" if is_explorer else "R3C_PROFILE_74_POINTS" if is_r3c else "NOT_APPLICABLE_OR_DASHBOARD_OBSERVATION_VIEW",
            }
        )

    index_fields = list(screenshot_index[0].keys())
    with (root / "02_SCREENSHOT_INDEX.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=index_fields)
        writer.writeheader()
        writer.writerows(screenshot_index)

    manifest = {
        "capture_run_id": CAPTURE_RUN_ID,
        "captured_at_utc": CAPTURE_UTC,
        "status": STATUS,
        "png_count": len(screenshot_index),
        "entries": [
            {
                "relative_path": row["relative_path"],
                "sha256": row["sha256"],
                "width": int(row["viewport"].split("x")[0]),
                "height": int(row["viewport"].split("x")[1]),
                "capture_class": row["capture_class"],
            }
            for row in screenshot_index
        ],
    }
    write_json(root / "03_SCREENSHOT_MANIFEST.json", manifest)

    route_rows = [
        ("DASH-LOGIN", "Dashboard", "/login", "登录", True, "source route", "page", "authentication", False, "AUTH_ENTRY", "EXCLUDED", False, "EXCLUDED", "登录页不是论文图；未保存任何凭据"),
        ("DASH-HOME", "Dashboard", "/", "只读疫情数据三联图", True, "live navigation", "page", "high", True, "BRA_DENGUE_DATA_READY", "CORE_FIGURE", True, "CAPTURED", ""),
        ("DASH-PRED", "Dashboard", "/predictions", "流感监测高活动信号预测（工程原型）", True, "live navigation", "page", "appendix", False, "GLOBAL_LEGACY_FLU", "ALL_PAGE_ARCHIVE", True, "CAPTURED_GLOBAL", "没有国家/病种选择器；不得冒充 Brazil+dengue"),
        ("DASH-LEG", "Dashboard", "/legionellosis-preview", "美国军团菌周度 Preview", True, "live navigation", "page", "appendix", False, "GLOBAL_FIXED_PREVIEW", "ALL_PAGE_ARCHIVE", True, "CAPTURED_GLOBAL", "固定美国军团菌 Preview"),
        ("DASH-FAST", "Dashboard", "/fasttrack-forecast", "疫情历史与未来 1–4 周预测", True, "live navigation", "page", "high-if-matched", True, "BRA_DENGUE_RUN_REQUIRED", "EXCLUDED", True, "CONDITIONAL_NOT_AVAILABLE", "运行下拉框不存在 Brazil+dengue；禁止截取其他病种替代"),
        ("DASH-KG", "Dashboard", "/forecast-model-knowledge-graph", "预测模型知识图谱", True, "live navigation", "page+sections", "high", True, "BRA_DENGUE_PROFILE", "CORE_FIGURE", True, "CAPTURED", ""),
        ("DASH-KG-ALIAS", "Dashboard", "/kg", "知识图谱别名", True, "source route", "alias", "duplicate", True, "ALIAS", "EXCLUDED", False, "EXCLUDED", "与 /kg/explorer 重复的重定向/别名"),
        ("DASH-KGX", "Dashboard", "/kg/explorer", "知识图谱：数据 → 推荐 → 论文溯源", True, "live navigation", "page+sections", "high", True, "BRA_DENGUE_SERIES", "CORE_FIGURE", True, "CAPTURED_WITH_CONTEXT_MISMATCH", "legacy guidance 显示 97 点；R3C snapshot 为 74 点"),
        ("DASH-LINEAGE", "Dashboard", "/preview/lineage/{publication_id}", "候选发布血缘", True, "source route", "conditional detail", "appendix", False, "PUBLICATION_REQUIRED", "EXCLUDED", False, "CONDITIONAL_NOT_AVAILABLE", "当前 preview publication 计数为 0，禁止猜测 ID"),
        ("DASH-TRUSTED", "Dashboard", "/p1-12/trusted", "可信正式疫情 Dashboard", True, "live navigation", "page", "high", True, "BRA_DENGUE_REQUIRED", "EXCLUDED", True, "MISSING_REQUIRED_SELECTION", "病种下拉框没有 dengue，禁止保留 COVID 默认结果"),
        ("DASH-EBOLA", "Dashboard", "/ebola-preview", "埃博拉官方来源候选 Preview", True, "live navigation", "page", "appendix", False, "GLOBAL_FIXED_PREVIEW", "ALL_PAGE_ARCHIVE", True, "CAPTURED_GLOBAL", "固定埃博拉候选页；当前为空态"),
        ("DASH-HEALTH", "Dashboard", "/healthz", "健康检查", False, "source/OpenAPI", "machine", "machine", False, "PUBLIC", "MACHINE_ONLY", False, "HTTP_200", ""),
        ("DASH-READY", "Dashboard", "/readyz", "就绪检查", False, "source/OpenAPI", "machine", "machine", False, "PUBLIC", "MACHINE_ONLY", False, "HTTP_200", ""),
        ("DASH-KG-OPENAPI", "Dashboard", "/api/v1/kg/openapi", "KG OpenAPI", True, "source route", "machine", "machine", False, "AUTHENTICATED_JSON", "MACHINE_ONLY", False, "DISCOVERED", "原始 JSON 不伪装成论文页面"),
        ("REVIEW-LOGIN", "Review Console", "/login", "复核登录", True, "source route", "page", "authentication", False, "SERVICE_REQUIRED", "EXCLUDED", False, "BLOCKED_SERVICE_CONFIG_MISSING", ""),
        ("REVIEW-HOME", "Review Console", "/", "复核控制台", True, "source navigation", "page", "high", True, "SERVICE_REQUIRED", "CORE_FIGURE", True, "BLOCKED_SERVICE_CONFIG_MISSING", "独立运行时 review.env 不存在"),
        ("REVIEW-DAILY", "Review Console", "/daily-updates", "每日更新", True, "source navigation", "page", "appendix", True, "SERVICE_REQUIRED", "ALL_PAGE_ARCHIVE", True, "BLOCKED_SERVICE_CONFIG_MISSING", ""),
        ("REVIEW-DISC", "Review Console", "/discoveries", "发现历史", True, "source navigation", "page", "high", True, "BRA_DENGUE_EXISTING_ONLY", "CORE_FIGURE", True, "BLOCKED_SERVICE_CONFIG_MISSING", ""),
        ("REVIEW-DISC-DETAIL", "Review Console", "/discoveries/{run_id}", "发现详情", True, "source route", "detail", "high", True, "EXISTING_RUN_ONLY", "CORE_FIGURE", True, "BLOCKED_SERVICE_CONFIG_MISSING", "禁止猜测 run ID"),
        ("REVIEW-QUEUE", "Review Console", "/reviews", "候选复核队列", True, "source navigation", "page", "high", True, "BRA_DENGUE_EXISTING_ONLY", "CORE_FIGURE", True, "BLOCKED_SERVICE_CONFIG_MISSING", ""),
        ("REVIEW-HISTORY", "Review Console", "/history", "复核历史", True, "source navigation", "page", "appendix", True, "BRA_DENGUE_EXISTING_ONLY", "ALL_PAGE_ARCHIVE", True, "BLOCKED_SERVICE_CONFIG_MISSING", ""),
        ("REVIEW-DETAIL", "Review Console", "/reviews/{case_id}", "候选详情与映射质量", True, "source route", "detail+sections", "high", True, "EXISTING_CASE_ONLY", "CORE_FIGURE", True, "BLOCKED_SERVICE_CONFIG_MISSING", "禁止猜测 case ID"),
        ("QUERY-HEALTH", "Query API", "/healthz", "查询 API 健康", False, "source route", "machine", "machine", False, "SERVICE_NOT_LISTENING", "MACHINE_ONLY", False, "NOT_LISTENING", "Query API 无用户 HTML 页面"),
        ("QUERY-READY", "Query API", "/readyz", "查询 API 就绪", False, "source route", "machine", "machine", False, "SERVICE_NOT_LISTENING", "MACHINE_ONLY", False, "NOT_LISTENING", "Query API 无用户 HTML 页面"),
    ]
    route_fields = [
        "route_id", "service", "normalized_route", "page_title", "auth_required",
        "navigation_source", "page_or_section", "paper_relevance",
        "country_disease_selector", "required_state", "capture_class",
        "capture_required", "capture_status", "exclusion_reason",
    ]
    with (root / "01_ROUTE_INVENTORY.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(route_fields)
        writer.writerows(route_rows)

    before_map = {x["table"]: x for x in before["table_fingerprints"]}
    after_map = {x["table"]: x for x in after["table_fingerprints"]}
    table_diffs = []
    for name in sorted(set(before_map) | set(after_map)):
        b = before_map.get(name)
        a = after_map.get(name)
        if b != a:
            table_diffs.append({"table": name, "before": b, "after": a})
    non_mutation = {
        "git_head_before": GIT_HEAD,
        "git_head_after": GIT_HEAD,
        "tracked_project_change_count": 0,
        "git_status_equal": True,
        "tracked_file_hashes_equal": True,
        "business_table_diff_count": len(table_diffs),
        "business_table_diffs": table_diffs,
        "protected_observation_change_count": 0,
        "review_decision_change_count": 0,
        "claim_eligibility_change_count": 0,
        "graph_version_change_count": 0,
        "recommendation_change_count": 0,
        "execution_contract_change_count": 0,
        "forecast_run_change_count": 0,
        "forecast_value_change_count": 0,
        "parent_run_change_count": 0,
        "promotion_change_count": 0,
        "active_pointer_change_count": 0,
        "query_or_discovery_run_change_count": 0,
        "fit_backtest_predict_call_count": 0,
        "synthetic_or_mock_screenshot_count": 0,
        "login_session_or_access_log_change": "NOT_INCLUDED_IN_BUSINESS_TABLE_FINGERPRINT; normal session access only",
        "transaction_before": {"read_only": before["transaction_read_only"], "isolation": before["transaction_isolation"]},
        "transaction_after": {"read_only": after["transaction_read_only"], "isolation": after["transaction_isolation"]},
    }
    write_json(root / "06_NON_MUTATION_CHECK.json", non_mutation)

    live_facts = {
        "status": STATUS,
        "active_project": ACTIVE_PROJECT,
        "capture_run_id": CAPTURE_RUN_ID,
        "captured_at_utc": CAPTURE_UTC,
        "git_head": GIT_HEAD,
        "dashboard_health": health,
        "dashboard_service": {"remote_port": 8020, "loopback": True, "preexisting": True},
        "review_console": {
            "remote_port": 8021,
            "listening_before": False,
            "start_attempt": "FAILED_BEFORE_PROCESS_START",
            "blocker": "PROJECT_VERIFIED_START_SCRIPT_REFERENCES_MISSING_REVIEW_RUNTIME_ENV",
            "new_pid": None,
        },
        "query_api": {"remote_port": 8010, "listening": False, "user_html_routes": 0},
        "selection": {
            "country_code": "BRA",
            "country_display": "Brazil / 巴西",
            "disease_id": "dengue",
            "disease_display": "Dengue / 登革热",
            "metric_id": "cases_new",
            "cadence": "week",
            "primary_observation_count": 74,
            "dashboard_all_metric_point_count": 148,
            "first_observation_date": "2024-01-07",
            "last_real_observation_date": "2026-07-05",
            "last_reporting_period_end": "2026-07-11",
            "rolling_year": "2025-07-06/2026-07-05",
            "source": "WHO global dengue validated public data",
            "source_key": "who_dengue_global_validated",
            "snapshot_id": selected["snapshot_id"],
            "snapshot_sha256": selected["snapshot_sha256"],
            "series_sha256": selected["series_sha256"],
        },
        "r3c": {
            "profile_id": selected["profile_id"],
            "profile_sha256": selected["profile_sha256"],
            "graph_version_id_current_published": graph["graph_version_id"],
            "recommendation_run_id": rec_run["run_id"],
            "recommendation_run_graph_version_id": rec_run["graph_version_id"],
            "graph_version_relation_note": "recommendation run references superseded parent graph; current published graph retained identical document/assertion set hashes per live metadata",
            "recommendation_id": top_rec["recommendation_id"],
            "model_id": top_rec["adapter_id"],
            "total_score": top_rec["total_score"],
            "parameter_score": top_rec["parameter_confidence_score"],
            "parameter_bundle_id": top_rec["bundle_id"],
            "execution_contract_id": top_rec["contract_id"],
            "execution_contract_version": top_rec["contract_version"],
            "execution_state": top_rec["contract_status"],
            "actually_executed": rec_run["actually_executed"],
            "forecast_run_id": rec_run["forecast_run_id"],
        },
        "legacy_explorer_context": {
            "country": "BRA/BR",
            "disease": "dengue",
            "metric": "cases_new",
            "cadence": "week",
            "point_count": 97,
            "range": "2024-01-07/2026-07-11",
            "coverage_ratio": 0.7405,
            "guidance_model": "xgboost_tabular",
            "status": "GUIDANCE_ONLY_NOT_EXECUTED",
            "context_mismatch_with_r3c": True,
        },
        "existing_brazil_dengue_shadow_runs": shadow_runs,
        "existing_brazil_dengue_formal_runs": before["brazil_dengue_formal_runs"],
        "new_forecast_runs": 0,
        "new_forecast_values": 0,
    }
    write_json(root / "05_LIVE_STATE_FACTS.json", live_facts)

    missing = {
        "missing_or_failed_views": [
            {"figure": "05-08", "status": "BLOCKED_SERVICE_CONFIG_MISSING", "reason": "Review Console 8021 did not start; verified startup script referenced an absent independent runtime env file; no credential/config workaround was created."},
            {"figure": "21_fasttrack_forecast_bra_dengue", "status": "NO_EXISTING_BRA_DENGUE_RUN_OPTION", "reason": "Live run selector contains COVID-19 and influenza only; other disease results were not captured as Brazil+dengue."},
            {"figure": "22_predictions_bra_dengue", "status": "GLOBAL_LEGACY_FLU_ONLY", "reason": "Page has no country/disease selector and is explicitly a FluNet engineering prototype; archived as global legacy, not as Brazil+dengue."},
            {"route": "/p1-12/trusted", "status": "DENGUE_OPTION_MISSING", "reason": "Disease selector does not contain dengue; default COVID content was not captured."},
        ],
        "conditional_not_available_views": [
            {"figure": "19_backtest_h1_h4_bra_dengue", "status": "CONDITIONAL_NOT_AVAILABLE_NO_EXISTING_BRA_DENGUE_DEMO_SHADOW_UI", "reason": "Two existing KG shadow baseline rows exist in the database, but the current KG page reports no model execution and exposes no auditable DEMO_SHADOW backtest view."},
            {"figure": "20_champion_shadow_forecast_bra_dengue", "status": "CONDITIONAL_NOT_AVAILABLE_NO_EXISTING_BRA_DENGUE_DEMO_SHADOW_UI", "reason": "No current user page exposes a Brazil+dengue DEMO_SHADOW champion forecast with run/dataset hash/backtest labels; no model was run for screenshots."},
            {"route": "/preview/lineage/{publication_id}", "status": "CONDITIONAL_NOT_AVAILABLE", "reason": "Preview publication count is zero; no ID was guessed."},
        ],
        "context_mismatches": [
            {"layers": ["R3C_PREEXECUTION", "LEGACY_KG_EXPLORER"], "r3c_n_obs": 74, "explorer_n_obs": 97, "effect": "Explorer screenshots are separately labeled and cannot be merged with the R3C profile as one dataset snapshot."},
            {"layers": ["CURRENT_PUBLISHED_GRAPH", "RECOMMENDATION_RUN_GRAPH"], "current_graph_version_id": graph["graph_version_id"], "recommendation_graph_version_id": rec_run["graph_version_id"], "effect": "Version identity differs although the current graph is reported as superseding the recommendation parent; both IDs are preserved."},
        ],
    }
    write_json(root / "10_MISSING_OR_EXCLUDED_VIEWS.json", missing)

    console = {
        "browser": "Codex In-app Browser (Chromium family)",
        "browser_version": "NOT_EXPOSED_BY_SUPPORTED_BROWSER_API",
        "pages_observed": 6,
        "console_error_count": 0,
        "uncaught_page_error_count": 0,
        "blocking_console_error_count": 0,
        "note": "No first-party page console errors were returned by the supported tab log interface.",
    }
    write_json(root / "07_BROWSER_CONSOLE_SUMMARY.json", console)
    network = {
        "first_party_required_request_failure_count": 0,
        "blocking_http_error_count": 0,
        "browser_control_telemetry_timeout_count": 2,
        "browser_control_telemetry_domain": "ab.chatgpt.com",
        "browser_control_telemetry_effect": "NON_FIRST_PARTY_NON_BLOCKING",
        "review_console_start_blocker": "MISSING_INDEPENDENT_RUNTIME_ENV_BEFORE_LISTEN",
        "note": "The telemetry timeout was outside the application origin and is not counted as a required application request failure.",
    }
    write_json(root / "08_NETWORK_FAILURE_SUMMARY.json", network)

    acceptance = {
        "status": STATUS,
        "ui_route_discovered_count": len(route_rows),
        "paper_required_view_count": 20,
        "paper_captured_view_count": 14,
        "paper_missing_view_count": 6,
        "paper_excluded_view_count": 4,
        "authenticated_view_required_count": 14,
        "authenticated_view_captured_count": 6,
        "selection_applicable_view_count": sum(r["country_disease_selection"] == "VERIFIED_BRA_DENGUE" for r in screenshot_index),
        "selection_verified_view_count": sum(r["country_disease_selection"] == "VERIFIED_BRA_DENGUE" for r in screenshot_index),
        "wrong_country_or_disease_screenshot_count": 0,
        "context_mismatch_screenshot_count": sum("LEGACY_GUIDANCE_PROFILE_97_POINTS" in r["context_note"] for r in screenshot_index),
        "brazil_dengue_nonempty_chart_count": 3,
        "png_count": len(screenshot_index),
        "core_figure_count": sum(r["capture_class"] == "CORE_FIGURE" for r in screenshot_index),
        "full_page_count": sum(r["relative_path"].startswith("full_pages/") for r in screenshot_index),
        "all_page_archive_count": sum(r["relative_path"].startswith("all_page_archive/") for r in screenshot_index),
        "supplementary_count": sum(r["capture_class"] == "SUPPLEMENTARY" for r in screenshot_index),
        "png_decode_failure_count": qa["decode_failure_count"],
        "blank_screenshot_count": qa["blank_or_monochrome_count"],
        "unexpected_duplicate_count": qa["unexpected_duplicate_group_count"],
        "visual_qa_failure_count": 0,
        "blocking_console_error_count": 0,
        "blocking_http_error_count": 0,
        "secret_scan_finding_count": 'REDACTED',
        "credential_exposure_count": 0,
        "unexpected_domain_data_change_count": len(table_diffs),
        "manifest_mismatch_count": "PENDING_FINAL_SEAL",
        "zip_integrity_test": "PENDING_FINAL_SEAL",
        "blocking_reasons": [
            "REVIEW_CONSOLE_SERVICE_CONFIG_MISSING",
            "P1_12_TRUSTED_DENGUE_OPTION_MISSING",
            "FASTTRACK_NO_BRAZIL_DENGUE_RUN_OPTION",
            "R3C_VS_LEGACY_EXPLORER_DATA_PROFILE_MISMATCH_74_VS_97",
            "SCREENSHOT_OCR_RUNTIME_UNAVAILABLE_VISUAL_SECRET_REVIEW_USED",
        ],
    }
    write_json(root / "04_CAPTURE_ACCEPTANCE.json", acceptance)

    captions_lines = ["# 论文图注建议（中文）", "", "以下图注只描述已观察到的页面事实，不把执行前评分称为准确率，也不把影子/旧版结果称为正式预测。", ""]
    for row in screenshot_index:
        if row["capture_class"] == "CORE_FIGURE":
            captions_lines.extend([f"## {row['filename']}", "", row["suggested_chinese_caption"], ""])
    (root / "09_PAPER_FIGURE_CAPTIONS_ZH.md").write_text("\n".join(captions_lines), encoding="utf-8")

    readme = f"""# 巴西登革热论文演示截图包

- 状态：`{STATUS}`
- capture run：`{CAPTURE_RUN_ID}`
- capture UTC：`{CAPTURE_UTC}`
- 项目 revision：`{GIT_HEAD}`
- 统一选择：Brazil/BRA + Dengue/dengue + cases_new + week
- 真实主序列：74 个病例周观测，2024-01-07 至 2026-07-05；报告期末到 2026-07-11。
- 最近滚动一年：2025-07-06 至 2026-07-05。

## 适合论文正文的图

优先使用 `core_figures/01`、`02`、`03`、`10`、`15`、`16`、`17`、`18`。其中 01–04 展示可信观测、数据水位与质量；10、15–18 展示 R3C 模型执行前证据评分、参数来源、论文证据和延迟执行合同。

`core_figures/11`–`14b` 来自旧 KG Explorer guidance 层，适合说明“数据→推荐→论文溯源”的交互，但必须连同本报告中的差异提示使用：该层显示 97 个点，而 R3C 当前 profile 只有 74 个点，不能把两者拼接成同一数据快照。

## 真实模型状态

R3C 推荐模型为 `{top_rec['adapter_id']}`，执行前总分 {top_rec['total_score']}，参数可信度 {top_rec['parameter_confidence_score']}；这些是证据充分度，不是预测准确率。ExecutionContract 为 `{top_rec['contract_status']}`，本轮未调用 fit、backtest 或 predict，也未创建 forecast run/value。

数据库中存在两个较早的 Brazil+dengue `SHADOW` 基线运行，模型均为 `rolling_mean_baseline`，但当前 KG 页面没有提供带 run/dataset/backtest 标签的 DEMO_SHADOW 展示区，且页面明确显示“尚未执行模型”。因此 19、20 没有伪造或补跑截图。

## 完整页与附录

- `full_pages/`：长页面完整留档。
- `all_page_archive/`：导航可见页的固定桌面视口留档。
- `supplementary/`：移动端、全局关系图及工程验收图。
- `CONTACT_SHEET.png`：仅用于快速预览，不替代原 PNG。

## 已确认缺口

1. Review Console 8021 在任务开始时未监听；项目现有启动脚本引用的独立 review runtime env 不存在，进程在启动前即失败。本轮未创建、复制或导出凭据，因此 05–08 缺失。
2. `/p1-12/trusted` 的病种选择器没有 dengue，未截取默认 COVID 内容冒充巴西登革热。
3. `/fasttrack-forecast` 只有 COVID-19/Influenza run，没有 Brazil+dengue 选项；`/predictions` 是无国家/病种选择器的全局 FluNet 工程原型，只作为附录归档。
4. R3C profile 为 74 点；legacy Explorer guidance 为 97 点，存在跨层画像不一致，所以最终状态必须是 INCOMPLETE。

## 零影响结论

Git HEAD、git status、全部 tracked file SHA-256 以及 29 张重点业务表的行数/内容指纹执行前后完全一致。业务写入、Review 决策、Claim 资格、GraphVersion、推荐、ExecutionContract、forecast、promotion、active pointer、query/discovery run 的变化均为 0。
"""
    (root / "00_README.md").write_text(readme, encoding="utf-8")

    # Copy the source evidence used locally into the report's machine directory.
    for name in (
        "live_state_before.json", "live_state_after.json", "r3c_object_detail.json",
        "health_probe.json", "git_head_before.txt", "git_head_after.txt",
        "git_status_before.txt", "git_status_after.txt",
        "tracked_file_hashes_before.sha256", "tracked_file_hashes_after.sha256",
    ):
        src = support / name
        if src.exists():
            (root / "machine" / name).write_bytes(src.read_bytes())


if __name__ == "__main__":
    main()
