from __future__ import annotations

# ruff: noqa: E501
# mypy: ignore-errors
import hmac
import html
import os
import urllib.parse
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import Engine, text

from epidemic_core.p1_9.promotion import DEMO_ENV, canonical_json, sha

WHITELIST = ("dt_notific", "sg_uf_not", "id_municip", "nu_ano", "case_notification_count")


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def _banner() -> str:
    if os.environ.get("APP_ENV") == DEMO_ENV:
        return '<div class="p1-banner demo">P1-9临时演示环境——数据不会写入正式数据库</div>'
    return '<div class="p1-banner formal">正式人工复核环境——本任务未执行任何正式审批或发布</div>'


def _page(title: str, body: str) -> HTMLResponse:
    style = """
    body{font-family:system-ui,sans-serif;margin:0;background:#f4f7fb;color:#172033}
    header,main{max-width:1180px;margin:auto;padding:18px}.p1-banner{padding:14px 18px;font-weight:800;color:#fff}
    .demo{background:#b42318}.formal{background:#344054}.card{background:#fff;border:1px solid #d0d5dd;
    border-radius:12px;padding:18px;margin:16px 0}table{border-collapse:collapse;width:100%}th,td{border:1px solid
    #d0d5dd;padding:8px;text-align:left;vertical-align:top}code{word-break:break-all}button,.button{background:#175cd3;
    color:#fff;border:0;border-radius:7px;padding:9px 14px;text-decoration:none;display:inline-block;margin:4px}button[disabled]
    {background:#98a2b3}.warning{background:#fffaeb;border-left:5px solid #f79009;padding:12px}.safe{color:#067647}
    .danger{color:#b42318}.checks label{display:block;margin:7px 0}textarea{width:100%;min-height:85px}
    details{margin-top:12px}.ids{font-family:ui-monospace,monospace;font-size:.86rem}.pill{padding:3px 7px;border-radius:12px;
    background:#eef4ff}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
    """
    return HTMLResponse(
        f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' "
        f"content='width=device-width,initial-scale=1'><title>{_e(title)}</title><style>{style}</style></head>"
        f"<body>{_banner()}<header><h1>{_e(title)}</h1><nav><a class='button' href='/p1-9/reviews'>候选复核</a>"
        "<a class='button' href='/discoveries'>Discovery任务</a></nav></header><main>"
        f"{body}</main></body></html>"
    )


