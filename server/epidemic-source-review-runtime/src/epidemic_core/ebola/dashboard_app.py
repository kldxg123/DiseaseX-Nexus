from __future__ import annotations

# ruff: noqa: E501
import html
import json
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import Engine, text


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


SERIES = (
    ("ebola_confirmed_cases_cumulative", "确诊", "#b91c1c"),
    ("ebola_confirmed_deaths_cumulative", "死亡", "#7c3aed"),
    ("ebola_recoveries_cumulative", "康复", "#047857"),
)


def _metric_points(rows: list[dict[str, Any]], metric: str) -> list[tuple[object, float]]:
    return [
        (row["source_as_of"], float(row["value_numeric"]))
        for row in rows
        if row["metric_code"] == metric
        and row["value_numeric"] is not None
        and row.get("import_classification", "all") in {"all", "not_reported"}
    ]


def _cumulative_chart(rows: list[dict[str, Any]]) -> str:
    available = [(code, label, color, _metric_points(rows, code)) for code, label, color in SERIES]
    available = [item for item in available if item[3]]
    if not available:
        return "<p>该来源没有可绘制的累计序列；缺失不等于 0。</p>"
    dates = sorted({str(date) for _, _, _, points in available for date, _ in points})
    values = [value for _, _, _, points in available for _, value in points]
    high = max(values) or 1.0
    paths: list[str] = []
    for _code, label, color, points in available:
        by_date = {str(date): value for date, value in points}
        coords = [
            f"{30 + index * 540 / max(1, len(dates) - 1):.1f},{170 - by_date[date] * 135 / high:.1f}"
            for index, date in enumerate(dates)
            if date in by_date
        ]
        paths.append(
            f"<polyline fill='none' stroke='{color}' stroke-width='4' points='{' '.join(coords)}'/><text x='35' y='{18 + len(paths) * 16}' fill='{color}' font-size='12'>{_e(label)}</text>"
        )
    labels = "".join(
        f"<text x='{30 + i * 540 / max(1, len(dates) - 1):.1f}' y='194' text-anchor='middle' font-size='9'>{_e(date)}</text>"
        for i, date in enumerate(dates)
    )
    return f"<svg viewBox='0 0 600 205' role='img' aria-label='累计确诊、死亡与康复'><line x1='30' y1='170' x2='570' y2='170' stroke='#94a3b8'/>{''.join(paths)}{labels}</svg>"


def _net_change_chart(rows: list[dict[str, Any]]) -> str:
    points = _metric_points(rows, "ebola_confirmed_cases_cumulative")
    if len(points) < 2:
        return "<p>至少需要两个官方报告日才能计算报告间净变化。</p>"
    changes = [(str(points[index][0]), points[index][1] - points[index - 1][1]) for index in range(1, len(points))]
    magnitude = max(1.0, max(abs(value) for _, value in changes))
    bars = []
    for index, (date, value) in enumerate(changes):
        x = 35 + index * 530 / max(1, len(changes) - 1)
        height = abs(value) * 110 / magnitude
        y = 90 - height if value >= 0 else 90
        color = "#0369a1" if value >= 0 else "#dc2626"
        bars.append(
            f"<rect x='{x - 12:.1f}' y='{y:.1f}' width='24' height='{height:.1f}' fill='{color}'/><text x='{x:.1f}' y='190' text-anchor='middle' font-size='9'>{_e(date)}</text><text x='{x:.1f}' y='{82 if value >= 0 else 108 + height:.1f}' text-anchor='middle' font-size='10'>{value:+g}</text>"
        )
    return f"<svg viewBox='0 0 600 205' role='img' aria-label='报告间累计确诊净变化'><line x1='25' y1='90' x2='575' y2='90' stroke='#64748b'/>{''.join(bars)}</svg><p class='small'>派生指标：report_to_report_net_change；不等同于按发病日统计的新增病例，负修订原样保留。</p>"


def _confirmed_changes(rows: list[dict[str, Any]]) -> dict[str, float]:
    points = _metric_points(rows, "ebola_confirmed_cases_cumulative")
    return {str(points[index][0]): points[index][1] - points[index - 1][1] for index in range(1, len(points))}


