"""本地演示用知识图谱预测指导服务。

以无数据库模式（engine=None）加载 kg_deploy_staging 内的知识图谱，
对外提供图谱元数据、子图查询与预测建模指导接口。
"""

from __future__ import annotations

import sys
from pathlib import Path

KG_SRC = Path(__file__).resolve().parent.parent / "kg_deploy_staging" / "src"
if str(KG_SRC) not in sys.path:
    sys.path.insert(0, str(KG_SRC))

import uvicorn
from fastapi import FastAPI, HTTPException, Query

from epidemic_core.forecast_model_kg import service

app = FastAPI(title="KG Forecast Guidance (local demo)")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "mode": "db-free"}


@app.get("/kg/metadata")
def kg_metadata() -> dict:
    return service.metadata_response()


@app.get("/kg/graph")
def kg_graph() -> dict:
    return service.full_graph_response()


@app.get("/kg/graph/{disease}")
def kg_disease_graph(disease: str) -> dict:
    try:
        return service.disease_graph_response(disease)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/kg/guidance")
def kg_guidance(
    disease: str = Query(..., description="病种，如 ebola / influenza / dengue"),
    country: str | None = Query(None, description="国家代码或名称，如 COD"),
    target: str | None = Query("cases_new"),
    horizon: int | None = Query(4),
) -> dict:
    try:
        return service.guidance_response(None, disease, country, target, horizon)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8002, log_level="info")
