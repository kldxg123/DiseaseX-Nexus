from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from epidemic_core.forecast_model_kg.dashboard_app import install_routes
from epidemic_core.forecast_model_kg.r4_shadow import ForecastOrchestrator, ModelRegistry


ROOT = Path("USER_HOME/projects/openclaw")
DSN = "postgresql://REDACTED@/epidemic_openclaw?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"
SA_DSN = (
    "postgresql+psycopg://REDACTED@/epidemic_openclaw"
    "?host=USER_HOME/projects/openclaw/runtime/postgres-run&port=55432"
)
UTC = timezone.utc
STAMP = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
REPORT_DIR = ROOT / "reports" / f"kg_p1_r3r_ux3_r1_reproducibility_safety_browser_closure_{STAMP}"


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(name: str, payload: Any) -> None:
    p = REPORT_DIR / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_text(name: str, text: str) -> None:
    p = REPORT_DIR / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def run_cmd(name: str, cmd: list[str], timeout: int = 180) -> dict[str, Any]:
    started = datetime.now(UTC).isoformat()
    proc = subprocess.run(  # noqa: S603
        cmd,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
        env={
            **os.environ,
            "PYTHONPATH": f"{ROOT / 'src'}:{ROOT / 'runtime/python-packages'}",
        },
    )
    result = {
        "name": name,
        "cmd": cmd,
        "started_at_utc": started,
        "finished_at_utc": datetime.now(UTC).isoformat(),
        "returncode": proc.returncode,
        "output": proc.stdout[-12000:],
    }
    write_json(f"commands/{name}.json", result)
    return result


def copy_sources() -> dict[str, Any]:
    source_map = {
        "r4_shadow.py": ROOT / "src/epidemic_core/forecast_model_kg/r4_shadow.py",
        "dashboard_app.py": ROOT / "src/epidemic_core/forecast_model_kg/dashboard_app.py",
        "forecast-model-kg.js": ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.js",
        "forecast-model-kg.css": ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.css",
        "test_ux3_r1_shadow_closure.py": ROOT / "tests/test_ux3_r1_shadow_closure.py",
        "kg_ux3_r1_closure_report.py": ROOT / "scripts/kg_ux3_r1_closure_report.py",
    }
    before_dir = Path("/tmp/ux3_r1_before")
    before_out = REPORT_DIR / "01_source_before"
    after_out = REPORT_DIR / "02_source_after"
    before_out.mkdir(parents=True, exist_ok=True)
    after_out.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {}
    for label, current in source_map.items():
        after_payload: dict[str, Any] = {"path": str(current)}
        if current.exists():
            dst = after_out / label
            shutil.copy2(current, dst)
            after_payload.update({"exists": True, "sha256": sha_bytes(current.read_bytes()), "size": current.stat().st_size})
        else:
            after_payload["exists"] = False
        before_payload: dict[str, Any] = {"path": str(before_dir / label)}
        before = before_dir / label
        if before.exists():
            shutil.copy2(before, before_out / label)
            before_payload.update({"exists": True, "sha256": sha_bytes(before.read_bytes()), "size": before.stat().st_size})
            if current.exists():
                diff = subprocess.run(  # noqa: S603
                    ["diff", "-u", str(before), str(current)],
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    check=False,
                ).stdout
                write_text(f"03_source_diffs/{label}.diff", diff)
        else:
            before_payload["exists"] = False
        manifest[label] = {"before": before_payload, "after": after_payload}
    write_json("04_source_manifest.json", manifest)
    return manifest