def _quality_panel(rows: list[dict[str, Any]], publication: Any) -> str:
    missing = sum(row["value_numeric"] is None for row in rows)
    revised = sum(int(row.get("revision_number") or 1) > 1 for row in rows)
    warnings = sum(bool(row.get("quality_flags")) for row in rows)
    latest = max((str(row["source_as_of"]) for row in rows), default="—")
    return f"<ul><li>水位：{_e(latest)}</li><li>Observation：{len(rows)}</li><li>缺失：{missing}</li><li>修订：{revised}</li><li>解析/质量警告：{warnings}</li><li>数据层：EBOLA_REVIEW_PREVIEW</li><li>许可：REVIEW_REQUIRED</li><li>跨源相加：禁止</li><li>训练/预测：禁止</li><li>Snapshot：<code>{_e(publication['snapshot_sha256'])}</code></li></ul>"


def install_routes(
    app: FastAPI,
    engine: Engine,
    authorized: Callable[[Request], bool],
    unauthorized: Callable[[], Response],
    query: Callable[..., list[dict[str, Any]]],
    json_response: Callable[..., Response],
) -> None:
    @app.get("/api/v1/ebola/options")
    def options(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        items = query(engine, "SELECT * FROM epidemic_ebola.active_preview_publications_v1 ORDER BY source_key")
        return json_response(
            {
                "state": "READY" if items else "EMPTY",
                "items": items,
                "data_tier": "EBOLA_REVIEW_PREVIEW",
                "cross_source_sum_allowed": False,
                "production_usable": False,
                "training_eligible": False,
            }
        )

    @app.get("/api/v1/ebola/series")
    def series(request: Request, publication_id: uuid.UUID) -> Response:
        if not authorized(request):
            return unauthorized()
        metadata = query(
            engine,
            "SELECT * FROM epidemic_ebola.active_preview_publications_v1 WHERE publication_id=:id",
            {"id": publication_id},
        )
        if not metadata:
            return json_response({"error_code": "EBOLA_PREVIEW_PUBLICATION_NOT_FOUND"}, 404)
        rows = query(
            engine,
            "SELECT * FROM epidemic_ebola.preview_series_v1 WHERE publication_id=:id ORDER BY source_as_of,country_iso3,metric_code",
            {"id": publication_id},
        )
        return json_response(
            {
                "metadata": metadata[0],
                "items": rows,
                "data_tier": "EBOLA_REVIEW_PREVIEW",
                "cross_source_sum_allowed": False,
                "missing_is_zero": False,
                "case_classification_preserved": True,
                "imported_cases_not_reassigned": True,
            }
        )

    @app.get("/api/v1/ebola/monitor-status")
    def monitor_status(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        return json_response(
            {
                "items": query(engine, "SELECT * FROM epidemic_ebola.monitor_status_v1 ORDER BY source_key"),
                "timezone": "Asia/Shanghai",
                "local_time": "02:30",
            }
        )

    @app.get("/ebola-preview")
    def page(request: Request) -> Response:
        if not authorized(request):
            return HTMLResponse("AUTHENTICATION_REQUIRED", 401)
        with engine.connect() as connection:
            publications = (
                connection.execute(
                    text("SELECT * FROM epidemic_ebola.active_preview_publications_v1 ORDER BY source_key")
                )
                .mappings()
                .all()
            )
            monitoring = (
                connection.execute(text("SELECT * FROM epidemic_ebola.monitor_status_v1 ORDER BY source_key"))
                .mappings()
                .all()
            )
            series_by_publication: dict[uuid.UUID, list[dict[str, Any]]] = {}
            for publication in publications:
                publication_rows = (
                    connection.execute(
                        text(
                            "SELECT * FROM epidemic_ebola.preview_series_v1 WHERE publication_id=:id ORDER BY source_as_of,metric_code"
                        ),
                        {"id": publication["publication_id"]},
                    )
                    .mappings()
                    .all()
                )
                series_by_publication[publication["publication_id"]] = [dict(row) for row in publication_rows]
        monitor_rows = "".join(
            f"<tr><td>{_e(row['source_name'])}</td><td>{_e(row['source_data_through'])}</td><td>{_e(row['last_run_state'] or '等待定时检查')}</td><td>{_e(row['next_due_at'])}</td></tr>"
            for row in monitoring
        )
        if not publications:
            content = "<section class='notice'><h2>尚无埃博拉候选 Preview</h2><p>三个官方候选已经停在人工复核区。请先在 Source Review Console 逐条查看证据，并由你本人决定是否生成候选 Preview。</p><p><a href='http://127.0.0.1:8121/p1-11/reviews'>打开埃博拉人工复核</a></p></section>"
        else:
            cards = ""
            for publication in publications:
                rows: list[dict[str, Any]] = series_by_publication[publication["publication_id"]]
                changes = _confirmed_changes(rows)
                table_rows = "".join(
                    f"<tr><td>{_e(row['source_as_of'])}</td><td>{_e(row['publication_date'])}</td><td>{_e(row['country_iso3'])}</td><td>{_e(publication['outbreak_title'])}</td><td>{_e(publication['pathogen_variant_code'])}</td><td>{_e(row['case_classification'])}/{_e(row['import_classification'])}</td><td>{_e(row['metric_code'])}</td><td>{_e(row['value_numeric'])}</td><td>{_e(changes.get(str(row['source_as_of'])) if row['metric_code'] == 'ebola_confirmed_cases_cumulative' else None)}</td><td>{_e(row['revision_number'])}</td><td>{_e(row['quality_status'])}</td><td>{_e(row['source_key'])}</td><td><code>{_e(row['artifact_sha256'])}</code></td><td><code>{_e(publication['snapshot_sha256'])}</code></td><td><code>{_e(row['lineage_sha256'])}</code></td></tr>"
                    for row in rows
                )
                technical = json.dumps(dict(publication), ensure_ascii=False, default=str, indent=2)
                cards += f"<section class='source-card'><h2>{_e(publication['source_name'])}</h2><p>{_e(publication['publisher'])} · {_e(publication['official_domain'])}</p><p><b>疾病：</b>埃博拉病毒病 · <b>病毒种：</b>{_e(publication['pathogen_variant_code'])} · <b>事件：</b>{_e(publication['outbreak_title'])} · <b>报告日范围：</b>{_e(publication['date_min'])} 至 {_e(publication['date_max'])}</p><div class='three'><article><h3>图 1：官方报告累计趋势</h3>{_cumulative_chart(rows)}</article><article><h3>图 2：报告间净变化</h3>{_net_change_chart(rows)}</article><article><h3>图 3：质量、水位与修订</h3>{_quality_panel(rows, publication)}</article></div><details><summary>查看完整聚合数据表</summary><div class='table-wrap'><table><thead><tr><th>source-as-of</th><th>publication</th><th>country</th><th>event</th><th>variant</th><th>classification</th><th>metric</th><th>value</th><th>report net change</th><th>revision</th><th>quality</th><th>source</th><th>Artifact</th><th>Snapshot</th><th>lineage</th></tr></thead><tbody>{table_rows}</tbody></table></div></details><details><summary>技术 JSON</summary><pre>{_e(technical)}</pre></details></section>"
            content = cards
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>埃博拉官方来源候选 Preview</title>"
            "<link rel='stylesheet' href='/static/dashboard.p1-11-ebola.css'></head><body>"
            "<header><h1>埃博拉官方来源候选 Preview</h1><p><a href='/'>返回原 Dashboard</a> · <a href='http://127.0.0.1:8121/p1-11/reviews'>人工复核</a></p></header>"
            "<p class='warning'><b>候选演示数据：</b>未进入可信数据；不同来源的报告时点、病例分类和进口/本地口径不得相加。缺失不等于 0。</p>"
            f"{content}<section class='monitor'><h2>每日检查状态（Asia/Shanghai 02:30）</h2><div class='table-wrap'><table><thead><tr><th>来源</th><th>数据截至</th><th>最近状态</th><th>下次检查</th></tr></thead><tbody>{monitor_rows}</tbody></table></div></section></body></html>"
        )
