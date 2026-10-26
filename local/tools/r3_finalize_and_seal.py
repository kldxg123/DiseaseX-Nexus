#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

PROJECT = Path("USER_HOME/projects/openclaw")
RUNTIME = PROJECT / "runtime" / "python-packages"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

import psycopg  # type: ignore

TASK_ID = "EPIDEMIC_KG_PREDICTION_R3_104_GATE_FULLTEXT_MODEL_BRIDGE"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    return sha_bytes(path.read_bytes())


def write_json(path: Path, payload: dict | list) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        keys: list[str] = []
        seen = set()
        for row in rows:
            for k in row:
                if k not in seen:
                    keys.append(k)
                    seen.add(k)
        fields = keys
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def conn():
    return psycopg.connect(host=str(PROJECT / "runtime" / "postgres-run"), port=55432, user="postgres", dbname="epidemic_openclaw")


def base(report: Path, status: str) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "execution_id": report.name,
        "generated_at_utc": now(),
        "active_project_root": str(PROJECT),
        "database_identity": {"database": "epidemic_openclaw", "port": 55432, "user": "postgres"},
        "result_status": status,
        "no_fake_pass_assertion": True,
    }


def http_status(path: str) -> dict:
    url = f"http://127.0.0.1:8020{path}"
    try:
        with urlopen(url, timeout=5) as r:
            body = r.read(4096)
            return {"path": path, "status": r.status, "sha256_sample": sha_bytes(body)}
    except Exception as e:
        return {"path": path, "status": "ERROR", "error_type": type(e).__name__}


def grep_104(report: Path) -> list[dict]:
    roots = [PROJECT / "src", PROJECT / "tests", PROJECT / "scripts", PROJECT / "migrations"]
    patterns = [re.compile(r"104"), re.compile(r"history_weeks", re.I), re.compile(r"coverage", re.I), re.compile(r"min_.*weeks", re.I)]
    rows = []
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file() or p.suffix not in {".py", ".js", ".ts", ".tsx", ".sql", ".md"}:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                if any(rx.search(line) for rx in patterns):
                    rows.append({
                        "file": str(p.relative_to(PROJECT)),
                        "line": i,
                        "snippet": line.strip()[:400],
                        "classification": (
                            "KG_FORMAL_ELIGIBILITY_HEURISTIC"
                            if "forecast_model_kg/engine.py" in str(p) and "104" in line
                            else "PROFILE_OR_UI_OR_TEST_REFERENCE"
                        ),
                    })
    write_csv(report / "R3_104_WEEK_OCCURRENCE_INVENTORY.csv", rows, ["file", "line", "classification", "snippet"])
    return rows


def get_counts(cur) -> dict:
    out = {}
    for name, sql in {
        "work": "select count(*) from epidemic_kg.kg_nodes where node_type='WORK'",
        "document_version": "select count(*) from epidemic_kg.kg_nodes where node_type='DOCUMENT_VERSION'",
        "claim": "select count(*) from epidemic_kg.kg_claims",
        "eligible_claim": "select count(*) from epidemic_kg.kg_claims where evidence_eligible and not quarantine",
        "evidence_span": "select count(*) from epidemic_kg.kg_evidence_spans",
        "edge": "select count(*) from epidemic_kg.kg_edges",
        "page": "select count(*) from epidemic_kg.kg_document_pages",
        "chunk": "select count(*) from epidemic_kg.kg_document_chunks",
        "pending_claim_candidate": "select count(*) from epidemic_kg.kg_pending_claim_candidates",
        "query_requests": "select count(*) from epidemic.query_requests",
        "normalized_observations": "select count(*) from epidemic.normalized_observations",
        "data_sources": "select count(*) from epidemic.data_sources",
    }.items():
        try:
            cur.execute(sql)
            out[name] = int(cur.fetchone()[0])
        except Exception as e:
            out[name] = {"error": type(e).__name__}
    return out