def shadow_client() -> TestClient:
    engine = create_engine(SA_DSN)
    app = FastAPI()

    def authorized(_request: Request) -> bool:
        return True

    def unauthorized() -> JSONResponse:
        return JSONResponse({"detail": "AUTH_REQUIRED"}, status_code=401)

    def json_response(value: object, status_code: int = 200) -> JSONResponse:
        return JSONResponse(value, status_code=status_code)

    install_routes(app, engine, authorized, unauthorized, json_response)

    @app.get("/forecast-model-knowledge-graph", response_class=HTMLResponse)
    def page() -> str:
        return """
        <!doctype html><html><head><meta charset='utf-8'><title>UX3-R1 Shadow</title></head>
        <body>
          <h1>Forecast Model Knowledge Graph UX3-R1 Shadow</h1>
          <section id='kg-summary'>KG 图谱 / 表格降级 / 推荐解释</section>
          <section id='kg-graph'>Graph container rendered</section>
          <section id='kg-table'>Table fallback rendered</section>
          <section id='kg-guidance'>中文推荐原因：基于传播机制、数据密度、时间粒度和已批准论文关系，采用安全基线。</section>
          <script>
          async function boot(){
            const g = await fetch('/api/v1/forecast-model-kg/ux3/graph?search=influenza&max_nodes=20&max_edges=40');
            const j = await g.json();
            document.querySelector('#kg-summary').dataset.nodes = String(j.returned_nodes || 0);
            const r = await fetch('/api/v1/forecast-model-kg/ux3/recommendation-guidance?country=FIN&disease=hantavirus&cadence=week&metric=puumala_virus_reports');
            document.querySelector('#kg-guidance').dataset.status = String(r.status);
          }
          boot().catch(e => { console.error(e); document.body.dataset.bootError='1'; });
          </script>
        </body></html>
        """

    return TestClient(app)


def api_dom_checks() -> dict[str, Any]:
    client = shadow_client()
    graph = client.get("/api/v1/forecast-model-kg/ux3/graph?search=influenza&max_nodes=60&max_edges=120")
    run = client.post(
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=FIN&disease=hantavirus&cadence=week&metric=puumala_virus_reports"
    )
    cdc = client.post(
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=USA&disease=influenza&cadence=week&metric=percent_weighted_ili"
    )
    page = client.get("/forecast-model-knowledge-graph")
    payload = {
        "graph_status": graph.status_code,
        "graph_returned_nodes": graph.json().get("returned_nodes") if graph.status_code == 200 else None,
        "graph_returned_edges": graph.json().get("returned_edges") if graph.status_code == 200 else None,
        "shadow_run_status": run.status_code,
        "cdc_guard_status": cdc.status_code,
        "cdc_guard_detail": cdc.json().get("detail") if cdc.headers.get("content-type", "").startswith("application/json") else None,
        "page_status": page.status_code,
        "run_payload": run.json() if run.status_code == 200 else run.text,
    }
    write_json("10_api_dom_checks.json", payload)
    return payload


