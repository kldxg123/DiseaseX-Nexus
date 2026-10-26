from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from epidemic_core.forecast_model_kg.dashboard_app import install_routes


class EngineStub:
    pass


def authorized(_: Request) -> bool:
    return True


def unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "UNAUTHORIZED"}, status_code=401)


def json_response(payload: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status)


app = FastAPI()
install_routes(app, EngineStub(), authorized, unauthorized, json_response)  # type: ignore[arg-type]
client = TestClient(app)

response = client.get("/api/v1/forecast-model-knowledge-graph/benchmarks/sgp-dengue")
assert response.status_code == 200, response.text
payload = response.json()
assert payload["metrics"]["test"]["agreement_pct"] >= 70.0
assert payload["is_fresh_heldout"] is False
assert payload["publication_state"] == "SHADOW_ONLY_NOT_PROMOTED"

page = client.get("/forecast-model-knowledge-graph")
assert page.status_code == 200
assert "第三条 70% 以上预测链路：新加坡登革热" in page.text
assert "kg-paper-benchmark-e" in page.text

print("AUTHENTICATED_ROUTE_SMOKE=PASS")
print(f"TRACK_E_AGREEMENT_PCT={payload['metrics']['test']['agreement_pct']:.2f}")
print(f"TRACK_E_PAYLOAD_SHA256={payload['payload_sha256']}")