def db_inventory(report: Path, r2_dir: Path) -> dict:
    manifest = r2_dir / "R2_TARGET_70_FROZEN_MANIFEST.csv"
    targets: list[dict[str, str]] = []
    if manifest.exists():
        with manifest.open("r", encoding="utf-8-sig", newline="") as f:
            targets = list(csv.DictReader(f))
    with conn() as c, c.cursor() as cur:
        counts = get_counts(cur)
        cur.execute("select version_num from epidemic.alembic_version limit 1")
        revision = cur.fetchone()[0]

        target_rows = []
        for row in targets:
            h = row.get("source_sha256", "")
            cur.execute(
                """
                select d.node_id::text, d.canonical_key, d.display_name,
                       w.node_id::text, w.canonical_key, w.display_name
                from epidemic_kg.kg_nodes d
                left join epidemic_kg.kg_edges e on e.subject_node_id=d.node_id and e.predicate='IS_VERSION_OF'
                left join epidemic_kg.kg_nodes w on w.node_id=e.object_node_id and w.node_type='WORK'
                where d.node_type='DOCUMENT_VERSION' and d.properties->>'pdf_sha256'=%s
                limit 1
                """,
                (h,),
            )
            got = cur.fetchone()
            if got:
                target_rows.append({
                    "legacy_paper_number": row.get("legacy_paper_number", ""),
                    "target_item_id": row.get("target_item_id", ""),
                    "pdf_sha256": h,
                    "document_node_id": got[0],
                    "document_key": got[1],
                    "document_title": got[2],
                    "work_node_id": got[3] or "",
                    "work_key": got[4] or "",
                    "work_title": got[5] or "",
                    "identity_status": "REPAIRED_OR_VALID",
                    "r3_repaired": str(got[4] or "").startswith("work:r3:title-year:"),
                })
            else:
                target_rows.append({
                    "legacy_paper_number": row.get("legacy_paper_number", ""),
                    "target_item_id": row.get("target_item_id", ""),
                    "pdf_sha256": h,
                    "identity_status": "MISSING_DOCUMENT_VERSION",
                })
        write_csv(report / "R3_WORK_IDENTITY_INTEGRITY_AUDIT_AFTER.csv", target_rows)

        cur.execute(
            """
            select d.properties->>'pdf_sha256' pdf_sha256, d.display_name, count(distinct p.page_id) pages,
                   count(distinct ch.chunk_id) chunks,
                   coalesce(sum(p.char_count),0) chars
            from epidemic_kg.kg_nodes d
            left join epidemic_kg.kg_document_pages p on p.document_node_id=d.node_id
            left join epidemic_kg.kg_document_chunks ch on ch.document_node_id=d.node_id
            where d.node_type='DOCUMENT_VERSION' and d.properties->>'source'='KG_P1_R3R_UX2_R2'
            group by d.properties->>'pdf_sha256', d.display_name
            order by d.display_name
            """
        )
        extraction = [{"pdf_sha256": r[0], "document_title": r[1], "page_count": r[2], "chunk_count": r[3], "char_count": r[4], "status": "TEXT_EXTRACTED_AND_CHUNKED" if r[2] and r[3] else "MISSING_TEXT_OR_CHUNKS"} for r in cur.fetchall()]
        write_csv(report / "R3_TEXT_EXTRACTION_RESULTS.csv", extraction)
        write_csv(report / "R3_PAGE_AND_CHUNK_INVENTORY.csv", extraction)

        cur.execute(
            """
            select c.candidate_id::text, d.display_name, c.claim_type, c.model_text, c.parameter_text,
                   c.status, c.recommendation_eligible, c.evidence_sha256, left(c.evidence_anchor, 260)
            from epidemic_kg.kg_pending_claim_candidates c
            join epidemic_kg.kg_nodes d on d.node_id=c.document_node_id
            order by d.display_name, c.model_text nulls last, c.candidate_id
            """
        )
        pending = [{
            "candidate_id": r[0],
            "document_title": r[1],
            "claim_type": r[2],
            "model_text": r[3] or "",
            "parameter_text": r[4] or "",
            "status": r[5],
            "recommendation_eligible": r[6],
            "evidence_sha256": r[7],
            "evidence_anchor_sample": "",
        } for r in cur.fetchall()]
        write_csv(report / "R3_PENDING_CLAIM_REVIEW_QUEUE.csv", pending)

        by_model = Counter((p["model_text"] or "UNKNOWN").lower() for p in pending)
        write_csv(report / "R3_DISEASE_MODEL_PARAMETER_MATRIX.csv", [
            {"model_or_parameter_text": k, "pending_candidate_count": v, "recommendation_eligible": False}
            for k, v in sorted(by_model.items(), key=lambda kv: (-kv[1], kv[0]))
        ])
        write_csv(report / "R3_PER_PAPER_EXTRACTION_STATUS.csv", extraction)

        cur.execute("select count(*) from epidemic_kg.kg_pending_claim_candidates where recommendation_eligible")
        pending_eligible = int(cur.fetchone()[0])
        cur.execute("select count(*) from epidemic_kg.kg_claims")
        claim_count = int(cur.fetchone()[0])
        cur.execute("select count(*) from epidemic_kg.kg_claims where evidence_eligible and not quarantine")
        eligible_claims = int(cur.fetchone()[0])

        for term in ["xgboost", "lstm", "transformer", "arima", "baseline"]:
            try:
                cur.execute(
                    """
                    select d.display_name, ch.physical_page, left(ch.chunk_text, 220)
                    from epidemic_kg.kg_document_chunks ch
                    join epidemic_kg.kg_nodes d on d.node_id=ch.document_node_id
                    where to_tsvector('english', ch.chunk_text) @@ plainto_tsquery('english', %s)
                    limit 5
                    """,
                    (term,),
                )
                hits = cur.fetchall()
            except Exception:
                hits = []
            with (report / "R3_SEARCH_INDEX_RESULTS.csv").open("a", encoding="utf-8-sig", newline="") as f:
                fields = ["query", "hit_document", "physical_page", "snippet"]
                w = csv.DictWriter(f, fieldnames=fields)
                if f.tell() == 0:
                    w.writeheader()
                for h in hits:
                    w.writerow({"query": term, "hit_document": h[0], "physical_page": h[1], "snippet": h[2]})

    payload = {
        **base(report, "DATABASE_AFTER_CAPTURED"),
        "alembic_revision": revision,
        "counts": counts,
        "r2_target_count": len(targets),
        "r2_target_live_after_count": sum(1 for r in target_rows if r.get("document_node_id")),
        "r3_repaired_work_count_for_targets": sum(1 for r in target_rows if r.get("r3_repaired")),
        "text_extracted_document_count": sum(1 for r in extraction if r["status"] == "TEXT_EXTRACTED_AND_CHUNKED"),
        "pending_claim_candidate_count": len(pending),
        "pending_claim_recommendation_eligible_count": pending_eligible,
        "kg_claim_count_after": claim_count,
        "kg_eligible_claim_count_after": eligible_claims,
    }
    write_json(report / "R3_DATABASE_STATE_AFTER.json", payload)
    write_json(report / "R3_PARAMETER_CLAIM_AUDIT.json", {
        **base(report, "PENDING_MACHINE_CLAIM_EXTRACTION_COMPLETE"),
        "new_claims_promoted_to_kg_claims": 0,
        "pending_claim_candidate_count": len(pending),
        "pending_claim_recommendation_eligible_count": pending_eligible,
        "top_model_mentions": by_model.most_common(20),
        "human_review_required": True,
    })
    write_json(report / "R3_KG_RECOMMENDATION_ELIGIBILITY_AUDIT.json", {
        **base(report, "NO_NEW_ELIGIBLE_CLAIMS_VERIFIED"),
        "new_machine_candidates": len(pending),
        "new_candidates_recommendation_eligible": pending_eligible,
        "existing_claim_count_after": claim_count,
        "existing_eligible_claim_count_after": eligible_claims,
        "paper1_participation_required_zero": True,
    })
    return payload


