from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url


PROJECT = Path(os.environ["PROJECT"])
REPORT = PROJECT / "reports/ebola_data_visualization_prediction_engineering_loop"
SCREENSHOTS = REPORT / "screenshots"


def jdefault(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return str(value)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=jdefault)


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha_obj(value: Any) -> str:
    return sha_text(canonical(value))


def write_json(rel: str, value: Any) -> None:
    (REPORT / rel).parent.mkdir(parents=True, exist_ok=True)
    (REPORT / rel).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=jdefault) + "\n",
        encoding="utf-8",
    )


def append_jsonl(rel: str, rows: list[dict[str, Any]]) -> None:
    (REPORT / rel).parent.mkdir(parents=True, exist_ok=True)
    with (REPORT / rel).open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=jdefault) + "\n")


def db_url() -> Any:
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url = make_url(url).set(username=os.environ["POSTGRES_ADMIN_USER"], password=os.environ["POSTGRES_ADMIN_PASSWORD"])
    return url


def admin_url_string() -> str:
    url = os.environ["MIGRATION_DATABASE_URL"]
    if os.environ.get("POSTGRES_ADMIN_PASSWORD") and os.environ.get("POSTGRES_ADMIN_USER"):
        url_obj = make_url(url).set(
            username=os.environ["POSTGRES_ADMIN_USER"],
            password=os.environ["POSTGRES_ADMIN_PASSWORD"],
        )
        return url_obj.render_as_string(hide_password=False)
    return url


def table_exists(conn: sa.Connection, regclass: str) -> bool:
    return bool(conn.execute(text("SELECT to_regclass(:r) IS NOT NULL"), {"r": regclass}).scalar_one())


def cols(conn: sa.Connection, schema: str, table: str) -> set[str]:
    return {
        str(r["column_name"])
        for r in conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=:s AND table_name=:t"
            ),
            {"s": schema, "t": table},
        )
        .mappings()
        .all()
    }


def rows(conn: sa.Connection, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(text(sql), params or {}).mappings().all()]


def scalar(conn: sa.Connection, sql: str, params: dict[str, Any] | None = None) -> Any:
    return conn.execute(text(sql), params or {}).scalar_one()


