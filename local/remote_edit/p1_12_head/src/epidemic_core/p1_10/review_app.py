from __future__ import annotations

# ruff: noqa: E501
# mypy: ignore-errors
import hmac
import html
import os
import re
import urllib.parse
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from epidemic_core.p1_10.diagnostics import safe_business_error
from epidemic_core.p1_10.preview import REQUIRED_MAPPING, canonical_json, sha

WHITELIST = ("dt_notific", "sg_uf_not", "id_municip", "nu_ano", "case_notification_count")
CONFIRMATIONS = (
    ("not_trusted", "我理解这不是正式数据批准"),
    ("license_pending", "我理解许可仍待复核"),
    ("sample_partial", "我理解数据可能只是 API sample"),
    ("not_confirmed_cases", "我理解通知记录数不等于确诊病例数"),
    ("not_national", "我确认数据不是巴西全国汇总"),
    ("excluded_from_automation", "我确认 Preview 不进入训练、预测或每日更新"),
    ("whitelist_only", "我确认只发布白名单脱敏聚合字段"),
    ("no_cross_source_sum", "我确认不与 WHO 或其他来源相加"),
)

BUSINESS_ERRORS = {
    "PREVIEW_DECISION_STALE": 409,
    "PREVIEW_ENVIRONMENT_MARKER_MISSING": 409,
    "PREVIEW_ENVIRONMENT_BOUNDARY_INVALID": 409,
    "PREVIEW_MAPPING_REQUIRED": 409,
    "PREVIEW_ARTIFACT_MISSING": 409,
    "PREVIEW_SCHEMA_UNSUPPORTED": 422,
    "PREVIEW_SANITIZATION_REQUIRED": 409,
    "PREVIEW_SCOPE_REVIEW_REQUIRED": 409,
    "PREVIEW_CONFIRMATION_TEXT_INVALID": 422,
    "PREVIEW_REASON_REQUIRED": 422,
    "PREVIEW_CONFIRMATIONS_INCOMPLETE": 422,
}


def _business_error(exc: DBAPIError) -> tuple[str, int] | None:
    message = str(getattr(exc, "orig", exc))
    for code, status in BUSINESS_ERRORS.items():
        if code in message:
            return code, status
    return None


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{_e(title)}</title>"
        "<link rel='stylesheet' href='/static/review.p1-7ar3-v1.css'><link rel='stylesheet' href='/static/review.p1-10-preview.css'></head>"
        "<body><div class='banner'>候选演示 Preview：未经正式批准，不用于训练、预测、每日更新或公共结论</div>"
        f"<header><h1>{_e(title)}</h1><nav><a class='button' href='/discoveries'>发现任务</a><a class='button' href='/reviews'>原复核队列</a><a class='button' href='/p1-10/reviews'>候选 Preview</a></nav></header><main>{body}</main></body></html>"
    )