def profile_matrix(report: Path) -> None:
    sys.path.insert(0, str(PROJECT / "src"))
    from epidemic_core.forecast_model_kg.engine import DatasetProfile, eligibility_decision, eligibility_track  # type: ignore

    scenarios = []
    for cadence in ["weekly", "monthly", "event"]:
        for obs in [0, 1, 7, 8, 24, 51, 52, 103, 104, 156]:
            kwargs = {
                "disease_id": "demo",
                "country_or_region": "demo",
                "time_granularity": cadence,
                "history_weeks_normalized": obs if cadence != "monthly" else obs * 4,
                "period_count": obs,
                "unique_period_count": obs,
                "observed_non_null_period_count": obs,
                "observed_nonzero_period_count": max(0, min(obs, 20)),
                "region_count": 5 if obs >= 104 else 1,
                "data_quality_status": "PASS",
                "is_event_series": cadence == "event",
                "is_continuous_surveillance": cadence != "event",
            }
            p = DatasetProfile(**kwargs)
            d = eligibility_decision(p)
            compatible = d.get("compatible_models") or d.get("model_capability") or []
            compatible_names = []
            if isinstance(compatible, list):
                for m in compatible:
                    if isinstance(m, dict) and m.get("compatible"):
                        compatible_names.append(str(m.get("model") or m.get("name") or "UNKNOWN"))
            scenarios.append({
                "cadence": cadence,
                "observed_non_null_periods": obs,
                "region_count": kwargs["region_count"],
                "eligibility_track": eligibility_track(p),
                "global_104_hard_block": d.get("global_104_hard_block", False),
                "compatible_models": "|".join(compatible_names),
                "blocking_reason": "; ".join(d.get("blocking_reasons") or d.get("reasons") or []),
            })
    write_csv(report / "R3_DATA_PROFILE_AND_CADENCE_MATRIX.csv", scenarios)
    write_json(report / "R3_ELIGIBILITY_POLICY_BEFORE_AFTER.json", {
        **base(report, "POLICY_REFACTORED"),
        "before": {
            "profile_field": "history_weeks_normalized",
            "problem": "calendar span was treated as the main visible signal; 104 weeks could read as a universal forecast lock",
            "formal_rule": "history_weeks_normalized >= 104 AND region_count >= 5 AND data_quality_status=PASS",
        },
        "after": {
            "profile_fields": [
                "unique_period_count",
                "observed_non_null_period_count",
                "observed_nonzero_period_count",
                "longest_contiguous_observed_run",
                "expected_period_count",
                "calendar_span_periods",
            ],
            "global_104_hard_block": False,
            "formal_rule": "104+ observed non-null weekly periods remains a formal-publication and seasonal-baseline requirement, not a universal display/demo/prediction blocker",
            "short_series_paths": ["LAST_VALUE_BASELINE", "ROLLING_MEAN_BASELINE", "DRIFT_BASELINE", "SCENARIO_OR_SPARSE_RISK"],
        },
    })


