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
        "<link rel='stylesheet' href='/static/review.p1-12.css'></head><body>"
        "<div class='banner'>P1-12 Candidate Preview：审批前可见；不读取 epidemic_preview，不属于可信正式数据</div>"
        f"<header><h1>{_e(title)}</h1><nav><a class='button' href='/p1-12/reviews'>合格待复核</a><a class='button' href='/p1-12/insufficient'>证据不足/待处理</a><a class='button' href='/discoveries'>全部发现任务</a></nav></header><main>{body}</main></body></html>"
    )


def _trend(rows: list[dict[str, Any]]) -> str:
    points = [row for row in rows if row["value_numeric"] is not None]
    if len(points) < 2:
        value = points[0]["value_numeric"] if points else "—"
        return f"<article class='card'><h3>单一时间点</h3><p style='font-size:2rem'>{_e(value)} 人</p><p>只有一个时间点，不绘制虚假折线。</p></article>"
    values = [float(row["value_numeric"]) for row in points]
    low, high = min(values), max(values)
    span = max(1.0, high - low)
    coordinates = " ".join(
        f"{20 + index * 560 / max(1, len(points) - 1):.1f},{195 - (value - low) * 165 / span:.1f}"
        for index, value in enumerate(values)
    )
    return f"<svg class='chart' viewBox='0 0 600 220' role='img' aria-label='候选数据趋势'><polyline points='{coordinates}'/></svg>"


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

    @app.get("/p1-12/reviews")
    def reviews(request: Request) -> Response:
        if auth(request) is None:
            return RedirectResponse("/login", 303)
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT * FROM epidemic_p1_12.review_ready_candidates_v2 ORDER BY publisher,dataset_name,candidate_version_id"
                    )
                )
                .mappings()
                .all()
            )
        cards = "".join(
            f"<article class='card'><h2>{_e(row['dataset_name'])}</h2><p>{_e(row['publisher'])} · {_e(row['source_tier'])}</p>"
            f"<div class='p112-grid'><p><b>国家</b><br>{_e(row['request_country_name'])} ({_e(row['request_country_iso3'])})</p><p><b>病种</b><br>{_e(row['request_disease_id'])}</p>"
            f"<p><b>查询/抓取/Artifact</b><br>{_e(row['executed_query_count'])}/{_e(row['fetch_success_count'])}/{_e(row['artifact_count'])}</p>"
            f"<p><b>Candidate Preview</b><br>{_e(row['candidate_preview_row_count'])} 行；正人数 {_e(row['mappable_person_metric_count'])}</p>"
            f"<p><b>状态</b><br><span class='ok'>{_e(row['review_state'])}</span></p><p><b>许可</b><br><span class='warn'>{_e(row['license_status'])}</span></p></div>"
            f"<p><a class='button' href='/p1-12/reviews/{_e(row['review_case_id'])}'>查看 Candidate Preview 与证据</a></p></article>"
            for row in rows
        )
        empty_card = "<section class='card'>暂无合格候选。</section>"
        return _page(
            "P1-12 有证据的待复核候选",
            f"<section class='card'><h2>review-ready-v2 强门禁</h2><p>这里只显示请求上下文、免密钥搜索、抓取、Artifact、确定性解析、标准化人数 Preview 与资格哈希全部齐全的候选。当前 {len(rows)} 条；系统不会自动批准。</p></section>{cards or empty_card}",
        )

    @app.get("/p1-12/insufficient")
    def insufficient(request: Request) -> Response:
        if auth(request) is None:
            return RedirectResponse("/login", 303)
        with engine.connect() as connection:
            rows = (
                connection.execute(
                    text(
                        "SELECT * FROM epidemic_p1_12.review_insufficient_candidates_v2 ORDER BY reclassified_at,candidate_version_id"
                    )
                )
                .mappings()
                .all()
            )
        table = "".join(
            f"<tr><td>{_e(row['dataset_name'])}</td><td>{_e(row['publisher'])}</td><td>{_e(row['old_review_state'])}</td><td>{_e(row['new_state'])}</td><td>{_e(', '.join(row['reasons'] or []))}</td></tr>"
            for row in rows
        )
        return _page(
            "证据不足/待处理",
            f"<section class='card'><p>历史记录未删除；这里只展示资格重算后的当前处理状态。</p><div class='preview-table'><table><thead><tr><th>数据集</th><th>发布机构</th><th>旧状态</th><th>当前状态</th><th>中文原因</th></tr></thead><tbody>{table}</tbody></table></div></section>",
        )

    @app.get("/p1-12/reviews/{case_id}")
    def detail(case_id: uuid.UUID, request: Request) -> Response:
        session = auth(request)
        if session is None:
            return RedirectResponse("/login", 303)
        _, csrf = session
        with engine.connect() as connection:
            card = (
                connection.execute(
                    text("SELECT * FROM epidemic_p1_12.review_ready_candidates_v2 WHERE review_case_id=:id"),
                    {"id": case_id},
                )
                .mappings()
                .one_or_none()
            )
            rows = (
                connection.execute(
                    text(
                        "SELECT * FROM epidemic_p1_12.review_candidate_preview_rows_v2 WHERE review_case_id=:id ORDER BY coalesce(source_as_of_date,period_start,report_date),metric_code,row_ordinal LIMIT 250"
                    ),
                    {"id": case_id},
                )
                .mappings()
                .all()
            )
            queries = (
                connection.execute(
                    text(
                        "SELECT provider_id,language_tag,query_text,state,result_count,elapsed_ms FROM epidemic_p1_12.review_discovery_queries_v2 WHERE discovery_request_id=(SELECT discovery_request_id FROM epidemic_p1_12.review_ready_candidates_v2 WHERE review_case_id=:id) ORDER BY created_at,id"
                    ),
                    {"id": case_id},
                )
                .mappings()
                .all()
            )
            search = (
                connection.execute(
                    text(
                        "SELECT provider_id,query_id,rank,title,canonical_url,domain,observed_at FROM epidemic_discovery.review_discovery_search_results_v1 WHERE discovery_run_id=(SELECT discovery_run_id FROM epidemic_p1_12.review_ready_candidates_v2 WHERE review_case_id=:id) ORDER BY query_id,rank"
                    ),
                    {"id": case_id},
                )
                .mappings()
                .all()
            )
            fetches = (
                connection.execute(
                    text(
                        "SELECT status,http_status,content_type,byte_size,etag,last_modified,elapsed_ms,artifact_sha256,final_url FROM epidemic_discovery.review_candidate_fetches_v1 WHERE discovery_run_id=(SELECT discovery_run_id FROM epidemic_p1_12.review_ready_candidates_v2 WHERE review_case_id=:id) ORDER BY attempted_at"
                    ),
                    {"id": case_id},
                )
                .mappings()
                .all()
            )
        if card is None:
            return _page("候选不存在", "<section class='card'>未找到通过 review-ready-v2 的候选。</section>")
        table_rows = "".join(
            f"<tr><td>{_e(row['country_name'])}</td><td>{_e(row['country_iso3'])}</td><td>{_e(row['source_as_of_date'] or (str(row['period_start']) + ' 至 ' + str(row['period_end'])))}</td><td>{_e(row['disease_id'])}</td><td>{_e(row['metric_code'])}</td><td>{_e(row['value_numeric'])}</td><td>{_e(row['unit'])}</td><td>{_e(row['aggregation_type'])}</td><td>{_e(row['geographic_scope'])}</td><td><a href='{_e(row['source_url'])}' rel='noreferrer'>官方来源</a></td></tr>"
            for row in rows
        )
        locators = "".join(
            f"<li><code>{_e(row['source_row_locator'])}</code> · 原始 {_e(row['raw_value'])} → 标准化 {_e(row['normalized_value'])}</li>"
            for row in rows[:20]
        )
        query_rows = "".join(
            f"<tr><td>{_e(row['provider_id'])}</td><td>{_e(row['language_tag'])}</td><td>{_e(row['query_text'])}</td><td>{_e(row['state'])}</td><td>{_e(row['result_count'])}</td><td>{_e(row['elapsed_ms'])} ms</td></tr>"
            for row in queries
        )
        technical = _e(
            json.dumps(
                {
                    "search_results": [dict(item) for item in search],
                    "fetches": [dict(item) for item in fetches],
                    "eligibility_hash": card["eligibility_hash"],
                    "preview_sha256": card["preview_sha256"],
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )
        action_forms = "".join(
            f"<form method='post' action='/p1-12/reviews/{_e(case_id)}/decision'><input type='hidden' name='csrf' value='{_e(csrf)}'><input type='hidden' name='state_version' value='{_e(card['state_version'])}'><input type='hidden' name='decision' value='{code}'><input type='hidden' name='idempotency_key' value='{uuid.uuid4()}'><label>理由（至少 10 个字符）<textarea name='reason' minlength='10' required></textarea></label><button type='submit'>{label}</button></form>"
            for code, label in (
                ("PREVIEW_APPROVED", "批准进入 Preview"),
                ("CHANGES_REQUESTED", "要求修改"),
                ("REJECTED", "拒绝"),
            )
        )
        return _page(
            str(card["dataset_name"]),
            f"<section class='card'><div class='p112-grid'><p><b>发布机构</b><br>{_e(card['publisher'])}</p><p><b>官方域名</b><br>{_e(urllib.parse.urlsplit(str(rows[0]['source_url'])).hostname if rows else '—')}</p><p><b>Discovery request/run</b><br><code>{_e(card['discovery_request_id'])}</code><br><code>{_e(card['discovery_run_id'])}</code></p><p><b>Artifact</b><br><code>{_e(str(card['artifact_sha256'])[:16])}</code> · {_e(card['artifact_bytes'])} bytes</p><p><b>抓取时间</b><br>{_e(card['retrieved_at'])}</p><p><b>资格</b><br><code>{_e(str(card['eligibility_hash'])[:16])}</code></p></div></section>"
            f"<section class='card'><h2>Candidate Preview</h2>{_trend([dict(row) for row in rows])}<div class='preview-table'><table><thead><tr><th>国家</th><th>ISO3</th><th>数据日期/期间</th><th>病种</th><th>指标</th><th>人数</th><th>单位</th><th>新增/累计</th><th>地理范围</th><th>来源</th></tr></thead><tbody>{table_rows}</tbody></table></div></section>"
            f"<section class='card'><h2>真实检索词</h2><table><thead><tr><th>Provider</th><th>语言</th><th>查询词</th><th>状态</th><th>结果</th><th>耗时</th></tr></thead><tbody>{query_rows}</tbody></table></section>"
            f"<section class='card'><h2>字段与行定位证据</h2><ul>{locators}</ul><p>transform: <code>{_e(card['transform_version'])}</code> · license: <span class='warn'>{_e(card['license_status'])}</span></p></section>"
            f"<section class='card'><h2>人工复核操作</h2><p>以下按钮由你本人决定；操作会绑定 candidate version、Artifact、transform 与 eligibility hash。当前阶段 Codex 未替你点击。</p><div class='actions'>{action_forms}</div></section>"
            f"<details class='card'><summary>技术证据（默认折叠）</summary><pre class='tech'>{technical}</pre></details>",
        )

    @app.post("/p1-12/reviews/{case_id}/decision")
    async def decision(case_id: uuid.UUID, request: Request) -> Response:
        session = auth(request)
        if session is None:
            return RedirectResponse("/login", 303)
        username, csrf = session
        values = urllib.parse.parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
        if not csrf_ok(request, csrf, str(values.get("csrf", [""])[0])):
            return HTMLResponse("请求来源校验失败。", 403)
        try:
            state_version = int(values["state_version"][0])
            key = uuid.UUID(values["idempotency_key"][0])
            selected = values["decision"][0]
            reason = values["reason"][0].strip()
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "SELECT epidemic_p1_12.submit_review_decision_v2(:case,:decision,:reviewer,:reason,:key,:version)"
                    ),
                    {
                        "case": case_id,
                        "decision": selected,
                        "reviewer": username,
                        "reason": reason,
                        "key": key,
                        "version": state_version,
                    },
                ).scalar_one()
        except (KeyError, ValueError, TypeError):
            return HTMLResponse("请求参数无效。", 422)
        except Exception:
            return HTMLResponse("候选状态已变化或业务规则拒绝了本次操作，请刷新后重试。", 409)
        return RedirectResponse("/p1-12/reviews", 303)

    @app.get("/api/v1/p1-12/review-cases/{case_id}/candidate-preview")
    def preview_api(case_id: uuid.UUID, request: Request) -> Response:
        if auth(request) is None:
            return HTMLResponse("需要登录。", 401)
        with engine.connect() as connection:
            card = (
                connection.execute(
                    text("SELECT * FROM epidemic_p1_12.review_ready_candidates_v2 WHERE review_case_id=:id"),
                    {"id": case_id},
                )
                .mappings()
                .one_or_none()
            )
            rows = (
                connection.execute(
                    text(
                        "SELECT * FROM epidemic_p1_12.review_candidate_preview_rows_v2 WHERE review_case_id=:id ORDER BY row_ordinal LIMIT 500"
                    ),
                    {"id": case_id},
                )
                .mappings()
                .all()
            )
        if card is None:
            return HTMLResponse("候选不存在或证据不足。", 404)
        return Response(
            json.dumps(
                {"candidate": dict(card), "items": [dict(row) for row in rows]}, ensure_ascii=False, default=str
            ),
            media_type="application/json",
        )