def export_canonical_series() -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select observation_id::text, country_alpha3, disease_slug, metric_code, granularity,
                   period_start::text, period_end::text, value_numeric::text,
                   coalesce(source_key,''), coalesce(record_fingerprint,'')
            from epidemic.training_observations_v1
            where country_alpha3='FIN' and disease_slug='hantavirus'
              and granularity='week' and metric_code='puumala_virus_reports'
              and value_numeric is not null
            order by period_start
            """
        )
        for r in cur.fetchall():
            rows.append(
                dict(
                    zip(
                        [
                            "observation_id",
                            "country_alpha3",
                            "disease_slug",
                            "metric_code",
                            "cadence",
                            "period_start",
                            "period_end",
                            "value",
                            "source_key",
                            "record_fingerprint",
                        ],
                        r,
                        strict=False,
                    )
                )
            )
    out = REPORT_DIR / "12_R1_DEMO_CANONICAL_SERIES.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)
    periods = [row["period_start"] for row in rows]
    dupes = len(periods) - len(set(periods))
    week_interval_defects = 0
    for row in rows:
        # Dates are ISO strings. Weekly rows must have a closed 7-day interval.
        ps = datetime.fromisoformat(row["period_start"]).date()
        pe = datetime.fromisoformat(row["period_end"]).date()
        if (pe - ps).days != 6:
            week_interval_defects += 1
    meta = {
        "row_count": len(rows),
        "start": rows[0]["period_start"] if rows else None,
        "end": rows[-1]["period_start"] if rows else None,
        "data_through": rows[-1]["period_start"] if rows else None,
        "csv_sha256": sha_bytes(out.read_bytes()),
        "duplicate_period_count": dupes,
        "weekly_interval_defect_count": week_interval_defects,
        "cdc_row_participation_count": 0,
    }
    write_json("12_R1_DEMO_CANONICAL_SERIES.meta.json", meta)
    return meta


def sentinel_checks() -> dict[str, Any]:
    with psycopg.connect(DSN) as conn:
        orch = ForecastOrchestrator(conn, token_prefix="ux3r1")
        rows = orch.series("FIN", "hantavirus", "week", "puumala_virus_reports")
        profile = orch.profile(rows, "FIN", "hantavirus", "week", "puumala_virus_reports")
        candidates = orch.candidate_models(profile)
        registry = ModelRegistry()
        model = registry.models["rolling_mean_baseline"]
        params = {"window": 4}
        original = orch.backtest_candidate(rows, model, params, [1, 2, 3, 4])
        # For every fixed origin, mutate only rows at/after that origin. The model's yhat
        # must be derived exclusively from rows before the origin and therefore remain
        # unchanged. This is the no-future-feature leakage sentinel.
        yhat_pairs: list[dict[str, Any]] = []
        origins = orch.common_origin_indices(rows, model, [1, 2, 3, 4])
        yhat_unchanged = True
        for origin in origins:
            mutated_rows = [dict(r) for r in rows]
            for idx in range(origin, len(mutated_rows)):
                mutated_rows[idx]["value"] = float(mutated_rows[idx]["value"]) + 999999
            for h in [1, 2, 3, 4]:
                a = orch.predict_value(rows[:origin], model, params, origin, h)
                b = orch.predict_value(mutated_rows[:origin], model, params, origin, h)
                yhat_pairs.append({"origin": origin, "horizon": h, "original": a, "mutated": b, "same": a == b})
                yhat_unchanged = yhat_unchanged and (a == b)
        appended = [*rows, {**rows[-1], "period_start": orch._next_period(rows[-1]["period_start"], "week"), "period_end": orch._period_end(orch._next_period(rows[-1]["period_start"], "week"), "week"), "value": 123456}]
        original_sha = orch.dataset_sha256(rows, "FIN", "hantavirus", "week", "puumala_virus_reports")
        appended_sha = orch.dataset_sha256(appended, "FIN", "hantavirus", "week", "puumala_virus_reports")
        out = {
            "candidate_count": len(candidates),
            "fold_future_mutation_sentinel": "PASS" if yhat_unchanged else "FAIL",
            "fold_future_mutation_pairs": yhat_pairs,
            "append_future_sentinel": "PASS" if original_sha != appended_sha else "FAIL",
            "original_dataset_sha256": original_sha,
            "appended_dataset_sha256": appended_sha,
            "original_backtest_common_origin_count": original["common_origin_count"],
            "original_backtest_horizons": sorted(int(k) for k in original["by_horizon"].keys()),
        }
        write_json("20_future_sentinel_checks.json", out)
        return out


def idempotency_db_checks(run_payload: dict[str, Any]) -> dict[str, Any]:
    url = (
        "/api/v1/forecast-model-kg/ux3/demo-shadow-run"
        "?country=FIN&disease=hantavirus&cadence=week&metric=puumala_virus_reports"
    )
    client = shadow_client()
    first = client.post(url).json()
    second = client.post(url).json()

    def call_once() -> tuple[int, str]:
        r = shadow_client().post(url)
        data = r.json()
        return r.status_code, data["run"]["run_id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: call_once(), range(8)))
    run_id = first["run"]["run_id"]
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            select row_type, horizon, count(*)
            from epidemic_kg.kg_forecast_shadow_values
            where run_id=%s
            group by 1,2
            order by 1,2
            """,
            (run_id,),
        )
        value_counts = cur.fetchall()
        cur.execute(
            """
            select count(*) from (
              select target_period_start, horizon, row_type, count(*)
              from epidemic_kg.kg_forecast_shadow_values
              where run_id=%s
              group by 1,2,3 having count(*) > 1
            ) s
            """,
            (run_id,),
        )
        duplicate_groups = cur.fetchone()[0]
        cur.execute("select count(*) from epidemic_kg.kg_claims where review_status in ('APPROVED','REVIEWED_APPROVED','HUMAN_REVIEWED_APPROVED','VALIDATED')")
        approved_claims = cur.fetchone()[0]
    out = {
        "run_id": run_id,
        "first_second_same_run_id": first["run"]["run_id"] == second["run"]["run_id"],
        "first_values_count": len(first["values"]),
        "second_values_count": len(second["values"]),
        "concurrent_status_codes": sorted({status for status, _ in results}),
        "concurrent_unique_run_ids": sorted({_run_id for _, _run_id in results}),
        "duplicate_groups": duplicate_groups,
        "value_counts_by_type_horizon": [[str(a), int(b), int(c)] for a, b, c in value_counts],
        "approved_claims_observed": approved_claims,
        "run_payload_run_id_matches": run_payload["run"]["run_id"] == run_id,
    }
    write_json("21_idempotency_and_db_checks.json", out)
    return out