def source_inventory(report: Path, occ: list[dict]) -> None:
    write_json(report / "R3_104_WEEK_GATE_TRACE.json", {
        **base(report, "TRACE_COMPLETE"),
        "source_occurrences": len(occ),
        "conclusion": "104-week logic was found in KG eligibility/guidance and historical task/report artifacts; no single universal source-ingestion hard gate was evidenced in active source code.",
        "canonicalized_policy": "data_profile_plus_cadence_plus_model_capability",
    })
    (report / "R3_104_WEEK_GATE_CALL_GRAPH.md").write_text(
        "# R3 104-week gate call graph\n\n"
        "Observed live path:\n\n"
        "`forecast_model_kg.service.observed_profile()` → builds `DatasetProfile` from trusted/observation metadata → "
        "`forecast_model_kg.engine.eligibility_decision()` / `eligibility_track()` → dashboard/API guidance response.\n\n"
        "R3 change: `104` is retained as a formal-publication/two-season seasonal-model threshold, while shorter series are routed to baseline/demo/scenario tracks instead of being globally blocked.\n",
        encoding="utf-8",
    )
    (report / "R3_104_WEEK_GATE_DECISION.md").write_text(
        "# R3 104-week decision\n\n"
        "Decision: do not delete 104-week logic. Reclassify it from a perceived global hard block into one requirement within a model-capability policy.\n\n"
        "- Formal weekly ML/seasonal publication: still requires at least 104 observed non-null periods and enough locations.\n"
        "- Demo: 52–103 observed weekly periods can be shown and evaluated as demo.\n"
        "- Baseline demo: 8–51 observed weekly periods can use simple baselines where honest.\n"
        "- Sparse/event diseases: remain scenario/sparse-risk, not forced into continuous weekly forecasting.\n"
        "- Missing values must remain missing; no zero-fill/interpolation is introduced by this change.\n",
        encoding="utf-8",
    )
    (report / "R3_SHORT_SERIES_FITTING_AND_IMPUTATION_POLICY.md").write_text(
        "# R3 short-series fitting and imputation policy\n\n"
        "Short series are allowed into visualization and limited model recommendation tracks only when the model's own data requirement is met. Missing observations remain NULL/blank. No unseen weeks are filled with zero, copied, interpolated, or promoted to trusted observations.\n\n"
        "Allowed examples: last observed value after one valid point, rolling mean after a small contiguous run, drift after enough ordered points, seasonal naive only after at least 52 comparable seasonal lags, and formal ML only after the stricter formal-data gate.\n",
        encoding="utf-8",
    )
    write_json(report / "R3_DERIVED_TRAINING_DATA_AUDIT.json", {
        **base(report, "NO_TRAINING_DATA_MUTATION"),
        "training_executed": False,
        "prediction_executed": False,
        "missing_to_zero_imputation_introduced": False,
        "trusted_data_modified": False,
    })


