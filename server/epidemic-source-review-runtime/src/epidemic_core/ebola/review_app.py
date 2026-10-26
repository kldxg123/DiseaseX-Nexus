from __future__ import annotations

# ruff: noqa: E501
import hmac
import html
import json
import os
import urllib.parse
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import Engine, text


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_e(title)}</title><link rel='stylesheet' href='/static/review.p1-7ar3-v1.css'>"
        "<style>.ebola-warning{background:#fff4e5;border-left:5px solid #d97706;padding:14px}.ebola-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px}.tag{display:inline-block;padding:4px 8px;border-radius:99px;background:#eef2ff;margin:2px}.danger{color:#9f1239}</style></head><body>"
        "<div class='banner'>埃博拉候选 Preview：仅供人工复核；未进入可信数据、训练、预测或跨源相加</div>"
        f"<header><h1>{_e(title)}</h1><nav><a class='button' href='/discoveries'>发现任务</a><a class='button' href='/p1-10/reviews'>既有候选</a><a class='button' href='/p1-11/reviews'>埃博拉候选</a></nav></header><main>{body}</main></body></html>"
    )


def install_routes(app: FastAPI, engine: Engine, sessions: Any) -> None:
    public_origin = os.environ.get("REVIEW_PUBLIC_ORIGIN", "http://127.0.0.1:8121").rstrip("/")

    def auth(request: Request) -> tuple[str, str] | None:
        value = sessions.get(request.cookies.get("epidemic_source_review"))
        return None if value is None else (str(value[0]), str(value[1]))

    def csrf_ok(request: Request, expected: str, submitted: str) -> bool:
        origin = request.headers.get("origin", "").rstrip("/")
        referer = request.headers.get("referer", "")
        return (
            origin == public_origin
            and referer.startswith(public_origin + "/")
            and hmac.compare_digest(expected, submitted)
        )

    @app.get("/p1-11/reviews")
    def reviews(request: Request) -> Response:
        if auth(request) is None:
            return RedirectResponse("/login", 303)
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text("SELECT * FROM epidemic_ebola.review_candidates_v1 ORDER BY source_key,candidate_version_id")
                )
                .mappings()
                .all()
            )
            monitor = (
                connection.execute(text("SELECT * FROM epidemic_ebola.monitor_status_v1 ORDER BY source_key"))
                .mappings()
                .all()
            )
        cards = "".join(
            f"<article class='card'><h2>{_e(row['source_name'])}</h2><p>{_e(row['publisher'])}</p><div class='ebola-grid'>"
            f"<p><b>来源</b><br>{_e(row['source_key'])}</p><p><b>人工复核</b><br>{_e(row['review_state'])}</p>"
            f"<p><b>数据范围</b><br>{_e(row['date_min'])} 至 {_e(row['date_max'])}</p><p><b>聚合行</b><br>{_e(row['row_count'])}</p>"
            f"<p><b>许可</b><br>{_e(row['license_status'])}</p><p><b>Preview</b><br>{_e(row['preview_state'] or '尚未授权')}</p></div>"
            f"<p><a class='button' href='/p1-11/reviews/{_e(row['review_case_id'])}'>查看证据与人工授权</a></p></article>"
            for row in rows
        )
        monitor_rows = "".join(
            f"<tr><td>{_e(row['source_name'])}</td><td>{_e(row['source_data_through'])}</td><td>{_e(row['last_run_state'] or '等待首次定时检查')}</td><td>{_e(row['next_due_at'])}</td><td>{_e(row['consecutive_failures'])}</td></tr>"
            for row in monitor
        )
        return _page(
            "P1-11 埃博拉官方来源人工复核",
            "<section class='ebola-warning'><b>当前停点：AWAITING_HUMAN_EBOLA_PREVIEW_APPROVAL</b><p>以下三个候选互相独立，均只在隔离监控区和人工复核区。没有任何 Preview 决定、可信批准、Promotion 或正式业务数据写入。</p></section>"
            f"{cards}<section class='card'><h2>每日监控状态</h2><table><thead><tr><th>来源</th><th>数据截至</th><th>最近状态</th><th>下次检查</th><th>连续失败</th></tr></thead><tbody>{monitor_rows}</tbody></table></section>",
        )

    @app.get("/p1-11/reviews/{case_id}")
    def detail(case_id: uuid.UUID, request: Request) -> Response:
        session = auth(request)
        if session is None:
            return RedirectResponse("/login", 303)
        _, csrf = session
        with engine.connect() as connection:
            card = (
                connection.execute(
                    text("SELECT * FROM epidemic_ebola.review_candidates_v1 WHERE review_case_id=:id"), {"id": case_id}
                )
                .mappings()
                .one_or_none()
            )
            rows = (
                connection.execute(
                    text(
                        "SELECT row_ordinal,sanitized_json,row_sha256 FROM epidemic_discovery.review_preview_v WHERE review_case_id=:id ORDER BY row_ordinal LIMIT 20"
                    ),
                    {"id": case_id},
                )
                .mappings()
                .all()
            )
        if card is None:
            return _page("候选不存在", "<section class='card'>未找到候选。</section>")
        headers = (
            "source_as_of",
            "country_iso3",
            "metric_code",
            "value_numeric",
            "unit",
            "case_classification",
            "import_classification",
            "quality_status",
        )
        preview = "".join(
            "<tr>"
            + "".join(f"<td>{_e((row['sanitized_json'] or {}).get(key))}</td>" for key in headers)
            + f"<td>{_e(str(row['row_sha256'])[:12])}</td></tr>"
            for row in rows
        )
        form = (
            "<p><b>该版本已经生成候选 Preview 授权记录。</b></p>"
            if card["preview_decision_id"]
            else f"""
        <form method='post' action='/p1-11/reviews/{_e(case_id)}/publish-preview'>
          <input type='hidden' name='csrf' value='{_e(csrf)}'><input type='hidden' name='state_version' value='{_e(card["state_version"])}'><input type='hidden' name='idempotency_key' value='{uuid.uuid4()}'>
          <label>人工理由（至少 10 个字符）<textarea name='reason' rows='4' required minlength='10'></textarea></label>
          <label><input type='checkbox' name='not_trusted' value='1' required>我理解这不是可信数据批准</label>
          <label><input type='checkbox' name='license_reviewed' value='1' required>我已复核许可仍为 REVIEW_REQUIRED</label>
          <label><input type='checkbox' name='aggregate_only' value='1' required>我确认仅发布聚合字段</label>
          <label><input type='checkbox' name='no_cross_source_sum' value='1' required>我确认不得跨来源相加</label>
          <label><input type='checkbox' name='no_training' value='1' required>我确认不得用于训练</label>
          <label><input type='checkbox' name='no_prediction' value='1' required>我确认不得用于预测</label>
          <button type='submit'>生成埃博拉候选 Preview（不等于正式批准）</button>
        </form>"""
        )
        return _page(
            str(card["source_name"]),
            f"<section class='ebola-warning'><b>人工操作边界</b><p>该按钮只把本候选的脱敏聚合序列发布到 REVIEW_PREVIEW；不写入 epidemic.normalized_observations，不生成可信 Snapshot，不触发 Promotion。</p></section>"
            f"<section class='card'><div class='ebola-grid'><p><b>发布机构</b><br>{_e(card['publisher'])}</p><p><b>官方域名</b><br>{_e(card['official_domain'])}</p><p><b>来源家族</b><br>{_e(card['source_family'])}</p><p><b>覆盖</b><br>{_e(card['coverage_status'])}</p><p><b>Artifact</b><br><code>{_e(card['artifact_sha256'])}</code></p><p><b>Mapping</b><br><code>{_e(card['mapping_sha256'])}</code></p></div></section>"
            f"<section class='card'><h2>脱敏聚合预览</h2><table><thead><tr>{''.join(f'<th>{_e(key)}</th>' for key in headers)}<th>row SHA</th></tr></thead><tbody>{preview}</tbody></table></section>"
            f"<section class='card'><h2>人工 Preview 授权</h2>{form}</section>",
        )

    @app.post("/p1-11/reviews/{case_id}/publish-preview")
    async def publish(case_id: uuid.UUID, request: Request) -> Response:
        session = auth(request)
        if session is None:
            return RedirectResponse("/login", 303)
        username, csrf = session
        values = urllib.parse.parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        if not csrf_ok(request, csrf, str(values.get("csrf", [""])[0])):
            return HTMLResponse("CSRF_REJECTED", 403)
        try:
            state_version = int(str(values["state_version"][0]))
            key = uuid.UUID(str(values["idempotency_key"][0]))
        except (KeyError, ValueError, TypeError):
            return HTMLResponse("INVALID_REQUEST", 422)
        confirmations = {
            name: str(values.get(name, [""])[0]) == "1"
            for name in (
                "not_trusted",
                "license_reviewed",
                "aggregate_only",
                "no_cross_source_sum",
                "no_training",
                "no_prediction",
            )
        }
        reason = str(values.get("reason", [""])[0]).strip()
        try:
            with engine.begin() as connection:
                decision = connection.execute(
                    text(
                        "SELECT epidemic_ebola.submit_preview_decision(:case,:reviewer,:reason,:key,:version,CAST(:confirmations AS jsonb),'production',false)"
                    ),
                    {
                        "case": case_id,
                        "reviewer": username,
                        "reason": reason,
                        "key": key,
                        "version": state_version,
                        "confirmations": json.dumps(confirmations),
                    },
                ).scalar_one()
                job = connection.execute(
                    text("SELECT id FROM epidemic_ebola.preview_jobs WHERE decision_id=:decision"),
                    {"decision": decision},
                ).scalar_one()
        except Exception:
            return HTMLResponse("EBOLA_PREVIEW_BUSINESS_RULE_REJECTED", 409)
        return RedirectResponse(f"/p1-11/jobs/{job}", 303)

    @app.get("/p1-11/jobs/{job_id}")
    def job(job_id: uuid.UUID, request: Request) -> Response:
        if auth(request) is None:
            return RedirectResponse("/login", 303)
        with engine.connect() as connection:
            row = (
                connection.execute(text("SELECT * FROM epidemic_ebola.preview_jobs WHERE id=:id"), {"id": job_id})
                .mappings()
                .one_or_none()
            )
        if row is None:
            return _page("任务不存在", "<section class='card'>未找到任务。</section>")
        link = (
            "<p><a class='button' href='http://127.0.0.1:8120/ebola-preview'>在 Dashboard 查看</a></p>"
            if row["state"] == "PREVIEW_PUBLISHED"
            else "<p>Worker 正在处理；请刷新本页。</p>"
        )
        return _page(
            "埃博拉 Preview 任务",
            f"<section class='card'><h2>{_e(row['state'])}</h2><p>Observation：{_e(row['observation_count'])}</p>{link}</section>",
        )