def _card(connection: Any, case_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        connection.execute(
            text(
                """SELECT rc.id review_case_id,rc.state review_state,rc.etag,rc.current_mapping_revision,
            cv.id candidate_version_id,cv.candidate_id,cv.license_status,cv.terms_sha256,
            cv.metadata,sc.discovery_run_id,sc.publisher,sc.dataset_name,sc.status candidate_status,
            sc.risk_flags,sp.schema_sha256,sp.row_count,sp.column_count,mp.mapping_sha256,
            ar.sha256 artifact_sha256,ar.byte_size,ar.mime_type,qa.pii_status,qa.license_status assessed_license_status,
            d.id approval_id,d.decision,d.environment decision_environment,d.test_only,d.production_usable,
            p.id promotion_job_id,p.state promotion_state
            FROM epidemic_discovery.source_review_cases rc
            JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
            JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
            JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
            JOIN epidemic_discovery.source_mapping_proposals mp
              ON mp.candidate_version_id=cv.id AND mp.revision=rc.current_mapping_revision
            JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
            JOIN epidemic_discovery.source_quality_assessments qa ON qa.candidate_version_id=cv.id
            LEFT JOIN LATERAL(SELECT x.* FROM epidemic_discovery.source_review_decisions x
              WHERE x.review_case_id=rc.id ORDER BY x.created_at DESC LIMIT 1)d ON true
            LEFT JOIN epidemic_discovery.source_promotion_runs p ON p.review_decision_id=d.id
            WHERE rc.id=:case"""
            ),
            {"case": case_id},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def _binding(card: dict[str, Any]) -> dict[str, str]:
    return {
        "candidate_id": str(card["candidate_id"]),
        "candidate_version_id": str(card["candidate_version_id"]),
        "discovery_run_id": str(card["discovery_run_id"]),
        "review_case_id": str(card["review_case_id"]),
        "artifact_sha256": str(card["artifact_sha256"]),
        "schema_sha256": str(card["schema_sha256"]),
        "mapping_sha256": str(card["mapping_sha256"]),
        "license_evidence_sha256": str(card["terms_sha256"]),
    }


def _metric() -> dict[str, object]:
    return {
        "metric_code": "notification_record_count",
        "display_name_zh": "登革热通知记录数",
        "unit": "notifications",
        "data_nature": "derived_aggregation",
        "training_eligible": False,
        "national_aggregate": False,
        "source_date_semantics": "notification_date",
        "quality_status": "requires_review",
    }


def _geography() -> dict[str, object]:
    return {
        "country_parent": "Brazil",
        "geography_semantics": "notification_location",
        "state_field": "sg_uf_not",
        "municipality_field": "id_municip",
        "national_aggregate": False,
        "names_verified": False,
    }


def install_routes(app: FastAPI, engine: Engine, sessions: Any) -> None:
    public_origin = os.environ.get("P1_9_PUBLIC_ORIGIN", "http://127.0.0.1:8121").rstrip("/")

    def auth(request: Request) -> tuple[str, str] | None:
        return sessions.get(request.cookies.get("epidemic_source_review"))

    def csrf_ok(request: Request, expected: str, submitted: str) -> bool:
        origin = request.headers.get("origin", "").rstrip("/")
        referer = request.headers.get("referer", "")
        return (
            origin == public_origin
            and referer.startswith(public_origin + "/")
            and hmac.compare_digest(expected, submitted)
        )

    @app.get("/p1-9/reviews")
    def reviews(request: Request) -> Response:
        if auth(request) is None:
            return RedirectResponse("/login", 303)
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """SELECT rc.id,rc.state,sc.publisher,sc.dataset_name,cv.license_status,
                    sp.row_count,sp.column_count,ar.sha256 artifact_sha256,p.state promotion_state
                    FROM epidemic_discovery.source_review_cases rc
                    JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
                    JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
                    JOIN epidemic_discovery.source_schema_profiles sp ON sp.candidate_version_id=cv.id
                    JOIN epidemic_discovery.quarantine_artifacts ar ON ar.candidate_version_id=cv.id
                    LEFT JOIN epidemic_discovery.source_review_decisions d ON d.review_case_id=rc.id
                    LEFT JOIN epidemic_discovery.source_promotion_runs p ON p.review_decision_id=d.id
                    ORDER BY rc.created_at DESC LIMIT 50"""
                    )
                )
                .mappings()
                .all()
            )
        body_rows = "".join(
            "<tr><td><a href='/p1-9/reviews/{id}'>{name}</a></td><td>{publisher}</td><td>{state}</td>"
            "<td>{license}</td><td>{shape}</td><td class='ids'>{artifact}</td><td>{promotion}</td></tr>".format(
                id=_e(row["id"]),
                name=_e(row["dataset_name"]),
                publisher=_e(row["publisher"]),
                state=_e(row["state"]),
                license=_e(row["license_status"]),
                shape=_e(f"{row['row_count']}×{row['column_count']}"),
                artifact=_e(str(row["artifact_sha256"])[:12]),
                promotion=_e(row["promotion_state"]),
            )
            for row in rows
        )
        return _page(
            "P1-9 受控复核与发布",
            "<section class='card'><p>批准和开始 Promotion 是两个独立步骤。正式环境的许可、隐私、范围或映射门禁不会因演示而降低。</p>"
            "<table><thead><tr><th>数据集</th><th>发布者</th><th>复核状态</th><th>许可</th><th>结构</th>"
            f"<th>Artifact</th><th>Promotion</th></tr></thead><tbody>{body_rows}</tbody></table></section>",
        )

    @app.get("/p1-9/reviews/{case_id}")
    def review_detail(case_id: uuid.UUID, request: Request) -> Response:
        session = auth(request)
        if session is None:
            return RedirectResponse("/login", 303)
        _, csrf = session
        with engine.connect() as connection:
            card = _card(connection, case_id)
            preview = (
                connection.execute(
                    text(
                        "SELECT row_ordinal,sanitized_json,row_sha256 FROM epidemic_discovery.review_preview_v "
                        "WHERE review_case_id=:case ORDER BY row_ordinal LIMIT 20"
                    ),
                    {"case": case_id},
                )
                .mappings()
                .all()
            )
        if card is None:
            return _page("候选不存在", "<p>未找到该复核记录。</p>")
        safe_rows: list[dict[str, object]] = []
        for item in preview:
            sanitized = item["sanitized_json"] or {}
            safe = {key: sanitized.get(key) for key in WHITELIST if key in sanitized}
            safe["source_row_hash"] = str(item["row_sha256"])
            safe_rows.append(safe)
        headers = list(WHITELIST) + ["source_row_hash"]
        table_rows = "".join(
            "<tr>" + "".join(f"<td>{_e(row.get(key))}</td>" for key in headers) + "</tr>" for row in safe_rows
        )
        environment = os.environ.get("APP_ENV", "production")
        license_pending = "REVIEW_REQUIRED" in str(card["assessed_license_status"])
        direct_identifier = any(
            str(flag) in {"DIRECT_IDENTIFIER", "SECRET_OR_CREDENTIAL_DETECTED"} for flag in (card["risk_flags"] or [])
        )
        approve_enabled = card["review_state"] == "REVIEW_READY" and not direct_identifier
        if environment != DEMO_ENV and license_pending:
            approve_enabled = False
        warning = (
            "许可仍待人工复核：演示审批仅确认看见此风险，不代表许可通过；该审批不可用于生产。"
            if environment == DEMO_ENV
            else "许可原文尚未确认，正式批准和 Promotion 保持禁用。"
        )
        decision_form = ""
        if card["review_state"] == "REVIEW_READY":
            disabled = "" if approve_enabled else " disabled"
            decision_form = f"""<section class='card'><h2>人工决定</h2><form method='post' action='/p1-9/reviews/{_e(case_id)}/decision'>
            <input type='hidden' name='csrf' value='{_e(csrf)}'><label>必填复核意见</label><textarea name='comment' required minlength='3'></textarea>
            <div class='checks'><label><input type='checkbox' name='license' value='1'> 我已阅读许可状态（不代表许可已通过）</label>
            <label><input type='checkbox' name='geography' value='1'> 我确认仅按通知地州/市代码展示，非全国汇总</label>
            <label><input type='checkbox' name='disease' value='1'> 我确认病种为登革热</label>
            <label><input type='checkbox' name='mapping' value='1'> 我确认仅使用白名单字段并聚合通知记录</label>
            <label><input type='checkbox' name='metric_unit' value='1'> 我确认指标为 notification_record_count / notifications</label>
            <label><input type='checkbox' name='time_semantics' value='1'> 我确认 dt_notific 是通知日期</label>
            <label><input type='checkbox' name='second_confirmation' value='1'> 二次确认：审批与 Promotion 是两个步骤</label></div>
            <button name='action' value='CHANGES_REQUESTED'>要求修改</button><button name='action' value='REJECTED'>拒绝</button>
            <button name='action' value='APPROVED'{disabled}>批准候选</button></form></section>"""
        promotion_form = ""
        if card["decision"] == "APPROVED" and card["promotion_job_id"] is None:
            disabled = "" if environment == DEMO_ENV or not license_pending else " disabled"
            promotion_form = f"""<section class='card'><h2>第二步：开始 Promotion</h2><p>再次校验 Artifact、schema、mapping、许可证据与范围绑定。</p>
            <form method='post' action='/p1-9/reviews/{_e(case_id)}/promotion'><input type='hidden' name='csrf' value='{_e(csrf)}'>
            <label><input type='checkbox' name='second_confirmation' value='1' required> 我确认开始受控发布</label>
            <button{disabled}>开始 Promotion</button></form></section>"""
        if card["promotion_job_id"]:
            promotion_form = (
                "<section class='card'><h2>Promotion</h2><p>状态：<strong>"
                f"{_e(card['promotion_state'])}</strong></p><a class='button' href='/p1-9/promotions/"
                f"{_e(card['promotion_job_id'])}'>查看状态与血缘</a></section>"
            )
        evidence = _binding(card)
        overview = f"""<section class='card'><h2>{_e(card["dataset_name"])}</h2><div class='grid'>
        <p><b>发布者</b><br>{_e(card["publisher"])}</p><p><b>状态</b><br>{_e(card["review_state"])}</p>
        <p><b>结构</b><br>{_e(card["row_count"])} 行 × {_e(card["column_count"])} 列</p>
        <p><b>Artifact</b><br><span class='ids'>{_e(str(card["artifact_sha256"])[:12])}</span></p></div>
        <p class='warning'>{_e(warning)}</p><p class='danger'>原始数据包含人口统计、医疗和准标识字段；页面、CSV、日志与报告只允许显示白名单字段。</p>
        <h3>固定映射</h3><pre>{_e(canonical_json(_metric()))}</pre><h3>地理范围</h3><pre>{_e(canonical_json(_geography()))}</pre></section>"""
        preview_html = (
            "<section class='card'><h2>白名单隔离预览</h2><table><thead><tr>"
            + "".join(f"<th>{_e(key)}</th>" for key in headers)
            + f"</tr></thead><tbody>{table_rows}</tbody></table></section>"
        )
        technical = f"<details class='card'><summary>版本绑定（完整ID折叠）</summary><pre>{_e(canonical_json(evidence))}</pre></details>"
        return _page("P1-9 候选复核", overview + preview_html + decision_form + promotion_form + technical)

    @app.post("/p1-9/reviews/{case_id}/decision")
    async def decide(case_id: uuid.UUID, request: Request) -> Response:
        session = auth(request)
        if session is None:
            return RedirectResponse("/login", 303)
        username, csrf = session
        values = urllib.parse.parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        if not csrf_ok(request, csrf, str(values.get("csrf", [""])[0])):
            return HTMLResponse("CSRF_REJECTED", 403)
        action = str(values.get("action", [""])[0])
        comment = str(values.get("comment", [""])[0]).strip()
        environment = os.environ.get("APP_ENV", "production")
        confirmations = {
            key: str(values.get(key, [""])[0]) == "1"
            for key in (
                "license",
                "geography",
                "disease",
                "mapping",
                "metric_unit",
                "time_semantics",
                "second_confirmation",
            )
        }
        with engine.begin() as connection:
            card = _card(connection, case_id)
            if card is None:
                return HTMLResponse("NOT_FOUND", 404)
            binding = _binding(card)
            decision_id = connection.execute(
                text(
                    "SELECT epidemic_discovery.p1_9_submit_review_decision("
                    ":case,:action,:reviewer,:comment,:key,:etag,:environment,:kind,:test_only,"
                    "CAST(:confirmations AS jsonb),CAST(:geography AS jsonb),CAST(:metric AS jsonb),"
                    "CAST(:binding AS jsonb),:binding_sha)"
                ),
                {
                    "case": case_id,
                    "action": action,
                    "reviewer": username,
                    "comment": comment,
                    "key": uuid.uuid4(),
                    "etag": card["etag"],
                    "environment": environment,
                    "kind": "demo_reviewer" if environment == DEMO_ENV else "human_reviewer",
                    "test_only": environment == DEMO_ENV,
                    "confirmations": canonical_json(confirmations),
                    "geography": canonical_json(_geography()),
                    "metric": canonical_json(_metric()),
                    "binding": canonical_json(binding),
                    "binding_sha": sha(binding),
                },
            ).scalar_one()
        return RedirectResponse(f"/p1-9/reviews/{case_id}?decision={decision_id}", 303)

    @app.post("/p1-9/reviews/{case_id}/promotion")
    async def queue_promotion(case_id: uuid.UUID, request: Request) -> Response:
        session = auth(request)
        if session is None:
            return RedirectResponse("/login", 303)
        _, csrf = session
        values = urllib.parse.parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        if not csrf_ok(request, csrf, str(values.get("csrf", [""])[0])):
            return HTMLResponse("CSRF_REJECTED", 403)
        if str(values.get("second_confirmation", [""])[0]) != "1":
            return HTMLResponse("SECOND_CONFIRMATION_REQUIRED", 422)
        environment = os.environ.get("APP_ENV", "production")
        with engine.begin() as connection:
            card = _card(connection, case_id)
            if card is None or card["approval_id"] is None:
                return HTMLResponse("VALID_APPROVAL_REQUIRED", 422)
            key = sha(
                {"decision": str(card["approval_id"]), "binding": sha(_binding(card)), "environment": environment}
            )
            promotion_id = connection.execute(
                text("SELECT epidemic_discovery.p1_9_queue_promotion(:decision,:environment,:key)"),
                {"decision": card["approval_id"], "environment": environment, "key": key},
            ).scalar_one()
        return RedirectResponse(f"/p1-9/promotions/{promotion_id}", 303)

    @app.get("/p1-9/promotions/{promotion_id}")
    def promotion_status(promotion_id: uuid.UUID, request: Request) -> Response:
        if auth(request) is None:
            return RedirectResponse("/login", 303)
        with engine.connect() as connection:
            run = (
                connection.execute(
                    text(
                        "SELECT p.*,pub.dashboard_path,pub.observation_count,pub.snapshot_id,pub.publication_sha256 "
                        "FROM epidemic_discovery.source_promotion_runs p LEFT JOIN epidemic_discovery.p1_9_publications pub "
                        "ON pub.promotion_run_id=p.id WHERE p.id=:id"
                    ),
                    {"id": promotion_id},
                )
                .mappings()
                .one_or_none()
            )
            events = (
                connection.execute(
                    text(
                        "SELECT from_state,to_state,event_code,created_at FROM epidemic_discovery.source_promotion_events "
                        "WHERE promotion_run_id=:id ORDER BY created_at,id"
                    ),
                    {"id": promotion_id},
                )
                .mappings()
                .all()
            )
        if run is None:
            return _page("Promotion 不存在", "<p>未找到记录。</p>")
        event_rows = "".join(
            f"<tr><td>{_e(item['created_at'])}</td><td>{_e(item['from_state'])}</td><td>{_e(item['to_state'])}</td><td>{_e(item['event_code'])}</td></tr>"
            for item in events
        )
        links = ""
        if run["state"] == "PUBLISHED":
            links = (
                f"<p><a class='button' href='/p1-9/lineage/{_e(run['publication_id'])}'>查看全链路追踪</a>"
                "<a class='button' href='http://127.0.0.1:8122/'>打开临时 Dashboard</a></p>"
            )
        body = f"""<section class='card'><h2>状态：{_e(run["state"])}</h2><div class='grid'>
        <p><b>Promotion</b><br><span class='ids'>{_e(str(run["id"])[:12])}</span></p>
        <p><b>尝试次数</b><br>{_e(run["attempt_count"])}/{_e(run["max_attempts"])}</p>
        <p><b>Observation</b><br>{_e(run["observation_count"])}</p><p><b>Snapshot</b><br>{_e(str(run["snapshot_id"])[:12])}</p>
        </div>{links}<table><thead><tr><th>时间</th><th>原状态</th><th>新状态</th><th>事件</th></tr></thead><tbody>{event_rows}</tbody></table></section>"""
        return _page("P1-9 Promotion 状态", body)

    @app.get("/p1-9/lineage/{publication_id}")
    def lineage(publication_id: uuid.UUID, request: Request) -> Response:
        if auth(request) is None:
            return RedirectResponse("/login", 303)
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        """SELECT pub.id publication_id,p.id promotion_job_id,d.id approval_id,rc.id review_case_id,
                    cv.candidate_id,cv.id candidate_version_id,sc.discovery_run_id,
                    l.observation_id,l.artifact_sha256,l.source_row_set_sha256,l.source_row_count,
                    mc.id mapping_contract_id,pub.normalization_run_id,pub.snapshot_id
                    FROM epidemic_discovery.p1_9_publications pub
                    JOIN epidemic_discovery.source_promotion_runs p ON p.id=pub.promotion_run_id
                    JOIN epidemic_discovery.source_review_decisions d ON d.id=p.review_decision_id
                    JOIN epidemic_discovery.source_review_cases rc ON rc.id=d.review_case_id
                    JOIN epidemic_discovery.source_candidate_versions cv ON cv.id=rc.candidate_version_id
                    JOIN epidemic_discovery.source_candidates sc ON sc.id=cv.candidate_id
                    JOIN epidemic_discovery.p1_9_observation_lineage l ON l.promotion_run_id=p.id
                    JOIN epidemic_discovery.p1_9_mapping_contracts mc ON mc.id=l.mapping_contract_id
                    WHERE pub.id=:id ORDER BY l.observation_id LIMIT 100"""
                    ),
                    {"id": publication_id},
                )
                .mappings()
                .all()
            )
        if not rows:
            return _page("血缘不存在", "<p>未找到发布记录。</p>")
        chain = rows[0]
        labels = (
            ("Discovery检索", "discovery_run_id"),
            ("候选", "candidate_id"),
            ("候选版本", "candidate_version_id"),
            ("复核案例", "review_case_id"),
            ("测试审批", "approval_id"),
            ("Promotion", "promotion_job_id"),
            ("映射契约", "mapping_contract_id"),
            ("标准化运行", "normalization_run_id"),
            ("不可变Snapshot", "snapshot_id"),
            ("Dashboard发布", "publication_id"),
        )
        chain_html = " → ".join(
            f"<span class='pill'>{_e(label)} {_e(str(chain[key])[:8])}</span>" for label, key in labels
        )
        rows_html = "".join(
            f"<tr><td class='ids'>{_e(str(item['observation_id'])[:12])}</td>"
            f"<td class='ids'>{_e(str(item['artifact_sha256'])[:12])}</td>"
            f"<td class='ids'>{_e(str(item['source_row_set_sha256'])[:12])}</td>"
            f"<td>{_e(item['source_row_count'])}</td></tr>"
            for item in rows
        )
        return _page(
            "P1-9 全链路追踪",
            f"<section class='card'><h2>发布链路</h2><p>{chain_html}</p><table><thead><tr><th>Observation</th>"
            f"<th>Artifact</th><th>源行集合哈希</th><th>源行数</th></tr></thead><tbody>{rows_html}</tbody></table>"
            f"<details><summary>完整技术ID</summary><pre>{_e(canonical_json(dict(chain)))}</pre></details></section>",
        )