def _card(connection: Any, case_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        connection.execute(
            text("SELECT * FROM epidemic_preview.preview_review_cards_v1 WHERE review_case_id=:case"),
            {"case": case_id},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def _is_p1_12_case(connection: Any, case_id: uuid.UUID) -> bool:
    return bool(
        connection.execute(
            text("SELECT 1 FROM epidemic_p1_12.review_application_bindings WHERE review_case_id=:case LIMIT 1"),
            {"case": case_id},
        ).scalar_one_or_none()
    )


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


def _category(name: str) -> str:
    value = name.lower()
    if value in {"dt_notific", "sg_uf_not", "id_municip", "nu_ano"}:
        return "白名单业务字段"
    if re.search(r"cpf|nome|nm_|paciente|mae|telefone|email|endereco|logradouro|cartao|cns", value):
        return "直接或准标识字段"
    if re.search(r"sint|hospital|evol|obito|grav|gest|comorb|exame|resultado|vacina|trat|clin", value):
        return "医疗敏感字段"
    if re.search(r"idade|sexo|raca|escolar|ocup|genero", value):
        return "人口学字段"
    if re.search(r"classi|criterio|caso|doenca|agravo", value):
        return "病例分类字段"
    if re.search(r"^dt_|data|date|ano|semana|mes", value):
        return "日期时间字段"
    if re.search(r"munic|bairro|cep|resid|\buf\b|_uf|geo", value):
        return "地理字段"
    return "尚未充分分类"


STATE_ZH = {
    "REVIEW_READY": "待人工复核",
    "PREVIEW_QUEUED": "候选预览排队中",
    "PREVIEW_CLAIMED": "候选预览处理中",
    "PREVIEW_PUBLISHED": "候选预览已发布",
    "PREVIEW_REVOKED": "候选预览已撤回",
    "FAILED_RETRYABLE": "可重试失败",
    "FAILED_FINAL": "终止失败",
}


def _state_zh(value: object, empty: str = "尚未生成") -> str:
    if value is None or str(value).strip() == "":
        return empty
    return STATE_ZH.get(str(value), str(value))


def _short_hash(value: object) -> str:
    return str(value or "")[:12]


def _version_card(connection: Any, case_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        connection.execute(
            text("SELECT * FROM epidemic_preview.preview_candidate_version_history_v1 WHERE review_case_id=:case"),
            {"case": case_id},
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


def install_routes(app: FastAPI, engine: Engine, sessions: Any) -> None:
    public_origin = os.environ.get("REVIEW_PUBLIC_ORIGIN", "http://127.0.0.1:8121").rstrip("/")

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

    @app.get("/p1-10/reviews")
    def reviews(request: Request) -> Response:
        if auth(request) is None:
            return RedirectResponse("/login", 303)
        with engine.connect() as connection:
            datasets = (
                connection.execute(
                    text(
                        "SELECT * FROM epidemic_preview.preview_candidate_dataset_summary_v1 "
                        "WHERE review_state='REVIEW_READY' AND NOT EXISTS ("
                        "SELECT 1 FROM epidemic_p1_12.review_application_bindings rab "
                        "WHERE rab.review_case_id=current_review_case_id) "
                        "ORDER BY current_retrieved_at DESC,logical_dataset_key"
                    )
                )
                .mappings()
                .all()
            )
            versions = (
                connection.execute(
                    text(
                        "SELECT * FROM epidemic_preview.preview_candidate_version_history_v1 "
                        "WHERE review_state='REVIEW_READY' AND NOT EXISTS ("
                        "SELECT 1 FROM epidemic_p1_12.review_application_bindings rab "
                        "WHERE rab.review_case_id=review_case_id) "
                        "ORDER BY logical_dataset_key,retrieved_at DESC,logical_version_no DESC"
                    )
                )
                .mappings()
                .all()
            )
        history_by_key: dict[str, list[Any]] = {}
        for version in versions:
            history_by_key.setdefault(str(version["logical_dataset_key"]), []).append(version)
        body_rows = ""
        for row in datasets:
            history_rows = "".join(
                "<tr>"
                f"<td>Version {_e(version['logical_version_no'])}</td>"
                f"<td class='ids compact-id'>{_e(_short_hash(version['discovery_run_id']))}</td>"
                f"<td class='nowrap'>{_e(version['retrieved_at'])}</td>"
                f"<td class='ids compact-id'>{_e(_short_hash(version['artifact_sha256']))}</td>"
                f"<td class='ids compact-id'>{_e(_short_hash(version['schema_sha256']))}</td>"
                f"<td class='nowrap'>{_e(version['row_count'])}×{_e(version['column_count'])}</td>"
                f"<td class='nowrap'>{'当前版本' if version['is_current'] else '历史版本（superseded）'}</td>"
                f"<td>{_e(_state_zh(version['preview_state']))}</td>"
                f"<td>{_e(version['difference_summary'])}</td>"
                f"<td><a class='button small' href='/p1-10/reviews/{_e(version['review_case_id'])}'>查看此版本</a></td>"
                "</tr>"
                for version in history_by_key.get(str(row["logical_dataset_key"]), [])
            )
            eligibility = "可生成候选预览" if row["preview_eligible"] else "暂不可生成"
            body_rows += f"""<article class='card dataset-card'>
            <div class='dataset-title'><div><h2>{_e(row["dataset_name"])}</h2><p>{_e(row["publisher"])}</p></div>
            <a class='button' href='/p1-10/reviews/{_e(row["current_review_case_id"])}'>查看当前版本</a></div>
            <div class='version-grid'>
              <p><b>国家 / 病种</b><br>{_e(row["country_iso3"])} / {_e(row["disease_code"])}</p>
              <p><b>Candidate 状态</b><br><span class='status'>{_e(_state_zh(row["candidate_status"]))}</span></p>
              <p><b>Preview 状态</b><br><span class='status'>{_e(_state_zh(row["preview_state"]))}</span></p>
              <p><b>当前版本</b><br>Version {_e(row["current_version_no"])} / 共 {_e(row["version_count"])} 版</p>
              <p><b>当前 Artifact</b><br><span class='ids compact-id'>{_e(_short_hash(row["current_artifact_sha256"]))}</span></p>
              <p><b>抓取时间</b><br>{_e(row["current_retrieved_at"])}</p>
              <p><b>结构</b><br>{_e(row["row_count"])}×{_e(row["column_count"])}</p>
              <p><b>许可</b><br>{_e(row["license_status"])}</p>
              <p><b>Preview eligibility</b><br>{_e(eligibility)}</p>
            </div>
            <details class='version-history'><summary>展开版本历史（{_e(row["version_count"])}）</summary>
            <div class='scroll'><table><thead><tr><th>Version</th><th>Discovery Run</th><th>Fetch 时间</th><th>Artifact SHA</th><th>Schema SHA</th><th>结构</th><th>版本状态</th><th>Preview 状态</th><th>差异摘要</th><th>操作</th></tr></thead><tbody>{history_rows}</tbody></table></div>
            </details></article>"""
        return _page(
            "正式候选 Preview 复核",
            "<section class='card'><p>每个逻辑数据集只显示一张卡片；当前版本按真实 Artifact 抓取时间确定。"
            "“生成候选演示预览”只创建内部脱敏聚合，不等于正式批准，不会触发 trusted Promotion。</p></section>"
            f"{body_rows}",
        )

    @app.get("/p1-10/reviews/{case_id}")
    def detail(case_id: uuid.UUID, request: Request) -> Response:
        session = auth(request)
        if session is None:
            return RedirectResponse("/login", 303)
        _, csrf = session
        with engine.connect() as connection:
            if _is_p1_12_case(connection, case_id):
                return safe_business_error(
                    "P1_12_CANDIDATE_NOT_ALLOWED_IN_P1_10_PREVIEW", request, 409, review_console=True
                )
            card = _card(connection, case_id)
            version = _version_card(connection, case_id)
            if card is None or version is None:
                return safe_business_error("PREVIEW_CANDIDATE_NOT_FOUND", request, 404, review_console=True)
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
            columns = (
                connection.execute(
                    text(
                        "SELECT source_name FROM epidemic_preview.preview_safe_source_columns_v1 "
                        "WHERE review_case_id=:case ORDER BY ordinal"
                    ),
                    {"case": case_id},
                )
                .scalars()
                .all()
            )
            fetch = (
                connection.execute(
                    text(
                        "SELECT status,http_status,content_type,byte_size,domain,elapsed_ms,artifact_sha256,attempted_at,completed_at "
                        "FROM epidemic_preview.preview_candidate_fetches_safe_v1 WHERE discovery_run_id=:run "
                        "AND artifact_sha256=:artifact "
                        "ORDER BY attempted_at DESC LIMIT 1"
                    ),
                    {"run": card["discovery_run_id"], "artifact": card["artifact_sha256"]},
                )
                .mappings()
                .one_or_none()
            )
            events = (
                connection.execute(
                    text(
                        "SELECT from_state,to_state,event_code,attempt,requires_user_action,error_code,next_step_zh,created_at "
                        "FROM epidemic_preview.preview_publication_events WHERE publication_job_id=:job ORDER BY id"
                    ),
                    {"job": card["preview_job_id"]},
                )
                .mappings()
                .all()
                if card["preview_job_id"] is not None
                else []
            )
        safe_rows: list[dict[str, object]] = []
        for row in preview:
            sanitized = row["sanitized_json"] or {}
            safe = {key: sanitized.get(key) for key in WHITELIST if key in sanitized}
            safe["source_row_hash"] = str(row["row_sha256"])
            safe_rows.append(safe)
        headers = list(WHITELIST) + ["source_row_hash"]
        table_rows = "".join(
            "<tr>" + "".join(f"<td>{_e(row.get(key))}</td>" for key in headers) + "</tr>" for row in safe_rows
        )
        categories: dict[str, int] = {}
        for name in columns:
            category = _category(str(name))
            categories[category] = categories.get(category, 0) + 1
        category_html = "".join(
            f"<span class='pill'>{_e(name)}：{count}</span>" for name, count in sorted(categories.items())
        )
        blockers: list[str] = []
        if not card.get("mapping_sha256"):
            blockers.append("PREVIEW_MAPPING_REQUIRED")
        if not card.get("artifact_sha256"):
            blockers.append("PREVIEW_ARTIFACT_MISSING")
        if not safe_rows:
            blockers.append("PREVIEW_SANITIZATION_REQUIRED")
        if "PROHIBIT" in str(card.get("license_status") or "").upper():
            blockers.append("PREVIEW_LICENSE_PROHIBITION")
        form = ""
        if card["preview_decision_id"] is None and not blockers:
            checks = "".join(
                f"<label><input type='checkbox' name='{_e(key)}' value='1' required> {_e(label)}</label>"
                for key, label in CONFIRMATIONS
            )
            form = f"""<section class='card'><h2>11. 候选 Preview 状态：生成候选演示预览</h2><p class='warning'>该操作不会批准许可，也不会写入正式 Observation、Snapshot、训练或 Daily Update。</p>
            <form method='post' action='/p1-10/reviews/{_e(case_id)}/publish-preview'>
            <input type='hidden' name='csrf' value='{_e(csrf)}'><input type='hidden' name='state_version' value='{_e(card["state_version"])}'>
            <input type='hidden' name='idempotency_key' value='{uuid.uuid4()}'><label>复核理由（必填）</label><textarea name='reason' required minlength='10'></textarea>
            <div class='checks'>{checks}</div><label>二次确认文本</label><input type='text' name='confirmation_text' required autocomplete='off' placeholder='发布候选预览'>
            <button type='submit'>生成候选演示预览</button></form></section>"""
        elif blockers:
            form = (
                "<section class='card'><h2>11. 候选 Preview 状态</h2>"
                "<p class='warning'>当前版本暂不可生成候选演示预览。</p><ul>"
                + "".join(f"<li><code>{_e(code)}</code></li>" for code in blockers)
                + "</ul></section>"
            )
        else:
            form = f"<section class='card'><h2>11. 候选 Preview 状态</h2><p>状态：<strong>{_e(_state_zh(card['preview_state']))}</strong></p><a class='button' href='/p1-10/jobs/{_e(card['preview_job_id'])}'>查看状态时间线</a></section>"
        fetch_html = (
            "无本轮 Fetch 证据"
            if fetch is None
            else (
                f"HTTP {_e(fetch['http_status'])}；{_e(fetch['content_type'])}；{_e(fetch['byte_size'])} bytes；"
                f"{_e(fetch['elapsed_ms'])} ms；域名 {_e(fetch['domain'])}；Artifact {_e(str(fetch['artifact_sha256'])[:12])}"
            )
        )
        evidence = {
            "binding": _binding(card),
            "mapping": REQUIRED_MAPPING,
            "fetch": dict(fetch) if fetch else None,
            "logical_version_no": version["logical_version_no"],
            "is_current": version["is_current"],
        }
        event_rows = (
            "".join(
                f"<tr><td>{_e(event['created_at'])}</td><td>{_e(_state_zh(event['to_state']))}</td>"
                f"<td>{_e(event['event_code'])}</td><td>{_e(event['error_code'])}</td><td>{_e(event['next_step_zh'])}</td></tr>"
                for event in events
            )
            or "<tr><td colspan='5'>尚无 Preview 事件；失败的只读详情请求未产生写入。</td></tr>"
        )
        current_label = "当前最新版本" if version["is_current"] else "历史版本（superseded）"
        mapping_valid = "仍有效" if version["mapping_still_valid"] else "需重新复核"
        body = f"""
        <section class='card'><h2>1. 数据集与发布机构</h2><div class='grid'>
        <p><b>数据集</b><br>{_e(card["dataset_name"])}</p><p><b>发布机构</b><br>{_e(card["publisher"])}</p>
        <p><b>国家 / 病种</b><br>{_e(version["country_iso3"])} / {_e(version["disease_code"])}</p>
        <p><b>官方地址</b><br>{_e(card["canonical_url_redacted"])}</p></div></section>
        <section class='card'><h2>2. 当前版本</h2><div class='grid'>
        <p><b>选择版本</b><br>Version {_e(version["logical_version_no"])} / {_e(version["version_count"])}</p>
        <p><b>版本状态</b><br>{_e(current_label)}</p><p><b>抓取时间</b><br>{_e(version["retrieved_at"])}</p>
        <p><b>差异摘要</b><br>{_e(version["difference_summary"])}</p></div></section>
        <section class='card'><h2>3. Artifact 与 Fetch 证据</h2><div class='grid'>
        <p><b>Artifact SHA-256</b><br><span class='ids'>{_e(card["artifact_sha256"])}</span></p>
        <p><b>Schema SHA-256</b><br><span class='ids'>{_e(card["schema_sha256"])}</span></p></div><p>{fetch_html}</p></section>
        <section class='card'><h2>4. 许可状态</h2><p>{_e(card["assessed_license_status"])}</p>
        <p class='warning'>许可待复核；数据可能只是 API sample，未证明完整分页或全国覆盖。</p></section>
        <section class='card'><h2>5. 数据范围</h2><div class='grid'><p><b>结构</b><br>{_e(card["row_count"])} 行 × {_e(card["column_count"])} 列</p>
        <p><b>日期范围</b><br>{_e(version["date_min"])} 至 {_e(version["date_max"])}</p></div></section>
        <section class='card'><h2>6. 字段安全分类</h2><p>{category_html}</p>
        <p class='danger'>198 列原始数据不会在普通页面、日志、截图、CSV、Qwen、Dashboard 或 Preview Observation 中出现。</p></section>
        <section class='card'><h2>7. 安全预览</h2><div class='scroll'><table><thead><tr>{"".join(f"<th>{_e(h)}</th>" for h in headers)}</tr></thead><tbody>{table_rows}</tbody></table></div></section>
        <section class='card'><h2>8. Mapping</h2><p>登革热通知记录数（notification_record_count / notifications），derived_aggregation，training_eligible=false，national_aggregate=false。</p>
        <p>与上一版本比较：{_e(mapping_valid)}。</p></section>
        <section class='card'><h2>9. 数据质量</h2><div class='grid'><p><b>PII 状态</b><br>{_e(card["pii_status"])}</p>
        <p><b>质量状态</b><br>requires_review</p><p><b>范围限定</b><br>sample / 非全国 / 不可训练</p></div></section>
        <section class='card disabled-action'><h2>10. 正式可信审批状态</h2><p><b>未批准；正式可信审批保持禁用。</b></p>
        <ul><li>许可仍待人工复核</li><li>当前证据是受控 API sample</li><li>通知记录数不等于确诊病例数</li><li>未证明全国完整覆盖</li><li>禁止 trusted Promotion、训练和每日更新</li></ul>
        <button type='button' disabled>正式批准（不可用）</button></section>
        {form}
        <section class='card'><h2>12. 事件时间线</h2><div class='scroll'><table><thead><tr><th>时间</th><th>状态</th><th>事件</th><th>错误</th><th>下一步</th></tr></thead><tbody>{event_rows}</tbody></table></div></section>
        <details class='card'><summary>13. 技术证据（默认折叠）</summary><pre>{_e(canonical_json(evidence))}</pre></details>"""
        return _page("候选 Preview 详情", body)

    @app.post("/p1-10/reviews/{case_id}/publish-preview")
    async def publish_preview(case_id: uuid.UUID, request: Request) -> Response:
        session = auth(request)
        if session is None:
            return RedirectResponse("/login", 303)
        username, csrf = session
        values = urllib.parse.parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        if not csrf_ok(request, csrf, str(values.get("csrf", [""])[0])):
            return safe_business_error("CSRF_REJECTED", request, 403, review_console=True)
        try:
            state_version = int(str(values.get("state_version", ["0"])[0]))
            idempotency_key = uuid.UUID(str(values.get("idempotency_key", [""])[0]))
        except (ValueError, TypeError):
            return safe_business_error("INVALID_CONCURRENCY_OR_IDEMPOTENCY_KEY", request, 422, review_console=True)
        reason = str(values.get("reason", [""])[0]).strip()
        confirmation_text = str(values.get("confirmation_text", [""])[0])
        confirmations = {key: str(values.get(key, [""])[0]) == "1" for key, _ in CONFIRMATIONS}
        environment = os.environ.get("APP_ENV", "production")
        try:
            with engine.begin() as connection:
                if _is_p1_12_case(connection, case_id):
                    return safe_business_error(
                        "P1_12_CANDIDATE_NOT_ALLOWED_IN_P1_10_PREVIEW", request, 409, review_console=True
                    )
                card = _card(connection, case_id)
                if card is None:
                    return safe_business_error("PREVIEW_CANDIDATE_NOT_FOUND", request, 404, review_console=True)
                binding = _binding(card)
                decision_id = connection.execute(
                    text(
                        "SELECT epidemic_preview.submit_preview_decision(:case,:reviewer,:reason,:key,:version,:confirmation,"
                        "CAST(:confirmations AS jsonb),CAST(:binding AS jsonb),:binding_sha,CAST(:mapping AS jsonb),"
                        ":mapping_sha,:environment,:test_only)"
                    ),
                    {
                        "case": case_id,
                        "reviewer": username,
                        "reason": reason,
                        "key": idempotency_key,
                        "version": state_version,
                        "confirmation": confirmation_text,
                        "confirmations": canonical_json(confirmations),
                        "binding": canonical_json(binding),
                        "binding_sha": sha(binding),
                        "mapping": canonical_json(REQUIRED_MAPPING),
                        "mapping_sha": card["mapping_sha256"],
                        "environment": environment,
                        "test_only": environment == "p1_10_test",
                    },
                ).scalar_one()
                job_key = uuid.uuid5(uuid.NAMESPACE_URL, f"p1-10-preview:{decision_id}")
                job_id = connection.execute(
                    text("SELECT epidemic_preview.queue_preview_publication(:decision,:key)"),
                    {"decision": decision_id, "key": job_key},
                ).scalar_one()
        except DBAPIError as exc:
            expected = _business_error(exc)
            if expected is None:
                raise
            code, status_code = expected
            return safe_business_error(code, request, status_code, review_console=True)
        return RedirectResponse(f"/p1-10/jobs/{job_id}", 303)

    @app.get("/p1-10/jobs/{job_id}")
    def job(job_id: uuid.UUID, request: Request) -> Response:
        if auth(request) is None:
            return RedirectResponse("/login", 303)
        with engine.connect() as connection:
            row = (
                connection.execute(
                    text("SELECT * FROM epidemic_preview.preview_publication_jobs WHERE id=:id"), {"id": job_id}
                )
                .mappings()
                .one_or_none()
            )
            events = (
                connection.execute(
                    text(
                        "SELECT from_state,to_state,event_code,attempt,requires_user_action,error_code,next_step_zh,created_at "
                        "FROM epidemic_preview.preview_publication_events WHERE publication_job_id=:id ORDER BY id"
                    ),
                    {"id": job_id},
                )
                .mappings()
                .all()
            )
        if row is None:
            return _page("任务不存在", "<section class='card'>未找到任务。</section>")
        rows = "".join(
            f"<tr><td>{_e(event['created_at'])}</td><td>{_e(event['to_state'])}</td><td>{_e(event['event_code'])}</td>"
            f"<td>{_e(event['attempt'])}</td><td>{_e(event['error_code'])}</td><td>{_e(event['next_step_zh'])}</td></tr>"
            for event in events
        )
        link = ""
        if row["state"] == "PREVIEW_PUBLISHED":
            link = "<p><a class='button' href='http://127.0.0.1:8120/?data_tier=REVIEW_PREVIEW'>在原 Dashboard 查看</a></p>"
        return _page(
            "Preview Publication 状态",
            f"<section class='card'><h2>{_e(row['state'])}</h2><div class='grid'><p>Attempt：{_e(row['attempt_count'])}</p><p>Observation：{_e(row['observation_count'])}</p><p>Snapshot：<span class='ids'>{_e(row['snapshot_id'])}</span></p></div>{link}"
            f"<div class='scroll'><table><thead><tr><th>时间</th><th>状态</th><th>事件</th><th>尝试</th><th>错误</th><th>下一步</th></tr></thead><tbody>{rows}</tbody></table></div></section>",
        )