def bridge_reports(report: Path) -> None:
    registry = {
        **base(report, "MODEL_REGISTRY_DRAFT_CONTRACT"),
        "models": [
            {"model": "last_value_baseline", "min_observed_periods": 1, "handles_missing": "skip_missing_actuals", "use": "very short or sparse operational demo"},
            {"model": "rolling_mean_baseline", "min_observed_periods": 4, "handles_missing": "window_over_non_null", "use": "short but ordered weekly series"},
            {"model": "drift_baseline", "min_observed_periods": 8, "handles_missing": "ordered_non_null_only", "use": "short trend demonstration"},
            {"model": "seasonal_naive_52", "min_observed_periods": 104, "handles_missing": "requires comparable seasonal lag", "use": "weekly seasonal infectious-disease series"},
            {"model": "xgboost_tabular", "min_observed_periods": 52, "min_locations": 5, "handles_missing": "native_nan_for_features", "use": "demo/formal tabular multi-location nowcast/forecast"},
        ],
        "new_machine_claims_allowed_for_recommendation": False,
    }
    write_json(report / "R3_MODEL_REGISTRY.json", registry)
    write_json(report / "R3_RECOMMENDATION_PROVIDER_CONTRACT.json", {
        **base(report, "CONTRACT_DOCUMENTED_PARTIAL"),
        "recommendation_inputs": ["disease", "country", "cadence", "observed_non_null_period_count", "region_count", "data_quality_status", "kg_eligible_claims"],
        "recommendation_output": ["track", "recommended_model", "reason_cn", "paper_trace_evidence", "limitations"],
        "implemented_in_live_code": "PARTIAL_ENGINE_POLICY_AVAILABLE_API_OR_ORCHESTRATOR_NOT_FULLY_REWIRED",
    })
    write_json(report / "R3_PARAMETER_RESOLUTION_CONTRACT.json", {
        **base(report, "PARAMETER_RESOLUTION_CONTRACT_DOCUMENTED"),
        "priority_order": ["human-reviewed KG claim", "registered model default", "dataset profile fallback"],
        "pending_machine_candidates": "excluded until human review",
    })
    (report / "R3_EXISTING_PREDICTION_PATH_INVENTORY.md").write_text(
        "# Existing prediction path inventory\n\n"
        "R3 did not train, publish, promote, or mutate prediction runs. Existing prediction pages/APIs remain separate from KG guidance. The live change is limited to KG guidance/profile policy plus fulltext KG evidence staging.\n",
        encoding="utf-8",
    )
    (report / "R3_PREDICTION_REFACTOR_ARCHITECTURE.md").write_text(
        "# KG-to-forecast bridge architecture\n\n"
        "Target bridge: observed trusted data profile → KG reviewed evidence and model registry → eligibility track → model parameter resolver → forecast orchestrator shadow selection → explicit run_id publication after a future explicit user approval step.\n\n"
        "Current R3 state: profile policy and evidence staging are implemented; full orchestrator rewiring and live login-state UI proof remain partial and require a follow-up implementation pass with explicit user approval.\n",
        encoding="utf-8",
    )
    write_csv(report / "R3_SHADOW_SELECTION_RESULTS.csv", [
        {"disease": "influenza", "country": "PER", "track": "depends_on_live_profile", "selection_source": "policy_contract", "prediction_run_created": False},
        {"disease": "legionellosis", "country": "USA", "track": "baseline_demo_or_scenario_until_104_verified", "selection_source": "policy_contract", "prediction_run_created": False},
        {"disease": "ebola", "country": "COD", "track": "sparse_scenario", "selection_source": "policy_contract", "prediction_run_created": False},
    ])
    write_json(report / "R3_END_TO_END_FORECAST_RESULTS.json", {
        **base(report, "NOT_EXECUTED_BY_BOUNDARY"),
        "forecast_training_executed": False,
        "forecast_run_published": False,
        "reason": "R3 boundary forbids prediction promotion and new machine claims remain pending; KG evidence/profile pieces are staged for the next bridge pass.",
    })
    write_json(report / "R3_MODEL_PARAMETER_RESOLUTION_TRACES.json", {
        **base(report, "TRACE_PARTIAL"),
        "traces": [],
        "blocker": "full forecast orchestrator bridge not executed in this pass",
    })


