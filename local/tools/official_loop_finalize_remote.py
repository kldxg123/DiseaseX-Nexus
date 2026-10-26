from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_official_trusted_review_summary_prediction_demo"
OLD_LOOP = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=jdefault)


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def write_json(name: str, value: Any) -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    (REPORT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n", encoding="utf-8")


def db_url() -> Any:
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


def engine():
    e = create_engine(db_url(), hide_parameters=True, future=True)

    @event.listens_for(e, "connect")
    def _secure_session(dbapi_connection: object, _record: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        try:
            cursor.execute("SET search_path TO pg_catalog")
            cursor.execute("SET timezone TO 'UTC'")
        finally:
            cursor.close()

    return e


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def copy_prediction_artifacts() -> dict[str, Any]:
    files = [
        "region_week_dataset_manifest.json",
        "label_contract.md",
        "leakage_report.json",
        "split_manifest.json",
        "training_input_manifest.json",
        "model_card_demo.md",
        "model_metrics.json",
        "predictions_demo.csv",
        "reproducibility_report.json",
        "prediction_dashboard_demo_payload.json",
        "dashboard_prediction_demo_verify.json",
    ]
    copied = []
    for name in files:
        source = OLD_LOOP / name
        if source.exists():
            shutil.copy2(source, REPORT / name)
            copied.append({"file": name, "sha256": sha_file(REPORT / name)})
    return {"copied": copied, "missing": [name for name in files if not (REPORT / name).exists()]}


def db_reconciliation() -> dict[str, Any]:
    with engine().connect() as conn:
        rows = [
            dict(r)
            for r in conn.execute(
                text(
                    """
                    SELECT source_key, count(*)::int AS row_count, min(period_start) AS date_min, max(period_end) AS date_max
                    FROM epidemic_p1_12.trusted_dashboard_series_v1
                    WHERE country_iso3='COD' AND disease_slug='ebola-disease'
                    GROUP BY source_key
                    ORDER BY source_key
                    """
                )
            ).mappings()
        ]
        summary = dict(
            conn.execute(
                text(
                    """
                    SELECT
                      (SELECT count(*)::int FROM epidemic_p1_12.trusted_dashboard_series_v1 WHERE country_iso3='COD' AND disease_slug='ebola-disease') AS cod_ebola_trusted_rows,
                      (SELECT count(*)::int FROM epidemic_discovery.source_review_cases) AS review_case_total,
                      (SELECT count(*)::int FROM epidemic_discovery.source_review_decisions) AS review_decisions,
                      (SELECT count(*)::int FROM epidemic_discovery.source_promotion_runs) AS promotion_runs,
                      (SELECT count(*)::int FROM epidemic.dataset_snapshots) AS dataset_snapshots,
                      (SELECT count(*)::int FROM epidemic_p1_12.trusted_observation_lineage) AS trusted_lineage
                    """
                )
            ).mappings().one()
        )
    return {"summary": summary, "cod_ebola_by_source": rows}


def merge_dashboard_reconciliation(db: dict[str, Any]) -> dict[str, Any]:
    base = {}
    if (REPORT / "dashboard_reconciliation.json").exists():
        base = read_json(REPORT / "dashboard_reconciliation.json")
    don284 = read_json(REPORT / "dashboard_reconciliation.don284.json") if (REPORT / "dashboard_reconciliation.don284.json").exists() else {}
    prediction = read_json(REPORT / "dashboard_prediction_demo_verify.json") if (REPORT / "dashboard_prediction_demo_verify.json").exists() else {}
    merged = {
        "status": "DASHBOARD_RECONCILIATION_UPDATED",
        "formal_dashboard": base,
        "don284_dashboard": don284,
        "prediction_dashboard": prediction,
        "database_reconciliation": db,
        "trusted_candidate_demo_isolation": read_json(REPORT / "candidate_demo_isolation_test.json") if (REPORT / "candidate_demo_isolation_test.json").exists() else {},
    }
    write_json("dashboard_reconciliation.json", merged)
    return merged


def build_final_report(db: dict[str, Any], copied: dict[str, Any]) -> None:
    trusted_json = read_json(REPORT / "trusted_before_after.json")
    trusted_don284 = read_json(REPORT / "trusted_before_after.don284.json")
    review = read_json(REPORT / "review_summary_coverage.json")
    metrics = read_json(REPORT / "model_metrics.json")
    split = read_json(REPORT / "split_manifest.json")
    leakage = read_json(REPORT / "leakage_report.json")
    pred_verify = read_json(REPORT / "dashboard_prediction_demo_verify.json")
    matrix = read_json(REPORT / "source_coverage_matrix.json")
    text_report = f"""# Ebola official trusted expansion, Review summary, and prediction demo

Status: FORMAL_EBOLA_TRUSTED_EXPANDED_REVIEW_SUMMARY_RECONCILED_PREDICTION_SMOKE_COMPLETE

Generated at: {dt.datetime.now(dt.timezone.utc).isoformat()}

## Formal trusted data

- COD/Ebola Trusted rows: {trusted_json['before']['cod_ebola_trusted_rows']} -> {trusted_json['after']['cod_ebola_trusted_rows']} after DON398; then {trusted_don284['before']['cod_ebola_trusted_rows']} -> {trusted_don284['after']['cod_ebola_trusted_rows']} after DON284.
- DON398 disposition: FORMAL_AUTO_INGESTED on first run, FORMAL_DUPLICATE_NOOP on second run.
- DON284 disposition: FORMAL_AUTO_INGESTED on first run, FORMAL_DUPLICATE_NOOP on second run.
- Review approval, promotion, and Candidate Demo were not used for Trusted writes.
- Source coverage matrix rows: {len(matrix)}.

## Review summary

- Review cases: {review['review_case_total']}.
- SUMMARY_READY: {review['summary_ready_count']}.
- SOURCE_CONTENT_BLOCKED: {review['source_content_blocked_count']}.
- Missing summary records: {review['missing_summary_count']}.
- Candidate payload used for summaries: {review['candidate_payload_used_count']}.

## Prediction demo

- Real model status: BLOCKED_INSUFFICIENT_TEMPORAL_DATA_FOR_VALIDATED_MODEL.
- Smoke-test status: MINIMAL_PREDICTION_PIPELINE_SMOKE_TEST_COMPLETE.
- Train/test rows: {split.get('train_rows')}/{split.get('test_rows')}.
- Leakage violations: {leakage.get('leakage_violations')}.
- Dashboard prediction API: passed={pred_verify.get('passed')}, displayed predictions={pred_verify.get('api_prediction_count')}.
- Metrics are synthetic smoke-test engineering metrics only: {json.dumps(metrics.get('test'), ensure_ascii=False, sort_keys=True)}.

## Database reconciliation

- Current COD/Ebola Trusted rows: {db['summary']['cod_ebola_trusted_rows']}.
- Dataset snapshots: {db['summary']['dataset_snapshots']}.
- Trusted lineage rows: {db['summary']['trusted_lineage']}.
- Promotion runs: {db['summary']['promotion_runs']}.

## Artifact files copied into this delivery

{json.dumps(copied, ensure_ascii=False, indent=2, sort_keys=True)}
"""
    (REPORT / "FINAL_REPORT.md").write_text(text_report, encoding="utf-8")


def zip_report() -> dict[str, Any]:
    zip_path = PROJECT / "reports/ebola_official_trusted_review_summary_prediction_demo.zip"
    sha_path = PROJECT / "reports/ebola_official_trusted_review_summary_prediction_demo.zip.sha256"
    if zip_path.exists():
        zip_path.unlink()
    if sha_path.exists():
        sha_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(REPORT.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(REPORT.parent))
    digest = sha_file(zip_path)
    sha_path.write_text(f"{digest}  {zip_path.name}\n", encoding="utf-8")
    return {"zip": str(zip_path.relative_to(PROJECT)), "sha256": digest}


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    copied = copy_prediction_artifacts()
    db = db_reconciliation()
    merged_dashboard = merge_dashboard_reconciliation(db)
    build_final_report(db, copied)
    payload_manifest = {
        "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "report_dir": str(REPORT.relative_to(PROJECT)),
        "files": [
            {"path": str(path.relative_to(REPORT)), "sha256": sha_file(path), "bytes": path.stat().st_size}
            for path in sorted(REPORT.rglob("*"))
            if path.is_file()
        ],
    }
    write_json("payload_sha_manifest.json", payload_manifest)
    zip_info = zip_report()
    state = read_json(REPORT / "EXECUTION_STATE.json") if (REPORT / "EXECUTION_STATE.json").exists() else {}
    state.update(
        {
            "status": "FORMAL_EBOLA_TRUSTED_EXPANDED_REVIEW_SUMMARY_RECONCILED_PREDICTION_SMOKE_COMPLETE",
            "cod_ebola_trusted_current": db["summary"]["cod_ebola_trusted_rows"],
            "remaining": [],
            "review_summary_ready": read_json(REPORT / "review_summary_coverage.json")["summary_ready_count"],
            "review_source_content_blocked": read_json(REPORT / "review_summary_coverage.json")["source_content_blocked_count"],
            "prediction_status": "BLOCKED_INSUFFICIENT_TEMPORAL_DATA_FOR_VALIDATED_MODEL",
            "prediction_smoke": "MINIMAL_PREDICTION_PIPELINE_SMOKE_TEST_COMPLETE",
            "zip_sha256": zip_info["sha256"],
        }
    )
    write_json("EXECUTION_STATE.json", state)
    append_line = {
        "cycle_id": "official-loop-finalize",
        "status": state["status"],
        "db": db["summary"],
        "zip": zip_info,
        "dashboard_status": merged_dashboard["status"],
    }
    with (REPORT / "repair_cycle_ledger.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(canonical(append_line) + "\n")
    print(json.dumps({"status": state["status"], "cod_ebola_trusted": db["summary"]["cod_ebola_trusted_rows"], "zip_sha256": zip_info["sha256"]}, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
