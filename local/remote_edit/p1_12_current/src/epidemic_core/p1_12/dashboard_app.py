from __future__ import annotations

# ruff: noqa: E501
import datetime as dt
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


OUTBREAK_REGISTRY: dict[str, dict[str, object]] = {
    "ebola-west-africa-2014-2016": {
        "label": "2014–2016 西非 Ebola 疫情",
        "disease": "ebola-disease",
        "countries": ["GIN", "LBR", "SLE"],
        "status": "PENDING_REVIEW",
        "source": "WHO official West Africa Ebola outbreak page / GHO legacy lead",
        "message": "当前没有已批准的 Trusted 历史观测；待复核来源请在 Review 页面查看。",
    },
    "ebola-drc-north-kivu-ituri-2018-2020": {
        "label": "2018–2020 刚果（金）North Kivu/Ituri Ebola 疫情",
        "disease": "ebola-disease",
        "countries": ["COD"],
        "status": "PENDING_REVIEW",
        "source": "WHO official North Kivu-Ituri Ebola outbreak page",
        "message": "当前没有已批准的 Trusted 历史观测；待复核来源请在 Review 页面查看。",
    },
    "bvd-drc-uganda-2026": {
        "label": "2026 DRC/Uganda Bundibugyo virus disease 当前事件",
        "disease": "ebola-disease",
        "countries": ["COD", "UGA"],
        "status": "TRUSTED_AVAILABLE_FOR_COD",
        "source": "WHO Disease Outbreak News / WHO AFRO current reports",
        "message": "当前事件已有 COD Trusted 报告级数据；UGA 相关来源仍需复核后才可进入 Trusted。",
    },
}


def _parse_date(value: object) -> dt.date | None:
    if not value:
        return None
    return dt.date.fromisoformat(str(value)[:10])


def _shift_years(value: dt.date, years: int) -> dt.date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _apply_time_preset(
    rows: list[dict[str, Any]],
    time_preset: str | None,
    start: str | None,
    end: str | None,
) -> tuple[list[dict[str, Any]], str | None, str | None, str, str | None]:
    if start or end or not time_preset:
        note = "时间未选择：显示该国家＋病种在可信正式层中全部已保存数据。" if not start and not end else "已按指定时间范围裁剪。"
        return rows, start, end, note, None
    if time_preset not in {"5y", "10y", "major_outbreak"}:
        return rows, start, end, "未知时间预设已忽略；仍显示全部 Trusted 数据。", "INVALID_TIME_PRESET_IGNORED"
    if time_preset == "major_outbreak":
        return rows, start, end, "重大疫情入口不会使用滚动 5/10 年限制；只显示已批准 Trusted，待复核历史来源不会混入图表。", None
    dates = [_parse_date(row.get("source_data_through") or row.get("period_end")) for row in rows]
    dates = [value for value in dates if value is not None]
    if not dates:
        return [], None, None, "该筛选下没有可锚定的 Trusted 日期；未生成补零或插值。", "NO_TRUSTED_DATE_ANCHOR"
    anchor = max(dates)
    window_start = _shift_years(anchor, 5 if time_preset == "5y" else 10)
    filtered = [row for row in rows if (_parse_date(row.get("period_end")) or dt.date.min) >= window_start]
    return filtered, window_start.isoformat(), anchor.isoformat(), f"{time_preset} 以当前所选 Trusted 数据最新日期 {anchor.isoformat()} 为锚点；仅过滤已有报告点，不补零、不插值。", None