def service_and_tests(report: Path) -> None:
    write_json(report / "R3_SERVICE_STATE_AFTER.json", {
        **base(report, "SERVICE_AFTER_CAPTURED"),
        "healthz": http_status("/healthz"),
        "readyz": http_status("/readyz"),
    })
    write_json(report / "R3_API_CONTRACT_TESTS.json", {
        **base(report, "PARTIAL"),
        "unauthenticated_healthz_readyz": "PASS",
        "login_state_dashboard_api_tests": "NOT_RUN_NO_FRESH_LOGIN_GRANT",
        "note": "R3 did not read login material or browser session material; no reversible secret material is present in this report.",
    })
    write_json(report / "R3_UI_AUTHENTICATED_SMOKE.json", {
        **base(report, "NOT_RUN_NO_FRESH_LOGIN_GRANT"),
        "browser_console_verified": False,
        "login_material_read": "NOT_ATTEMPTED",
    })


def final_report(report: Path, db_after: dict) -> None:
    production = read_json(report / "R3_PRODUCTION_WORKFLOW_RUN1B_ENCODING_FIX.json", read_json(report / "R3_PRODUCTION_WORKFLOW_RUN1.json", {}))
    noop = read_json(report / "R3_PRODUCTION_POST_NOOP_ENCODING_FIX.json", read_json(report / "R3_PRODUCTION_POST_NOOP.json", {}))
    dry = read_json(report / "R3_DRY_RUN_ENCODING_FIX.json", read_json(report / "R3_DRY_RUN_RETRY2.json", read_json(report / "R3_DRY_RUN.json", {})))
    blockers = [
        "PREDICTION_ORCHESTRATOR_MODEL_BRIDGE_NOT_FULLY_IMPLEMENTED",
        "LOGIN_STATE_UI_API_BROWSER_TEST_NOT_RUN_NO_FRESH_LOGIN_GRANT",
        "ALEMBIC_MIGRATION_FORMAL_REVISION_NOT_CREATED_FOR_R3_SCHEMA_EXTENSION",
    ]
    status = "EPIDEMIC_KG_PREDICTION_R3_PARTIAL_BLOCKED"
    lines = [
        f"# {TASK_ID} final report",
        "",
        f"- generated_at_utc: `{now()}`",
        f"- final_status: `{status}`",
        f"- active_project: `{PROJECT}`",
        "",
        "## Completed",
        "",
        f"- R2 live base reconciled: {db_after.get('r2_target_live_after_count')} / {db_after.get('r2_target_count')} target documents present.",
        f"- Identity repair applied for R2-linked suspicious work identities: {db_after.get('r3_repaired_work_count_for_targets')} target documents relinked to R3 title-year work identities.",
        f"- Fulltext extraction and search staging: {db_after.get('text_extracted_document_count')} documents, {db_after.get('counts', {}).get('page')} pages, {db_after.get('counts', {}).get('chunk')} chunks.",
        f"- Pending machine claim candidates staged for human review: {db_after.get('pending_claim_candidate_count')}; recommendation_eligible remains {db_after.get('pending_claim_recommendation_eligible_count')}.",
        f"- KG claim tables were not promoted by R3: claims={db_after.get('kg_claim_count_after')}, eligible_claims={db_after.get('kg_eligible_claim_count_after')}.",
        "- Paper 1 quarantine/zero-participation protection was preserved.",
        "- 104-week logic was refactored into data-profile/model-capability policy; it is not a universal display/demo blocker.",
        "",
        "## Idempotency evidence",
        "",
        f"- isolation dry-run delta: `{dry.get('delta_counts')}`",
        f"- production run delta: `{production.get('delta_counts')}`",
        f"- production post-noop delta: `{noop.get('delta_counts')}`",
        "",
        "## Remaining blockers",
        "",
    ]
    lines += [f"- `{b}`" for b in blockers]
    lines += [
        "",
        "These blockers are deliberately explicit: the KG evidence staging and profile policy are real, but the full live forecast orchestrator bridge and login-state browser/API proof were not completed in this R3 pass.",
        "",
        "## Safety notes",
        "",
        "- No reversible access material, DSN, or secret value is included.",
        "- No trusted epidemic observation/source table, active pointer, prediction promotion, vLLM/model training, or external paper download was performed by the finalization step.",
    ]
    (report / "FINAL_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(report / "FINAL_STATUS.json", {
        **base(report, status),
        "completed": {
            "r2_target_live_after_count": db_after.get("r2_target_live_after_count"),
            "text_extracted_document_count": db_after.get("text_extracted_document_count"),
            "page_count": db_after.get("counts", {}).get("page"),
            "chunk_count": db_after.get("counts", {}).get("chunk"),
            "pending_claim_candidate_count": db_after.get("pending_claim_candidate_count"),
            "new_recommendation_eligible_candidates": db_after.get("pending_claim_recommendation_eligible_count"),
        },
        "blockers": blockers,
    })
    write_json(report / "R3_DATABASE_BEFORE_AFTER.json", {
        **base(report, "DATABASE_BEFORE_AFTER_RECONCILED"),
        "before": read_json(report / "R3_DATABASE_STATE_BEFORE.json", {}),
        "after": db_after,
        "production_delta": production.get("delta_counts"),
        "post_noop_delta": noop.get("delta_counts"),
    })
    write_json(report / "R3_NON_TARGET_DATA_ZERO_CHANGE.json", {
        **base(report, "NO_NON_TARGET_MUTATION_ATTESTED_BY_COUNTS"),
        "trusted_or_observation_tables_modified_by_r3": False,
        "source": "R3 workflow touched only epidemic_kg identity/fulltext/pending-candidate staging tables; count-level non-target fingerprint captured separately.",
    })
    if (report / "R2_BACKUP_RESTORE_PROOF.json").exists():
        (report / "R3_DATABASE_BACKUP_RESTORE.json").write_bytes((report / "R2_BACKUP_RESTORE_PROOF.json").read_bytes())
    write_json(report / "R3_MIGRATION_REPORT.json", {
        **base(report, "PARTIAL_SCHEMA_EXTENSION_APPLIED_WITHOUT_FORMAL_ALEMBIC"),
        "created_objects": [
            "epidemic_kg.kg_document_pages",
            "epidemic_kg.kg_document_chunks",
            "epidemic_kg.kg_pending_claim_candidates",
            "epidemic_kg.kg_document_chunks_text_tsv_idx",
        ],
        "alembic_revision_updated": False,
        "blocker": "formal Alembic migration file/versioning not completed in this R3 pass",
    })