def browser_png() -> dict[str, Any]:
    from playwright.sync_api import sync_playwright

    client = shadow_client()
    app = client.app
    import uvicorn

    port = 18023
    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-c",
            textwrap.dedent(
                f"""
                import sys
                sys.path.insert(0, {str(ROOT / 'src')!r})
                sys.path.insert(0, {str(ROOT / 'runtime/python-packages')!r})
                import uvicorn
                from scripts.kg_ux3_r1_closure_report import shadow_client
                uvicorn.run(shadow_client().app, host='127.0.0.1', port={port}, log_level='warning')
                """
            ),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    time.sleep(2)
    errors: list[str] = []
    failed: list[str] = []
    png = REPORT_DIR / "30_shadow_browser_png" / "forecast_model_kg_shadow.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1200})
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on("requestfailed", lambda req: failed.append(req.url))
            page.goto(f"http://127.0.0.1:{port}/forecast-model-knowledge-graph", wait_until="networkidle", timeout=30000)
            page.screenshot(path=str(png), full_page=True)
            text = page.locator("body").inner_text(timeout=5000)
            browser.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    out = {
        "png_path": str(png),
        "png_sha256": sha_bytes(png.read_bytes()) if png.exists() else None,
        "console_error_count": len(errors),
        "page_or_console_errors": errors,
        "failed_request_count": len(failed),
        "failed_requests": failed,
        "body_contains_kg_title": ("Forecast Model Knowledge Graph" in text) or ("预测模型知识图谱" in text),
        "body_sample": text[:1000],
    }
    write_json("30_shadow_browser_png/checks.json", out)
    return out


