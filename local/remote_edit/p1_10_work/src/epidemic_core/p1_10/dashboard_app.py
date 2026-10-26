from __future__ import annotations

# ruff: noqa: E501
# mypy: ignore-errors
import datetime as dt
import html
import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import Engine


def _e(value: object) -> str:
    return html.escape(str(value if value is not None else "—"), quote=True)


def install_routes(
    app: FastAPI,
    engine: Engine,
    authorized: Callable[[Request], bool],
    unauthorized: Callable[[], Response],
    query: Callable[..., list[dict[str, Any]]],
    json_response: Callable[..., Response],
) -> None:
    @app.get("/api/v1/preview/options")
    def preview_options(request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        items = query(
            engine,
            "SELECT id AS publication_id,source_key,source_name,publisher,official_domain,country_iso2,disease_slug,"
            "license_status,coverage_status,data_tier,production_usable,training_eligible,national_aggregate,"
            "snapshot_id,snapshot_sha256,observation_count,total_value,date_min,date_max,state_count,"
            "municipality_count,artifact_sha256,metric_code,display_name_zh,unit,data_nature,"
            "source_date_semantics,geography_semantics FROM epidemic_preview.active_preview_publications_v1 "
            "ORDER BY published_at DESC,id",
        )
        return json_response(
            {
                "items": items,
                "data_tier": "REVIEW_PREVIEW",
                "production_usable": False,
                "training_eligible": False,
                "national_aggregate": False,
                "warning": "候选演示数据未经正式批准，不代表完整或全国疫情数据，不用于训练、预测、每日更新或公共结论。",
            }
        )

    @app.get("/api/v1/preview/series")
    def preview_series(
        request: Request,
        publication_id: uuid.UUID,
        start_date: str | None = None,
        end_date: str | None = None,
        state_code: str | None = None,
        municipality_code: str | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> Response:
        if not authorized(request):
            return unauthorized()
        if page < 1 or not 1 <= page_size <= 250:
            return json_response({"detail": "INVALID_PAGINATION"}, 400)
        try:
            start = dt.date.fromisoformat(start_date) if start_date else None
            end = dt.date.fromisoformat(end_date) if end_date else None
        except ValueError:
            return json_response({"detail": "INVALID_DATE_RANGE"}, 400)
        if start and end and start > end:
            return json_response({"detail": "INVALID_DATE_RANGE"}, 400)
        metadata = query(
            engine,
            "SELECT * FROM epidemic_preview.active_preview_publications_v1 WHERE id=:publication",
            {"publication": publication_id},
        )
        if not metadata:
            return json_response({"detail": "PREVIEW_PUBLICATION_NOT_ACTIVE"}, 404)
        params = {
            "publication": publication_id,
            "start": start,
            "end": end,
            "state": state_code or None,
            "municipality": municipality_code or None,
            "limit": page_size,
            "offset": (page - 1) * page_size,
        }
        where = (
            "publication_id=:publication AND (CAST(:start AS date) IS NULL OR observation_date>=CAST(:start AS date)) "
            "AND (CAST(:end AS date) IS NULL OR observation_date<=CAST(:end AS date)) "
            "AND (CAST(:state AS text) IS NULL OR state_code=:state) "
            "AND (CAST(:municipality AS text) IS NULL OR municipality_code=:municipality)"
        )
        total = query(
            engine,
            f"SELECT count(*) AS total,COALESCE(sum(value_numeric),0) AS value_total FROM epidemic_preview.preview_dashboard_series_v1 WHERE {where}",
            params,
        )[0]
        items = query(
            engine,
            f"SELECT * FROM epidemic_preview.preview_dashboard_series_v1 WHERE {where} ORDER BY observation_date,state_code,municipality_code,observation_id LIMIT :limit OFFSET :offset",
            params,
        )
        dimensions = query(
            engine,
            "SELECT DISTINCT state_code,municipality_code FROM epidemic_preview.preview_dashboard_series_v1 "
            f"WHERE {where} ORDER BY state_code,municipality_code",
            params,
        )
        return json_response(
            {
                "metadata": metadata[0],
                "items": items,
                "dimensions": dimensions,
                "page": page,
                "page_size": page_size,
                "total": total["total"],
                "value_total": total["value_total"],
                "data_tier": "REVIEW_PREVIEW",
                "production_usable": False,
                "training_eligible": False,
                "national_aggregate": False,
                "license_status": metadata[0]["license_status"],
                "coverage_status": metadata[0]["coverage_status"],
                "complete_snapshot_when_dates_empty": start is None and end is None,
                "cross_source_sum": False,
            }
        )

    @app.get("/api/v1/preview/lineage/{publication_id}")
    def preview_lineage_api(publication_id: uuid.UUID, request: Request) -> Response:
        if not authorized(request):
            return unauthorized()
        rows = query(
            engine, "SELECT * FROM epidemic_preview.preview_lineage_v1 WHERE publication_id=:id", {"id": publication_id}
        )
        if not rows:
            return json_response({"detail": "PREVIEW_LINEAGE_NOT_FOUND"}, 404)
        return json_response({"item": rows[0], "data_tier": "REVIEW_PREVIEW", "raw_fields_exposed": False})

    @app.get("/preview/lineage/{publication_id}")
    def preview_lineage_page(publication_id: uuid.UUID, request: Request) -> Response:
        if not authorized(request):
            return HTMLResponse("AUTHENTICATION_REQUIRED", 401)
        rows = query(
            engine, "SELECT * FROM epidemic_preview.preview_lineage_v1 WHERE publication_id=:id", {"id": publication_id}
        )
        if not rows:
            return HTMLResponse("PREVIEW_LINEAGE_NOT_FOUND", 404)
        item = rows[0]
        chain = (
            ("Discovery Run", "discovery_run_id"),
            ("Candidate", "candidate_id"),
            ("Candidate Version", "candidate_version_id"),
            ("Artifact", "artifact_sha256"),
            ("Preview 人工授权", "preview_decision_id"),
            ("Mapping", "mapping_contract_id"),
            ("Preview Snapshot", "snapshot_id"),
            ("Dashboard publication", "publication_id"),
        )
        cards = "".join(f"<li><b>{_e(label)}</b><br><code>{_e(item[key])}</code></li>" for label, key in chain)
        return HTMLResponse(
            "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>候选 Preview 完整血缘</title><style>body{font-family:system-ui;max-width:1000px;margin:auto;padding:20px;background:#f4f7fb}"
            ".warning{background:#fffaeb;border-left:5px solid #f79009;padding:14px}li{background:white;border:1px solid #d0d5dd;border-radius:10px;padding:12px;margin:8px 0}code{word-break:break-all}</style></head><body>"
            "<h1>候选 Preview 完整血缘</h1><p class='warning'>未经正式批准；不进入训练、预测、每日更新或 trusted 数据。</p>"
            f"<ol>{cards}</ol><p>Snapshot SHA-256：<code>{_e(item['snapshot_sha256'])}</code></p>"
            f"<p>质量：{_e(item['quality_status'])}；PII：{_e(item['pii_status'])}；许可：{_e(item['license_status'])}；范围：{_e(item['coverage_status'])}</p>"
            "<p><a href='/'>返回 Dashboard</a></p></body></html>"
        )
