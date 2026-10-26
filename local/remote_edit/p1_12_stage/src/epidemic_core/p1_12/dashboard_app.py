from __future__ import annotations

# ruff: noqa: E501
import html
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import Engine


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def _line_chart(rows: list[dict[str, Any]], title: str, unit: str) -> str:
    if not rows:
        return f"<article class='panel'><h3>{_e(title)}</h3><p>该筛选下没有数据。</p></article>"
    values = [float(row["value_numeric"]) for row in rows]
    low, high = min(values), max(values)
    span = max(1.0, high - low)
    points = " ".join(
        f"{30 + index * 640 / max(1, len(rows) - 1):.1f},{210 - (value - low) * 170 / span:.1f}"
        for index, value in enumerate(values)
    )
    latest = rows[-1]
    return f"<article class='panel'><h3>{_e(title)}</h3><svg viewBox='0 0 700 240' role='img' aria-label='{_e(title)}'><polyline points='{points}'/></svg><p>最新：{_e(latest['period_end'])} · {_e(latest['value_numeric'])} {_e(unit)}</p></article>"


def install_routes(
    app: FastAPI,
    engine: Engine,
    authorized: Callable[[Request], bool],
    unauthorized: Callable[[], Response],
    query: Callable[..., list[dict[str, Any]]],
    json_response: Callable[..., Response],
) -> None:
    @app.get("/api/v1/p1-12/trusted/catalog")
    def catalog(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        return json_response(
            {
                "items": query(
                    engine, "SELECT * FROM epidemic_p1_12.trusted_dashboard_catalog_v1 ORDER BY source_key,snapshot_id"
                ),
                "data_layer": "TRUSTED",
                "candidate_preview_included": False,
            }
        )

    @app.get("/api/v1/p1-12/trusted/series")
    def series(
        request: Request,
        disease: str,
        country_iso3: str,
        metric: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        params = {"disease": disease, "country": country_iso3, "metric": metric, "start": start, "end": end}
        rows = query(
            engine,
            "SELECT * FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE disease_slug=:disease AND country_iso3=:country AND (CAST(:metric AS text) IS NULL OR metric_code=:metric) AND (CAST(:start AS date) IS NULL OR period_end>=CAST(:start AS date)) AND (CAST(:end AS date) IS NULL OR period_start<=CAST(:end AS date)) ORDER BY period_start,metric_code,revision",
            params,
        )
        return json_response(
            {
                "items": rows,
                "data_layer": "TRUSTED",
                "all_saved_when_time_empty": start is None and end is None,
                "interpolated": False,
                "predicted": False,
            }
        )

    @app.get("/api/v1/p1-12/trusted/update-status")
    def update_status(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        return json_response(
            {
                "items": query(engine, "SELECT * FROM epidemic_p1_12.trusted_update_status_v1 ORDER BY schedule_id"),
                "daily_means_check": True,
            }
        )

    @app.get("/p1-12/trusted")
    def page(
        request: Request,
        disease: str | None = None,
        country: str | None = None,
        metric: str | None = None,
        start: str | None = None,
        end: str | None = None,
        layer: str = "TRUSTED",
    ) -> Response:
        if not authorized(request):
            return HTMLResponse("AUTHENTICATION_REQUIRED", 401)
        catalog_rows = query(
            engine, "SELECT * FROM epidemic_p1_12.trusted_dashboard_catalog_v1 ORDER BY source_key,snapshot_id"
        )
        options = query(
            engine,
            "SELECT DISTINCT disease_slug,disease_name,country_iso3,country_name FROM epidemic_p1_12.trusted_dashboard_series_v1 ORDER BY disease_slug,country_name",
        )
        selected_disease = disease or (str(options[0]["disease_slug"]) if options else "")
        country_options = [row for row in options if row["disease_slug"] == selected_disease]
        selected_country = country or (str(country_options[0]["country_iso3"]) if country_options else "")
        metrics = (
            query(
                engine,
                "SELECT DISTINCT metric_code,metric_name,unit FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE disease_slug=:disease AND country_iso3=:country ORDER BY unit,metric_code",
                {"disease": selected_disease, "country": selected_country},
            )
            if selected_country
            else []
        )
        rows = (
            query(
                engine,
                "SELECT * FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE disease_slug=:disease AND country_iso3=:country AND (CAST(:metric AS text) IS NULL OR metric_code=:metric) AND (CAST(:start AS date) IS NULL OR period_end>=CAST(:start AS date)) AND (CAST(:end AS date) IS NULL OR period_start<=CAST(:end AS date)) ORDER BY period_start,metric_code,revision",
                {
                    "disease": selected_disease,
                    "country": selected_country,
                    "metric": metric,
                    "start": start,
                    "end": end,
                },
            )
            if selected_country
            else []
        )
        disease_options = "".join(
            f"<option value='{_e(row['disease_slug'])}'{' selected' if row['disease_slug'] == selected_disease else ''}>{_e(row['disease_name'])}</option>"
            for row in {row["disease_slug"]: row for row in options}.values()
        )
        country_html = "".join(
            f"<option value='{_e(row['country_iso3'])}'{' selected' if row['country_iso3'] == selected_country else ''}>{_e(row['country_name'])} ({_e(row['country_iso3'])})</option>"
            for row in country_options
        )
        metric_html = "<option value=''>全部指标</option>" + "".join(
            f"<option value='{_e(row['metric_code'])}'{' selected' if row['metric_code'] == metric else ''}>{_e(row['metric_name'])} [{_e(row['unit'])}]</option>"
            for row in metrics
        )
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row["metric_code"])].append(row)
        if selected_disease == "ebola-disease":
            chart_html = _line_chart(
                grouped.get("ebola_confirmed_cases_cumulative", []), "1. 确认病例累计趋势", "person"
            ) + _line_chart(
                grouped.get("ebola_confirmed_deaths_cumulative", []) + grouped.get("ebola_recoveries_cumulative", []),
                "2. 死亡与康复累计趋势（分指标见下表）",
                "person",
            )
            third = "<article class='panel'><h3>3. 来源、水位与修订</h3><p>WHO 官方 DON 事件系列；病原体 BDBV。图中变化是报告间派生差值，不等同实际发病新增。来源可能回溯修订，负差值保留 revision/supersedes。</p></article>"
        else:
            person_rows = [row for row in rows if row["unit"] == "person"]
            detection_rows = [row for row in rows if row["unit"] == "detection"]
            specimen_rows = [row for row in rows if row["unit"] == "specimen"]
            chart_html = _line_chart(
                person_rows, "1. FluID ILI/SARI 人数周趋势（综合征，不等同确诊流感）", "person"
            ) + _line_chart(detection_rows, "2. FluNet A/B 与亚型检出构成", "detection")
            third = _line_chart(specimen_rows, "3. FluNet 检测量（独立 specimen 轴）", "specimen")
        table_rows = "".join(
            f"<tr><td>{_e(row['period_start'])}</td><td>{_e(row['period_end'])}</td><td>{_e(row['metric_code'])}</td><td>{_e(row['value_numeric'])}</td><td>{_e(row['unit'])}</td><td>{_e(row['source_as_of_date'])}</td><td>{_e(row['pathogen_variant'])}</td><td>{_e(row['case_definition'])}</td><td>{_e(row['publisher'])}</td><td><a href='{_e(row['source_url'])}' rel='noreferrer'>来源</a></td><td>{_e(row['revision'])}</td><td><code>{_e(str(row['artifact_sha256'])[:12])}</code></td></tr>"
            for row in rows
        )
        relevant_catalog = [
            row
            for row in catalog_rows
            if selected_disease in (row["diseases"] or []) and selected_country in (row["countries"] or [])
        ]
        source_cards = "".join(
            f"<article><b>{_e(row['publisher'])}</b><p>{_e(row['source_name'])}</p><p>trust tier: {_e(row['trust_tier'])}</p><p>Snapshot <code>{_e(row['snapshot_id'])}</code> · <code>{_e(str(row['snapshot_sha256'])[:16])}</code> · {_e(row['row_count'])} 行</p><p>数据 {_e(row['date_min'])} 至 {_e(row['date_max'])} · 最近检查 {_e(row['last_checked_at'])} · 最近变化 {_e(row['last_source_change_at'])}</p></article>"
            for row in relevant_catalog
        )
        status = query(engine, "SELECT * FROM epidemic_p1_12.trusted_update_status_v1 ORDER BY schedule_id")
        status_rows = "".join(
            f"<tr><td>{_e(row['schedule_id'])}</td><td>{_e(row['last_run_state'])}</td><td>{_e(row['last_checked_at'])}</td><td>{_e(row['source_data_through'])}</td><td>{_e(row['next_due_at'])}</td></tr>"
            for row in status
        )
        params_note = (
            "时间未选择：显示该国家＋病种在可信正式层中全部已保存数据。"
            if not start and not end
            else "已按指定时间范围裁剪。"
        )
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>P1-12 可信疫情 Dashboard</title><link rel='stylesheet' href='/static/dashboard.p1-12-trusted.css'></head><body>"
            "<header><h1>可信正式疫情 Dashboard</h1><p><a href='/'>返回主 Dashboard</a> · <a href='http://127.0.0.1:8121/p1-12/reviews'>打开 Candidate Review Console</a></p><p>默认只展示 Trusted。未审批 Candidate Preview 不会出现在此页面。</p></header><main>"
            f"<form class='filters' method='get'><label>数据层<select name='layer'><option value='TRUSTED' selected>可信正式</option><option value='REVIEW_PREVIEW'>已批准候选 Preview</option></select></label><label>病种<select name='disease'>{disease_options}</select></label><label>国家<select name='country'>{country_html}</select></label><label>指标<select name='metric'>{metric_html}</select></label><label>开始日期<input type='date' name='start' value='{_e(start or '')}'></label><label>结束日期<input type='date' name='end' value='{_e(end or '')}'></label><button type='submit'>应用筛选</button></form><p class='notice'>{_e(params_note)}</p>"
            f"<section class='sources'>{source_cards}</section><section class='triptych'>{chart_html}{third}</section>"
            f"<section class='card'><h2>正式观测表</h2><div class='scroll'><table><thead><tr><th>开始</th><th>结束</th><th>指标</th><th>值</th><th>单位</th><th>source-as-of</th><th>病原体/亚型</th><th>口径</th><th>发布机构</th><th>来源</th><th>revision</th><th>Artifact</th></tr></thead><tbody>{table_rows}</tbody></table></div></section>"
            f"<section class='card'><h2>每日检查状态</h2><p>每日表示检查来源；来源无变化不会重复写入观测或 Snapshot。</p><div class='scroll'><table><thead><tr><th>Schedule</th><th>最近状态</th><th>最近检查</th><th>数据截至</th><th>下次检查</th></tr></thead><tbody>{status_rows}</tbody></table></div></section></main></body></html>"
        )