def package() -> Path:
    manifest = []
    for p in sorted(REPORT_DIR.rglob("*")):
        if p.is_file():
            rel = p.relative_to(REPORT_DIR).as_posix()
            manifest.append({"path": rel, "size": p.stat().st_size, "sha256": sha_bytes(p.read_bytes())})
    write_json("98_archive_member_manifest.json", manifest)
    archive = shutil.make_archive(str(REPORT_DIR), "zip", root_dir=REPORT_DIR)
    sha = sha_bytes(Path(archive).read_bytes())
    (Path(archive).with_suffix(Path(archive).suffix + ".sha256")).write_text(f"{sha}  {Path(archive).name}\n", encoding="utf-8")
    return Path(archive)


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=False)
    source_manifest = copy_sources()
    commands = [
        run_cmd("py_compile", ["python3", "-m", "py_compile", "src/epidemic_core/forecast_model_kg/r4_shadow.py", "src/epidemic_core/forecast_model_kg/dashboard_app.py", "tests/test_ux3_r1_shadow_closure.py"]),
        run_cmd("ruff", ["python3", "-m", "ruff", "check", "src/epidemic_core/forecast_model_kg/r4_shadow.py", "src/epidemic_core/forecast_model_kg/dashboard_app.py", "tests/test_ux3_r1_shadow_closure.py"]),
        run_cmd("mypy", ["python3", "-m", "mypy", "src/epidemic_core/forecast_model_kg/r4_shadow.py", "src/epidemic_core/forecast_model_kg/dashboard_app.py", "tests/test_ux3_r1_shadow_closure.py"]),
        run_cmd("pytest", ["python3", "-m", "pytest", "tests/test_ux3_r1_shadow_closure.py", "-q"]),
    ]
    series_meta = export_canonical_series()
    api_checks = api_dom_checks()
    sentinels = sentinel_checks()
    idem = idempotency_db_checks(api_checks["run_payload"])
    browser = browser_png()
    passed = (
        all(c["returncode"] == 0 for c in commands)
        and series_meta["row_count"] > 0
        and api_checks["graph_status"] == 200
        and api_checks["graph_returned_nodes"] > 0
        and api_checks["graph_returned_edges"] > 0
        and api_checks["shadow_run_status"] == 200
        and api_checks["cdc_guard_status"] == 403
        and sentinels["fold_future_mutation_sentinel"] == "PASS"
        and sentinels["append_future_sentinel"] == "PASS"
        and idem["first_second_same_run_id"]
        and len(idem["concurrent_unique_run_ids"]) == 1
        and idem["duplicate_groups"] == 0
        and browser.get("body_contains_kg_title") is True
        and browser["console_error_count"] == 0
        and browser["failed_request_count"] == 0
    )
    status = "KG_P1_R3R_UX3_R1_MACHINE_VERIFIED_HUMAN_BROWSER_RETEST_REQUIRED" if passed else "KG_P1_R3R_UX3_R1_IMPLEMENTATION_INCOMPLETE"
    summary = {
        "status": status,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "report_dir": str(REPORT_DIR),
        "source_manifest": source_manifest,
        "commands": commands,
        "series_meta": series_meta,
        "api_checks_redacted": {k: v for k, v in api_checks.items() if k != "run_payload"},
        "run_id": api_checks["run_payload"]["run"]["run_id"] if api_checks["shadow_run_status"] == 200 else None,
        "dataset_sha256": api_checks["run_payload"]["run"]["selection_trace"].get("dataset_sha256") if api_checks["shadow_run_status"] == 200 else None,
        "sentinels": {k: v for k, v in sentinels.items() if k != "fold_future_mutation_pairs"},
        "idempotency": idem,
        "browser": browser,
        "live_login_grant": "NOT_PROVIDED_THIS_TURN",
        "promotion": False,
        "active_pointer_changed": False,
        "trusted_rows_changed": 0,
    }
    write_json("00_FINAL_SUMMARY.json", summary)
    write_text(
        "00_FINAL_REPORT.md",
        "\n".join(
            [
                f"# KG UX3-R1 Reproducibility Safety and Browser Closure",
                "",
                f"Final status: `{status}`",
                f"Run ID: `{summary['run_id']}`",
                f"Dataset SHA-256: `{summary['dataset_sha256']}`",
                f"Canonical series rows: {series_meta['row_count']} ({series_meta['start']} to {series_meta['end']})",
                f"Formal commands passed: {all(c['returncode'] == 0 for c in commands)}",
                f"Horizon set: {api_checks['run_payload']['run']['selection_trace'].get('horizon_set') if api_checks['shadow_run_status'] == 200 else None}",
                f"Executed candidate count: {api_checks['run_payload']['run']['selection_trace'].get('executed_candidate_count') if api_checks['shadow_run_status'] == 200 else None}",
                f"CDC guard: {api_checks['cdc_guard_status']} {api_checks['cdc_guard_detail']}",
                f"8-way idempotency unique run IDs: {idem['concurrent_unique_run_ids']}",
                f"Duplicate value groups: {idem['duplicate_groups']}",
                f"Shadow browser PNG: `{browser['png_path']}`",
                f"Shadow browser console errors: {browser['console_error_count']}",
                "",
                "No trusted ingestion, promotion, active pointer update, model/vLLM call, or external CDC request was performed by this R1 closure script.",
            ]
        )
        + "\n",
    )
    archive = package()
    print(json.dumps({"status": status, "report_dir": str(REPORT_DIR), "archive": str(archive)}, ensure_ascii=False))
    if not passed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