def _outbreak_notice(outbreak_id: str | None) -> str:
    if not outbreak_id:
        return ""
    event = OUTBREAK_REGISTRY.get(outbreak_id)
    if not event:
        return "<section class='notice'><b>重大疫情：</b>未知事件 ID；未改变 Trusted 查询。</section>"
    countries = "、".join(str(x) for x in event["countries"])
    return (
        "<section class='notice'><b>重大疫情：</b>"
        f"{_e(event['label'])} · 国家/地区：{_e(countries)} · 状态：{_e(event['status'])}。"
        f"<br>{_e(event['message'])} <a href='http://127.0.0.1:8121/reviews'>打开 Review 页面</a>。"
        "</section>"
    )


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
        time_preset: str | None = None,
        outbreak_id: str | None = None,
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        if time_preset not in {None, "", "5y", "10y", "major_outbreak"}:
            return json_response({"detail": "INVALID_TIME_PRESET"}, 422)
        if outbreak_id and outbreak_id not in OUTBREAK_REGISTRY:
            return json_response({"detail": "INVALID_OUTBREAK_ID"}, 422)
        rows = query(
            engine,
            "SELECT * FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE disease_slug=:disease AND country_iso3=:country AND (CAST(:metric AS text) IS NULL OR metric_code=:metric) ORDER BY period_start,metric_code,revision",
            {"disease": disease, "country": country_iso3, "metric": metric},
        )
        if start or end:
            start_date, end_date = _parse_date(start), _parse_date(end)
            rows = [
                row
                for row in rows
                if (start_date is None or (_parse_date(row.get("period_end")) or dt.date.min) >= start_date)
                and (end_date is None or (_parse_date(row.get("period_start")) or dt.date.max) <= end_date)
            ]
            effective_start, effective_end, note, warning = start, end, "已按指定时间范围裁剪。", None
        else:
            rows, effective_start, effective_end, note, warning = _apply_time_preset(rows, time_preset or None, start, end)
        if time_preset == "major_outbreak" and outbreak_id in {
            "ebola-west-africa-2014-2016",
            "ebola-drc-north-kivu-ituri-2018-2020",
        }:
            rows = []
            note = "该重大历史疫情目前只有待复核官方来源，尚无已批准 Trusted 历史观测；不会把当前事件或 Candidate 混入 Trusted 图表。"
        return json_response(
            {
                "items": rows,
                "data_layer": "TRUSTED",
                "candidate_preview_included": False,
                "all_saved_when_time_empty": start is None and end is None and not time_preset,
                "interpolated": False,
                "predicted": False,
                "zero_filled": False,
                "time_preset": time_preset,
                "outbreak_id": outbreak_id,
                "effective_start": effective_start,
                "effective_end": effective_end,
                "note": note,
                "warning": warning,
                "outbreak": OUTBREAK_REGISTRY.get(outbreak_id or ""),
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
        time_preset: str | None = None,
        outbreak_id: str | None = None,
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
                "SELECT * FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE disease_slug=:disease AND country_iso3=:country AND (CAST(:metric AS text) IS NULL OR metric_code=:metric) ORDER BY period_start,metric_code,revision",
                {"disease": selected_disease, "country": selected_country, "metric": metric},
            )
            if selected_country
            else []
        )
        if start or end:
            start_date, end_date = _parse_date(start), _parse_date(end)
            rows = [
                row
                for row in rows
                if (start_date is None or (_parse_date(row.get("period_end")) or dt.date.min) >= start_date)
                and (end_date is None or (_parse_date(row.get("period_start")) or dt.date.max) <= end_date)
            ]
            params_note, preset_warning = "已按指定时间范围裁剪。", None
        else:
            rows, _effective_start, _effective_end, params_note, preset_warning = _apply_time_preset(
                rows, time_preset or None, start, end
            )
        if time_preset == "major_outbreak" and outbreak_id in {
            "ebola-west-africa-2014-2016",
            "ebola-drc-north-kivu-ituri-2018-2020",
        }:
            rows = []
            params_note = "该重大历史疫情目前只有待复核官方来源，尚无已批准 Trusted 历史观测；不会把当前事件或 Candidate 混入 Trusted 图表。"
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
        ) or "<tr><td colspan='12'>当前筛选没有已批准的 Trusted 观测；待复核历史来源请在 Review 页面查看。缺失不会显示为 0。</td></tr>"
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
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>P1-12 可信疫情 Dashboard</title><link rel='stylesheet' href='/static/dashboard.p1-12-trusted.css'></head><body>"
            "<header><h1>可信正式疫情 Dashboard</h1><p><a href='/'>返回主 Dashboard</a> · <a href='http://127.0.0.1:8121/p1-12/reviews'>打开 Candidate Review Console</a></p><p>默认只展示 Trusted。未审批 Candidate Preview 不会出现在此页面。</p></header><main>"
            f"<form class='filters' method='get'><label>数据层<select name='layer'><option value='TRUSTED' selected>可信正式</option><option value='REVIEW_PREVIEW'>已批准候选 Preview</option></select></label><label>病种<select name='disease'>{disease_options}</select></label><label>国家<select name='country'>{country_html}</select></label><label>指标<select name='metric'>{metric_html}</select></label><label>时间入口<select name='time_preset'><option value='' {'selected' if not time_preset else ''}>全部 Trusted</option><option value='5y' {'selected' if time_preset == '5y' else ''}>最近 5 年</option><option value='10y' {'selected' if time_preset == '10y' else ''}>最近 10 年</option><option value='major_outbreak' {'selected' if time_preset == 'major_outbreak' else ''}>重大疫情</option></select></label><label>重大疫情<select name='outbreak_id'><option value=''>不指定</option><option value='ebola-west-africa-2014-2016' {'selected' if outbreak_id == 'ebola-west-africa-2014-2016' else ''}>2014–2016 西非</option><option value='ebola-drc-north-kivu-ituri-2018-2020' {'selected' if outbreak_id == 'ebola-drc-north-kivu-ituri-2018-2020' else ''}>2018–2020 DRC North Kivu/Ituri</option><option value='bvd-drc-uganda-2026' {'selected' if outbreak_id == 'bvd-drc-uganda-2026' else ''}>2026 BDBV 当前事件</option></select></label><label>开始日期<input type='date' name='start' value='{_e(start or '')}'></label><label>结束日期<input type='date' name='end' value='{_e(end or '')}'></label><button type='submit'>应用筛选</button></form><p class='notice'>{_e(params_note)} 该来源为官方报告级数据，点与点之间不代表连续日报；系统不会补零或插值。{' 警告：' + _e(preset_warning) if preset_warning else ''}</p>{_outbreak_notice(outbreak_id)}"
            f"<section class='sources'>{source_cards}</section><section class='triptych'>{chart_html}{third}</section>"
            f"<section class='card'><h2>正式观测表</h2><div class='scroll'><table><thead><tr><th>开始</th><th>结束</th><th>指标</th><th>值</th><th>单位</th><th>source-as-of</th><th>病原体/亚型</th><th>口径</th><th>发布机构</th><th>来源</th><th>revision</th><th>Artifact</th></tr></thead><tbody>{table_rows}</tbody></table></div></section>"
            f"<section class='card'><h2>每日检查状态</h2><p>每日表示检查来源；来源无变化不会重复写入观测或 Snapshot。</p><div class='scroll'><table><thead><tr><th>Schedule</th><th>最近状态</th><th>最近检查</th><th>数据截至</th><th>下次检查</th></tr></thead><tbody>{status_rows}</tbody></table></div></section></main></body></html>"
        )
