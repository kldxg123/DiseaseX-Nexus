from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from epidemic_core.forecast_model_kg.dashboard_app import install_routes

DSN = "postgresql+psycopg://REDACTED@/epidemic_openclaw?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"


def main() -> int:
    engine = create_engine(DSN)
    app = FastAPI()
    install_routes(app, engine, lambda _request: True, lambda: JSONResponse({"detail": "AUTH_REQUIRED"}, status_code=401), lambda v, status_code=200: JSONResponse(v, status_code=status_code))
    c = TestClient(app)
    out = {}
    for url in [
        "/api/v1/forecast-model-kg/ux3/graph?search=influenza&max_nodes=30&max_edges=60",
        "/api/v1/forecast-model-kg/r4/model-registry",
    ]:
        r = c.get(url)
        out[url] = {"status": r.status_code, "keys": sorted(r.json().keys()) if r.headers.get("content-type", "").startswith("application/json") else []}
        assert r.status_code == 200, r.text[:300]
    r = c.post("/api/v1/forecast-model-kg/ux3/demo-shadow-run?country=FIN&disease=hantavirus&cadence=week&metric=puumala_virus_reports")
    out["demo_shadow"] = {"status": r.status_code}
    assert r.status_code == 200, r.text[:500]
    d = r.json()
    out["demo_shadow"].update({
        "run_id": d["run"]["run_id"],
        "mode": d["run"]["mode"],
        "status": d["run"]["status"],
        "model_name": d["run"]["model_name"],
        "promotion_eligible": d["run"]["promotion_eligible"],
        "active_pointer_changed": d["run"]["active_pointer_changed"],
        "trusted_rows_changed": d["run"]["trusted_rows_changed"],
        "value_count": len(d["values"]),
        "executed_candidate_count": d["run"]["selection_trace"].get("executed_candidate_count"),
        "reason_code": d["run"]["selection_trace"].get("baseline_guard", {}).get("reason_code"),
    })
    assert d["run"]["mode"] == "SHADOW"
    assert d["non_promotion_assertions"]["mode_equivalent"] == "DEMO_SHADOW"
    assert d["run"]["promotion_eligible"] is False
    assert d["run"]["active_pointer_changed"] is False
    assert d["run"]["trusted_rows_changed"] == 0
    assert len(d["values"]) > 0
    assert (d["run"]["selection_trace"].get("executed_candidate_count") or 0) >= 2
    # Idempotency: same request must return same run_id and not duplicate value rows.
    r2 = c.post("/api/v1/forecast-model-kg/ux3/demo-shadow-run?country=FIN&disease=hantavirus&cadence=week&metric=puumala_virus_reports")
    assert r2.status_code == 200, r2.text[:500]
    d2 = r2.json()
    assert d2["run"]["run_id"] == d["run"]["run_id"]
    assert len(d2["values"]) == len(d["values"])
    out["idempotency"] = {"same_run_id": True, "same_value_count": True}
    # Unsafe CDC R4F route must fail closed.
    r3 = c.post("/api/v1/forecast-model-kg/ux3/demo-shadow-run?country=USA&disease=influenza&cadence=week&metric=percent_weighted_ili")
    out["cdc_guard"] = {"status": r3.status_code, "body": r3.json()}
    assert r3.status_code == 403
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                select count(*) from epidemic_kg.kg_forecast_shadow_runs
                where idempotency_key like 'ux3:%' and mode='SHADOW'
                  and promotion_eligible=false and active_pointer_changed=false and trusted_rows_changed=0
                """
            )
        ).scalar_one()
        out["ux3_safe_run_count"] = rows
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