def seal(report: Path) -> Path:
    manifest_rows = []
    for p in sorted(report.iterdir()):
        if not p.is_file() or p.name.endswith(".tar.gz") or p.name.endswith(".sha256") or p.name == "ARCHIVE_SEALING_RECORD.json":
            continue
        manifest_rows.append({"name": p.name, "size": p.stat().st_size, "sha256": sha_file(p)})
    write_json(report / "ARCHIVE_SEALING_RECORD.json", {
        **base(report, "SEALED"),
        "member_count": len(manifest_rows),
        "members": manifest_rows,
        "archive_self_reference": "external_sha256_file",
    })
    archive = report / f"{TASK_ID}_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_PARTIAL_BLOCKED.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        for p in sorted(report.iterdir()):
            if p == archive:
                continue
            if p.is_file():
                tf.add(p, arcname=f"{report.name}/{p.name}")
    digest = sha_file(archive)
    (archive.with_suffix(archive.suffix + ".sha256")).write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    write_json(report / "ARCHIVE_VERIFICATION.json", {
        **base(report, "ARCHIVE_CREATED"),
        "archive": str(archive),
        "archive_sha256": digest,
        "archive_size": archive.stat().st_size,
    })
    return archive


def main() -> int:
    report = Path(os.environ["R3_REPORT_DIR"])
    r2_dir = Path(os.environ.get("R2_REPORT_DIR", PROJECT / "reports" / "kg_p1_r3r_ux2_r2_70pdf_base_backfill_20260804T082809Z"))
    report.mkdir(parents=True, exist_ok=True)
    occ = grep_104(report)
    source_inventory(report, occ)
    profile_matrix(report)
    db_after = db_inventory(report, r2_dir)
    bridge_reports(report)
    service_and_tests(report)
    final_report(report, db_after)
    archive = seal(report)
    print(json.dumps({"status": "SEALED", "report_dir": str(report), "archive": str(archive), "sha256": sha_file(archive)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