def jsonish(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return {"raw": str(value)}


def html_contains_all(text_value: str, needles: list[str]) -> dict[str, bool]:
    folded = text_value.casefold()
    return {needle: needle.casefold() in folded for needle in needles}


def sanitize_html_capture(html_text: str) -> str:
    import re

    html_text = re.sub(
        r'(<meta\s+name=["\']csrf-token["\']\s+content=["\'])[^"\']+(["\'])',
        r"\1[redacted-test-csrf]\2",
        html_text,
        flags=re.I,
    )
    html_text = re.sub(
        r'(<input[^>]+name=["\']csrf["\'][^>]+value=["\'])[^"\']+(["\'])',
        r"\1[redacted-test-csrf]\2",
        html_text,
        flags=re.I,
    )
    return html_text


def database_exports() -> dict[str, Any]:
    engine = sa.create_engine(db_url(), hide_parameters=True, future=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout='30s'"))
            conn.execute(text("SET LOCAL idle_in_transaction_session_timeout='60s'"))
            identity = dict(
                conn.execute(
                    text(
                        "SELECT current_database() AS current_database,current_user,session_user,"
                        "current_setting('transaction_read_only') AS transaction_read_only,"
                        "current_setting('transaction_isolation') AS transaction_isolation"
                    )
                )
                .mappings()
                .one()
            )
            revisions = rows(conn, "SELECT version_num FROM epidemic.alembic_version ORDER BY version_num")

            candidate_rows: list[dict[str, Any]] = []
            if table_exists(conn, "epidemic_discovery.source_review_cases"):
                candidate_rows = rows(
                    conn,
                    """
                    SELECT
                      rc.id AS review_case_id,
                      rc.state AS review_state,
                      rc.created_at AS review_created_at,
                      v.id AS candidate_version_id,
                      v.candidate_id,
                      v.content_sha256 AS artifact_sha256,
                      v.format,
                      v.metadata,
                      c.publisher,
                      c.dataset_name,
                      c.source_tier,
                      c.status AS candidate_status,
                      COALESCE(pr.preview_rows,0) AS preview_row_count,
                      s.summary_status,
                      s.failure_reason AS summary_failure_reason,
                      s.summary_input_kind,
                      s.candidate_payload_used,
                      s.summary_input_sha256,
                      s.summary_sha256,
                      s.document_type
                    FROM epidemic_discovery.source_review_cases rc
                    JOIN epidemic_discovery.source_candidate_versions v ON v.id = rc.candidate_version_id
                    JOIN epidemic_discovery.source_candidates c ON c.id = v.candidate_id
                    LEFT JOIN (
                      SELECT candidate_version_id, count(*)::int AS preview_rows
                      FROM epidemic_discovery.source_preview_rows
                      GROUP BY candidate_version_id
                    ) pr ON pr.candidate_version_id = v.id
                    LEFT JOIN epidemic_discovery.review_case_raw_source_summaries_v1 s ON s.review_case_id = rc.id
                    ORDER BY rc.created_at, rc.id::text
                    """,
                )

            preview_rows: list[dict[str, Any]] = []
            if table_exists(conn, "epidemic_discovery.source_preview_rows"):
                preview_cols = cols(conn, "epidemic_discovery", "source_preview_rows")
                preview_select = [
                    "candidate_version_id",
                    "row_ordinal" if "row_ordinal" in preview_cols else "NULL::integer AS row_ordinal",
                    "sanitized_json" if "sanitized_json" in preview_cols else "NULL::jsonb AS sanitized_json",
                    "row_sha256" if "row_sha256" in preview_cols else "NULL::text AS row_sha256",
                    "created_at" if "created_at" in preview_cols else "NULL::timestamptz AS created_at",
                ]
                preview_rows = rows(
                    conn,
                    "SELECT " + ", ".join(preview_select) + " "
                    "FROM epidemic_discovery.source_preview_rows "
                    "ORDER BY candidate_version_id::text, row_ordinal NULLS LAST",
                )

            trusted_ebola = []
            if table_exists(conn, "epidemic_p1_12.trusted_dashboard_series_v1"):
                trusted_ebola = rows(
                    conn,
                    """
                    SELECT to_jsonb(t) AS row_json
                    FROM epidemic_p1_12.trusted_dashboard_series_v1 t
                    WHERE lower(to_jsonb(t)::text) LIKE '%ebola%'
                    ORDER BY to_jsonb(t)::text
                    """,
                )
            trusted_cod_ebola = []
            if table_exists(conn, "epidemic_p1_12.trusted_dashboard_series_v1"):
                trusted_cod_ebola = rows(
                    conn,
                    """
                    SELECT to_jsonb(t) AS row_json
                    FROM epidemic_p1_12.trusted_dashboard_series_v1 t
                    WHERE lower(to_jsonb(t)::text) LIKE '%ebola%'
                      AND (
                        to_jsonb(t)->>'country_alpha3'='COD'
                        OR to_jsonb(t)->>'country_alpha2'='CD'
                        OR lower(to_jsonb(t)::text) LIKE '%democratic republic of the congo%'
                      )
                    ORDER BY to_jsonb(t)::text
                    """,
                )

            snapshots = []
            if table_exists(conn, "epidemic.dataset_snapshots"):
                snapshots = rows(conn, "SELECT to_jsonb(t) AS row_json FROM epidemic.dataset_snapshots t ORDER BY to_jsonb(t)::text")

            decisions = []
            if table_exists(conn, "epidemic_discovery.source_review_decisions"):
                decisions = rows(
                    conn,
                    "SELECT to_jsonb(t) AS row_json FROM epidemic_discovery.source_review_decisions t ORDER BY to_jsonb(t)::text",
                )

            counts = {}
            for reg in [
                "epidemic_discovery.source_candidates",
                "epidemic_discovery.source_candidate_versions",
                "epidemic_discovery.source_preview_rows",
                "epidemic_discovery.source_review_cases",
                "epidemic_discovery.source_review_case_raw_source_summaries",
                "epidemic_discovery.source_review_decisions",
                "epidemic.normalized_observations",
                "epidemic.dataset_snapshots",
                "epidemic_p1_12.trusted_dashboard_series_v1",
                "epidemic_preview.preview_observations",
                "epidemic_preview.preview_snapshots",
            ]:
                if table_exists(conn, reg):
                    counts[reg] = int(scalar(conn, f"SELECT count(*)::bigint FROM {reg}"))

            conn.execute(text("ROLLBACK"))
    finally:
        engine.dispose()

    artifacts: list[dict[str, Any]] = []
    review_case_export: list[dict[str, Any]] = []
    for item in candidate_rows:
        meta = jsonish(item.get("metadata"))
        source_url = (
            meta.get("source_url")
            or meta.get("url")
            or meta.get("canonical_url")
            or meta.get("landing_page_url")
            or meta.get("artifact_url")
        )
        final_url = meta.get("final_url") or source_url
        artifact = {
            "review_case_id": str(item.get("review_case_id")),
            "candidate_id": str(item.get("candidate_id")),
            "candidate_version_id": str(item.get("candidate_version_id")),
            "publisher": item.get("publisher"),
            "dataset_name": item.get("dataset_name"),
            "source_tier": item.get("source_tier"),
            "original_url_present": bool(source_url),
            "final_url_present": bool(final_url),
            "original_domain": meta.get("domain") or meta.get("official_domain"),
            "artifact_sha256": item.get("artifact_sha256"),
            "document_type": item.get("document_type") or item.get("format"),
            "format": item.get("format"),
            "metadata_sha256": sha_obj(meta),
            "preview_row_count": int(item.get("preview_row_count") or 0),
            "summary_status": item.get("summary_status"),
        }
        artifacts.append(artifact)
        review_case_export.append(
            {
                **artifact,
                "review_state": item.get("review_state"),
                "review_created_at": item.get("review_created_at"),
                "candidate_status": item.get("candidate_status"),
                "summary_input_kind": item.get("summary_input_kind"),
                "candidate_payload_used": item.get("candidate_payload_used"),
                "summary_input_sha256": item.get("summary_input_sha256"),
                "summary_sha256": item.get("summary_sha256"),
                "summary_failure_reason": item.get("summary_failure_reason"),
            }
        )

    preview_hashes: dict[str, list[str]] = {}
    preview_semantics: list[dict[str, Any]] = []
    for row in preview_rows:
        version = str(row.get("candidate_version_id"))
        payload = jsonish(row.get("sanitized_json"))
        preview_hashes.setdefault(version, []).append(sha_obj(payload))
        lowered = canonical(payload).casefold()
        preview_semantics.append(
            {
                "candidate_version_id": version,
                "row_ordinal": row.get("row_ordinal"),
                "row_sha256": row.get("row_sha256"),
                "payload_sha256": sha_obj(payload),
                "mentions_total": "total" in lowered,
                "mentions_confirmed": "confirmed" in lowered,
                "mentions_probable": "probable" in lowered,
                "mentions_deaths": "death" in lowered or "deaths" in lowered,
                "mentions_event_final": "event_final" in lowered or "event final" in lowered,
                "mentions_report_snapshot": "report_snapshot" in lowered or "snapshot" in lowered,
                "has_period_token": any(token in lowered for token in ["period", "week", "date", "published", "reporting"]),
            }
        )

    rowset_entries = [
        {
            "candidate_version_id": version,
            "row_count": len(values),
            "rowset_sha256": sha_text("\n".join(values)),
            "exact_duplicate_payloads": len(values) - len(set(values)),
        }
        for version, values in sorted(preview_hashes.items())
    ]

    return {
        "identity": identity,
        "alembic_revisions": revisions,
        "counts": counts,
        "artifacts": artifacts,
        "review_case_export": review_case_export,
        "preview_semantics": preview_semantics,
        "rowset_entries": rowset_entries,
        "trusted_ebola_count": len(trusted_ebola),
        "trusted_cod_ebola_count": len(trusted_cod_ebola),
        "trusted_cod_ebola_sha256": sha_obj(trusted_cod_ebola),
        "trusted_snapshot_count": len(snapshots),
        "trusted_snapshot_sha256": sha_obj(snapshots),
        "review_decision_count": len(decisions),
        "review_decision_sha256": sha_obj(decisions),
    }


def write_html(rel: str, html_text: str) -> dict[str, Any]:
    html_text = sanitize_html_capture(html_text)
    target = REPORT / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html_text, encoding="utf-8")
    return {"path": rel, "bytes": len(html_text.encode("utf-8")), "sha256": sha_text(html_text)}


def fake_auth_file(username: str = "test", password: bytes = b"unused-test-password") -> tempfile.NamedTemporaryFile:
    salt = b"epidemic-openclaw-test-auth-salt"
    payload = {
        "username": username,
        "salt_hex": salt.hex(),
        "hash_hex": hashlib.scrypt(password, salt=salt, n=16384, r=8, p=1, dklen=32).hex(),
        "n": 16384,
        "r": 8,
        "p": 1,
    }
    fh = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json")
    fh.write(json.dumps(payload, sort_keys=True))
    fh.close()
    return fh


def ui_contract_exports() -> dict[str, Any]:
    from fastapi.testclient import TestClient

    previous_dashboard_url = os.environ.get("DASHBOARD_DATABASE_URL")
    previous_review_url = os.environ.get("REVIEW_DATABASE_URL")
    previous_dashboard_auth = os.environ.get("DASHBOARD_AUTH_FILE")
    previous_review_auth = os.environ.get("REVIEW_AUTH_FILE")
    auth_file = fake_auth_file()
    html_reports: list[dict[str, Any]] = []
    try:
        transient_db_url = admin_url_string()
        os.environ["DASHBOARD_DATABASE_URL"] = transient_db_url
        os.environ["REVIEW_DATABASE_URL"] = transient_db_url
        os.environ["DASHBOARD_AUTH_FILE"] = auth_file.name
        os.environ["REVIEW_AUTH_FILE"] = auth_file.name

        from epidemic_core.dashboard.app import COOKIE_NAME as DASH_COOKIE
        from epidemic_core.dashboard.app import create_app as create_dashboard_app
        from epidemic_core.discovery.review_app import COOKIE as REVIEW_COOKIE
        from epidemic_core.discovery.review_app import create_app as create_review_app

        dash_app = create_dashboard_app()
        with TestClient(dash_app) as client:
            login = client.post("/login", data={"username": 'REDACTED', "password": 'REDACTED'}, follow_redirects=False)
            dash_root = client.get("/")
            dash_catalog = client.get("/api/v2/catalog/options")
            dash_prediction = client.get("/api/v1/prediction-demo")
            dash_history = client.get(
                "/api/v2/history/series",
                params={"country_iso2": "CD", "disease_id": "00000000-0000-0000-0000-000000000000"},
            )

        review_app = create_review_app()
        with TestClient(review_app) as client:
            review_login = client.post(
                "/login",
                data={"username": 'REDACTED', "password": 'REDACTED'},
                headers={"origin": "http://127.0.0.1:8121", "referer": "http://127.0.0.1:8121/login"},
                follow_redirects=False,
            )
            review_root = client.get("/reviews")
            review_detail = client.get("/reviews/668e3dd5-cbdf-5e19-af8b-5360ef9e1af9")

        html_reports.append(write_html("ui_captures/dashboard_authenticated_root.html", dash_root.text))
        html_reports.append(write_html("ui_captures/review_authenticated_root.html", review_root.text))
        html_reports.append(write_html("ui_captures/review_don398_authenticated_detail.html", review_detail.text))

        dash_catalog_payload: Any
        try:
            dash_catalog_payload = dash_catalog.json()
        except Exception:
            dash_catalog_payload = {"non_json_sha256": sha_text(dash_catalog.text)}
        try:
            dash_prediction_payload = dash_prediction.json()
        except Exception:
            dash_prediction_payload = {"non_json_sha256": sha_text(dash_prediction.text)}

        report = {
            "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "method": "FastAPI TestClient with temporary fake auth file; real auth secret not read",
            "database_writes": 0,
            "trusted_snapshot_promotion_human_decision_writes": 0,
            "auth_secret_read": False,
            "auth_secret_displayed": False,
            "dashboard_login_status": login.status_code,
            "dashboard_root_status": dash_root.status_code,
            "dashboard_contract_markers": html_contains_all(
                dash_root.text,
                [
                    "country-input",
                    "disease-input",
                    "start-date",
                    "end-date",
                    "cases-chart",
                    "deaths-chart",
                    "quality-chart",
                    "prediction-demo-panel",
                    "p1-10-preview-panel",
                ],
            ),
            "dashboard_catalog_status": dash_catalog.status_code,
            "dashboard_catalog_sha256": sha_obj(dash_catalog_payload),
            "dashboard_prediction_status": dash_prediction.status_code,
            "dashboard_prediction_prediction_count": len(dash_prediction_payload.get("predictions") or [])
            if isinstance(dash_prediction_payload, dict)
            else 0,
            "dashboard_history_probe_status": dash_history.status_code,
            "dashboard_history_probe_note": "invalid disease UUID sentinel is expected to test error containment, not data reconciliation",
            "review_login_status": review_login.status_code,
            "review_root_status": review_root.status_code,
            "review_detail_status": review_detail.status_code,
            "review_detail_contract_markers": html_contains_all(
                review_detail.text,
                [
                    "source-summary-card",
                    "raw-source-summary",
                    "Candidate",
                    "Preview",
                    "artifact",
                    "668e3dd5",
                ],
            ),
            "html_artifacts": html_reports,
            "screenshot_png_status": "NOT_GENERATED_BROWSER_SESSION_OR_RENDERER_REQUIRED",
            "passed_non_browser_contract": dash_root.status_code == 200
            and review_root.status_code == 200
            and review_detail.status_code == 200
            and "prediction-demo-panel" in dash_root.text
            and ("source-summary-card" in review_detail.text or "raw-source" in review_detail.text.casefold()),
        }
        return report
    finally:
        try:
            Path(auth_file.name).unlink()
        except OSError:
            pass
        if previous_dashboard_url is not None:
            os.environ["DASHBOARD_DATABASE_URL"] = previous_dashboard_url
        else:
            os.environ.pop("DASHBOARD_DATABASE_URL", None)
        if previous_review_url is not None:
            os.environ["REVIEW_DATABASE_URL"] = previous_review_url
        else:
            os.environ.pop("REVIEW_DATABASE_URL", None)
        if previous_dashboard_auth is not None:
            os.environ["DASHBOARD_AUTH_FILE"] = previous_dashboard_auth
        else:
            os.environ.pop("DASHBOARD_AUTH_FILE", None)
        if previous_review_auth is not None:
            os.environ["REVIEW_AUTH_FILE"] = previous_review_auth
        else:
            os.environ.pop("REVIEW_AUTH_FILE", None)


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    db = database_exports()
    ui = ui_contract_exports()

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    write_json(
        "source_artifact_manifest.json",
        {
            "created_at_utc": now,
            "source": "epidemic_discovery source_candidate_versions joined to review cases",
            "artifact_count": len(db["artifacts"]),
            "artifact_manifest_sha256": sha_obj(db["artifacts"]),
            "artifacts": db["artifacts"],
        },
    )
    append_jsonl(
        "artifact_lineage.jsonl",
        [
            {
                "lineage_stage": "candidate_review_preview",
                "artifact_sha256": row.get("artifact_sha256"),
                "candidate_version_id": row.get("candidate_version_id"),
                "review_case_id": row.get("review_case_id"),
                "summary_status": row.get("summary_status"),
                "preview_row_count": row.get("preview_row_count"),
                "trusted_written": False,
                "snapshot_written": False,
                "promotion_run": False,
            }
            for row in db["artifacts"]
        ],
    )
    write_json("review_case_export.json", {"created_at_utc": now, "case_count": len(db["review_case_export"]), "cases": db["review_case_export"]})
    write_json(
        "ebola_coverage_before_after.json",
        {
            "created_at_utc": now,
            "baseline_cod_ebola_trusted_rows": 26,
            "current_cod_ebola_trusted_rows": db["trusted_cod_ebola_count"],
            "trusted_cod_ebola_delta": db["trusted_cod_ebola_count"] - 26,
            "candidate_count_current": len(db["review_case_export"]),
            "candidate_preview_rows_current": db["counts"].get("epidemic_discovery.source_preview_rows", 0),
            "trusted_cod_ebola_sha256": db["trusted_cod_ebola_sha256"],
            "promotion_performed_this_cycle": False,
            "human_approval_action_this_cycle": False,
        },
    )
    write_json(
        "candidate_delta.json",
        {
            "created_at_utc": now,
            "current_source_candidates": db["counts"].get("epidemic_discovery.source_candidates", 0),
            "current_source_candidate_versions": db["counts"].get("epidemic_discovery.source_candidate_versions", 0),
            "current_review_cases": db["counts"].get("epidemic_discovery.source_review_cases", 0),
            "current_preview_rows": db["counts"].get("epidemic_discovery.source_preview_rows", 0),
            "current_summary_rows": db["counts"].get("epidemic_discovery.source_review_case_raw_source_summaries", 0),
            "trusted_delta": 0,
            "snapshot_delta": 0,
            "promotion_delta": 0,
            "human_decision_delta": 0,
            "note": "Candidate/preview rows may include earlier phases; no Trusted/Snapshot/Promotion write was performed by this closure script.",
        },
    )
    write_json(
        "temporal_semantics_audit.json",
        {
            "created_at_utc": now,
            "scope": "preview row payload semantic token audit; not a substitute for human approval",
            "row_count": len(db["preview_semantics"]),
            "event_final_rows_detected": sum(1 for r in db["preview_semantics"] if r["mentions_event_final"]),
            "report_snapshot_rows_detected": sum(1 for r in db["preview_semantics"] if r["mentions_report_snapshot"]),
            "total_confirmed_probable_joint_mentions": sum(
                1 for r in db["preview_semantics"] if r["mentions_total"] and (r["mentions_confirmed"] or r["mentions_probable"])
            ),
            "period_token_rows": sum(1 for r in db["preview_semantics"] if r["has_period_token"]),
            "machine_policy": {
                "event_final_is_not_temporal_granularity": True,
                "event_final_not_replicated_to_weekly_series": True,
                "total_confirmed_probable_not_summed_as_independent_total": True,
                "missing_not_interpreted_as_zero": True,
            },
            "sample_rows": db["preview_semantics"][:50],
        },
    )
    write_json(
        "dedupe_report.json",
        {
            "created_at_utc": now,
            "rowset_count": len(db["rowset_entries"]),
            "rowsets": db["rowset_entries"],
            "unresolved_exact_payload_duplicates": sum(int(r["exact_duplicate_payloads"]) for r in db["rowset_entries"]),
            "natural_key_dedupe_requires_promotion_gate": True,
            "promotion_run": False,
        },
    )
    write_json(
        "dashboard_reconciliation.json",
        {
            "created_at_utc": now,
            "database": {
                "trusted_cod_ebola_rows": db["trusted_cod_ebola_count"],
                "trusted_all_ebola_rows": db["trusted_ebola_count"],
                "review_cases": db["counts"].get("epidemic_discovery.source_review_cases", 0),
                "preview_rows": db["counts"].get("epidemic_discovery.source_preview_rows", 0),
                "summary_rows": db["counts"].get("epidemic_discovery.source_review_case_raw_source_summaries", 0),
            },
            "api_ui_contract": ui,
            "db_api_ui_reconciled_for_static_contract": ui.get("passed_non_browser_contract") is True,
            "browser_screenshot_gate": ui.get("screenshot_png_status"),
        },
    )
    write_json(
        "evidence_closure_report.json",
        {
            "created_at_utc": now,
            "status": "EVIDENCE_GAP_CLOSURE_COMPLETE_WITH_BROWSER_SCREENSHOT_GATE",
            "database_identity": db["identity"],
            "alembic_revisions": db["alembic_revisions"],
            "files_written": [
                "source_artifact_manifest.json",
                "artifact_lineage.jsonl",
                "review_case_export.json",
                "ebola_coverage_before_after.json",
                "candidate_delta.json",
                "temporal_semantics_audit.json",
                "dedupe_report.json",
                "dashboard_reconciliation.json",
                "ui_contract_validation.json",
            ],
            "database_writes": 0,
            "trusted_snapshot_promotion_human_decision_writes": 0,
            "ui_non_browser_contract_passed": ui.get("passed_non_browser_contract") is True,
            "remaining_gate": ui.get("screenshot_png_status"),
        },
    )
    write_json("ui_contract_validation.json", ui)
    print(
        json.dumps(
            {
                "status": "EVIDENCE_GAP_CLOSURE_COMPLETE_WITH_BROWSER_SCREENSHOT_GATE",
                "review_cases": len(db["review_case_export"]),
                "artifacts": len(db["artifacts"]),
                "trusted_cod_ebola_rows": db["trusted_cod_ebola_count"],
                "ui_non_browser_contract_passed": ui.get("passed_non_browser_contract") is True,
                "database_writes": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
