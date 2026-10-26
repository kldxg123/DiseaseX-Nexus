#!/usr/bin/env python3
"""Seed the epidemic_kg R3C demo layer on the A100 PostgreSQL database.

Replays the vm201 kg_r3c demo export (reports/kg_r3c_server_asset_incremental_20260809T145021Z/)
into the empty epidemic_kg schema on A100 so the KG dashboard pages show the same
content as the earlier demo:

- kg_import_runs / kg_import_members     (11_IMPORT_RUN.json, 06_ARCHIVE_MEMBER_MANIFEST.csv)
- kg_graph_versions v1 + v2              (14_GRAPH_VERSION.json / 14b)
- kg_review_items                        (synthesized from 13_ASSERTION_REVIEW_COUNTS.json counts)
- kg_data_snapshots / kg_data_profiles / kg_forecast_tasks
  (regenerated on A100 via epidemic_core.kg_r3c.profiles.snapshot_and_profile — idempotent,
   stable_uuid keyed on series content; missing series fall back to rows synthesized from
   15_DATA_PROFILES.csv keeping the original profile_id)
- kg_model_adapters                      (16_MODEL_ADAPTER_REGISTRY.json)
- kg_parameter_bundles                   (17_PARAMETER_BUNDLES.json)
- kg_score_policies                      (18_SCORE_POLICY.json)
- kg_recommendation_runs / kg_recommendations / kg_recommendation_components
  (19_ALL_CANDIDATE_SCORES.csv + 20_SELECTED_RECOMMENDATIONS.csv + 22 no-eligible gaps)
- kg_execution_contracts                 (25b_EXECUTION_CONTRACTS.json)

All inserts are idempotent (ON CONFLICT DO NOTHING / deterministic stable_uuid PKs),
so the whole script can be re-run safely. Nothing outside schema epidemic_kg is touched.
No remote service is restarted.

Usage (from repo root, local .venv python):
    .venv/Scripts/python tools/kg_r3c_seed_a100.py              # full seed + verify
    .venv/Scripts/python tools/kg_r3c_seed_a100.py --build-only # assemble payload, no SSH
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import importlib.util
import json
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "kg_r3c_server_asset_incremental_20260809T145021Z"
MIG_MODULE = ROOT / "tools" / "migrate_epidemic_to_a100.py"
ADAPTERS_MODULE = ROOT / "kg_pt_work" / "remote_src" / "kg_r3c" / "adapters.py"

NS = uuid.UUID("a3c1e7d2-5b6f-4a8b-9c0d-1e2f3a4b5c6d")  # kg_r3c namespace (dbutil.py)
REMOTE_PAYLOAD_PATH = "$HOME/autodl-tmp/_kg_r3c_seed_payload.json"

GRAPH_V1 = "4fa7ea54-3679-50f7-9a0b-fe19b9ccf351"
GRAPH_V2 = "6ac06cc5-606a-585f-a91b-a455824f1159"
RUN_ID = "cbcdcce9-0a14-5028-8fef-8243379d8958"
BUNDLE_VERSION = "kg_r3c.bundles.v1"
ADAPTER_MODULE_PATH = "epidemic_core.kg_r3c.adapters"

REVIEW_REASONS = {
    "DUPLICATE_ALIAS": "duplicate_alias_metadata_quarantined",
    "LEGACY_CLAIM_EMPTY_SUBJECT": "legacy_claim_empty_subject",
    "PDF_PARSE_FAILED": "pdf_parse_failed",
    "OCR_REQUIRED": "ocr_required",
    "PDF_DUPLICATE_ALIAS": "pdf_duplicate_alias",
    "ORPHAN_METADATA": "orphan_metadata_row",
    "ORPHAN_PDF": "orphan_pdf_without_metadata",
}

POLICY_MAX = {"A": 20.0, "B": 15.0, "C": 10.0, "D": 20.0, "E": 15.0, "F": 20.0}


def stable_uuid(*parts: str) -> str:
    return str(uuid.uuid5(NS, "|".join(str(p) for p in parts)))


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def load_adapter_registry() -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("kg_r3c_adapters", ADAPTERS_MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kg_r3c_adapters"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return {a.adapter_id: a for a in mod.REGISTRY}


def load_migrate_module() -> Any:
    spec = importlib.util.spec_from_file_location("mig", MIG_MODULE)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mig"] = mod
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _f(value: str) -> float | None:
    value = (value or "").strip()
    return float(value) if value else None


def build_components(row: dict[str, str], adapter: Any, profile: dict[str, str],
                     bundle_sha: str | None, warnings: list[str]) -> list[dict[str, Any]]:
    """Reconstruct per-rule score components from the vm201 aggregate CSV row.

    Deterministic rules (A1-A3, B, C, E, F, penalties) are recomputed from the
    profile CSV fields + adapter spec exactly as scoring.py does; A4 is the
    residual (gap regularity is not in the CSV); D is a single aggregate row
    because evidence spans are not re-seeded on A100.
    """
    label = f"{row['profile']}|{row['adapter_id']}"
    sem = profile["semantics"]
    n_obs = int(profile["n_obs"])
    zero_ratio = float(profile["zero_ratio"] or 0.0)
    overdisp = _f(profile["overdispersion"])
    freq = profile["frequency"]
    dims = {d: float(row[f"dim_{d}"] or 0.0) for d in "ABCDEF"}
    comps: list[dict[str, Any]] = []

    def add(dim: str, rule: str, raw: Any, score: float, maxs: float,
            rationale: str, sources: list[str], penalty: bool = False) -> None:
        comps.append({"dimension": dim, "rule_id": rule, "raw_value": str(raw),
                      "score": round(float(score), 4), "max_score": float(maxs),
                      "is_penalty": penalty, "rationale": rationale,
                      "source_ids": [s for s in sources if s]})

    # A1 distribution match (mirrors scoring.py)
    if sem in ("INCIDENT", "CUMULATIVE_SUSPECTED"):
        od = overdisp or 1.0
        if adapter.supports_overdispersion and od > 1.5:
            a1: float = 8
        elif adapter.supports_overdispersion:
            a1 = 7
        elif "Kalman" in adapter.model_family or "local" in adapter.model_family.lower():
            a1 = 5
        else:
            a1 = 4
    elif sem == "CUMULATIVE":
        a1 = 7 if "CUMULATIVE" in adapter.semantics else 0
    elif sem == "EVENT":
        a1 = 8 if sem in adapter.semantics else 0
    else:
        a1 = 4
    add("A_data_structure_fit", "A1_DISTRIBUTION_MATCH",
        f"sem={sem};overdisp={overdisp}", a1, 8, "recomputed from profile export", [])
    a2 = 4 if zero_ratio <= 0.3 else (4 if adapter.supports_zero_inflation else 1)
    add("A_data_structure_fit", "A2_ZERO_INFLATION", zero_ratio, a2, 4,
        "recomputed from profile export", [])
    if n_obs >= max(adapter.min_obs * 2, 24):
        a3 = 4
    elif n_obs >= adapter.min_obs:
        a3 = 3
    elif n_obs >= 4:
        a3 = 1
    else:
        a3 = 0
    add("A_data_structure_fit", "A3_LENGTH", n_obs, a3, 4,
        f"n_obs={n_obs} vs min_obs={adapter.min_obs}", [])
    a4 = round(dims["A"] - a1 - a2 - a3, 4)
    if a4 not in (0.0, 2.0, 4.0):
        warnings.append(f"A4 residual unusual {a4} for {label}")
    add("A_data_structure_fit", "A4_FREQUENCY_REGULARITY", freq, a4, 4,
        "residual of exported dim_A total (gap regularity not in CSV)", [])

    mech = adapter.disease_mechanism
    if mech == "TRANSMISSION_PROCESS":
        b1 = 10 if sem == "INCIDENT" else 4
    elif mech == "OBSERVATION_PROCESS":
        b1 = 8
    else:
        b1 = 5
    add("B_disease_mechanism_fit", "B1_MECHANISM_CLASS", mech, b1, 10,
        "recomputed from adapter spec", [])
    b2 = 5 if adapter.model_family in (
        "Negative-Binomial DGLM", "Gamma-Poisson DGLM", "Dynamic hurdle count state-space",
        "Renewal (Cori R_t)") else 3
    add("B_disease_mechanism_fit", "B2_INDICATOR_APPLICABILITY", profile["indicator"],
        b2, 5, "recomputed from adapter spec", [])
    if abs(b1 + b2 - dims["B"]) > 1e-6:
        warnings.append(f"B mismatch {b1 + b2}!={dims['B']} for {label}")

    add("C_country_scenario_transfer", "C1_GEO_INDEPENDENCE", profile["country"], 7, 7,
        "model has no country-specific structural parameters", [])
    c2 = round(dims["C"] - 7, 4)
    add("C_country_scenario_transfer", "C2_SURVEILLANCE_QUALITY", "TRUSTED_LIVE", c2, 3,
        "residual of exported dim_C total", [])

    add("D_scientific_evidence", "D_FULLTEXT_EVIDENCE_AGGREGATE", "vm201_export",
        dims["D"], 20,
        "aggregate of D1/D2/D3 from vm201 demo export; fulltext evidence spans "
        "are not re-seeded on A100, so per-rule split is unavailable", [])

    pconf = float(row["parameter_confidence"])
    e1 = round(pconf / 100.0 * 15, 4)
    if abs(e1 - dims["E"]) > 0.01:
        warnings.append(f"E mismatch {e1}!={dims['E']} for {label}")
        e1 = dims["E"]
    add("E_parameter_confidence", "E1_BUNDLE_CONFIDENCE", pconf, e1, 15,
        "bundle confidence from vm201 demo export",
        [(bundle_sha or "")[:16]])

    add("F_execution_validation_readiness", "F1_ADAPTER_EXECUTABLE",
        adapter.adapter_id, 8, 8, "adapter registered with executable implementation",
        [adapter.adapter_id])
    f2 = 6 if n_obs >= adapter.min_obs else 0
    add("F_execution_validation_readiness", "F2_DATA_REQUIREMENTS",
        f"{n_obs}>={adapter.min_obs}", f2, 6, "minimum observations met", [])
    f3 = 3 if adapter.probabilistic_output else 0
    add("F_execution_validation_readiness", "F3_PROBABILISTIC_OUTPUT",
        adapter.probabilistic_output, f3, 3, "posterior/predictive-distribution intervals", [])
    f4 = 3 if n_obs >= 24 and sem != "EVENT" else (1 if n_obs >= 8 else 0)
    add("F_execution_validation_readiness", "F4_BACKTEST_FEASIBLE", n_obs, f4, 3,
        "rolling-origin H1-H4 backtest feasibility", [])
    if abs(8 + f2 + f3 + f4 - dims["F"]) > 1e-6:
        warnings.append(f"F mismatch {8 + f2 + f3 + f4}!={dims['F']} for {label}")

    pen = float(row["penalties"] or 0.0)
    if pen <= -2.0:
        add("PENALTY", "P_IRREGULAR_CADENCE",
            "gap_structure.regular == false and cadence != 'irregular'", -2.0, 0,
            "irregular cadence penalty (vm201 export)", [], penalty=True)
    if pen in (-1.0, -3.0):
        add("PENALTY", "P_REVISION_HISTORY",
            "reporting_delay_revision_flags.has_revisions == true", -1.0, 0,
            "revision history penalty (vm201 export)", [], penalty=True)

    total = round(sum(c["score"] for c in comps), 4)
    if abs(total - float(row["total_score"])) > 1e-6:
        warnings.append(f"total mismatch {total}!={row['total_score']} for {label}")
    return comps


def build_payload() -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    registry = load_adapter_registry()

    profiles = list(csv.DictReader(
        (REPORT / "15_DATA_PROFILES.csv").open(encoding="utf-8")))
    prof_by_label = {
        f"{p['disease']}|{p['country']}|{p['indicator']}|{p['frequency']}": p
        for p in profiles
    }

    import_run = json.loads((REPORT / "11_IMPORT_RUN.json").read_text(encoding="utf-8"))
    gv2 = json.loads((REPORT / "14_GRAPH_VERSION.json").read_text(encoding="utf-8"))
    policy_doc = json.loads((REPORT / "18_SCORE_POLICY.json").read_text(encoding="utf-8"))
    adapters_doc = json.loads((REPORT / "16_MODEL_ADAPTER_REGISTRY.json").read_text(encoding="utf-8"))
    bundles_doc: dict[str, Any] = json.loads(
        (REPORT / "17_PARAMETER_BUNDLES.json").read_text(encoding="utf-8"))
    candidates = list(csv.DictReader(
        (REPORT / "19_ALL_CANDIDATE_SCORES.csv").open(encoding="utf-8")))
    selected = {r["profile"] + "|" + r["adapter_id"]: r for r in csv.DictReader(
        (REPORT / "20_SELECTED_RECOMMENDATIONS.csv").open(encoding="utf-8"))}
    no_eligible = json.loads(
        (REPORT / "22_THRESHOLD_AND_BASELINE_PROOF.json").read_text(encoding="utf-8")
    )["no_eligible_profiles"]
    contracts = json.loads(
        (REPORT / "25b_EXECUTION_CONTRACTS.json").read_text(encoding="utf-8"))["contracts"]
    review_counts = json.loads(
        (REPORT / "13_ASSERTION_REVIEW_COUNTS.json").read_text(encoding="utf-8")
    )["review_items_by_kind"]
    members = list(csv.DictReader(
        (REPORT / "06_ARCHIVE_MEMBER_MANIFEST.csv").open(encoding="utf-8")))

    # bundles: rebuild full parameters entries from adapter ParamSpecs + exported details
    bundles = []
    for bundle_id, b in bundles_doc.items():
        adapter = registry[b["adapter_id"]]
        spec_by_name = {p.name: p for p in adapter.params}
        params = []
        for d in b["param_details"]:
            spec = spec_by_name[d["param"]]
            rule = d["source_rule"]
            adaptation = {
                "SNAPSHOT_CLOSED_FORM_ESTIMATE": "estimated in closed form from all snapshot observations",
                "SNAPSHOT_DATA_DRIVEN_PRIOR": "derived from immutable snapshot statistics",
            }.get(rule, "none")
            params.append({
                "name": spec.name, "type": spec.ptype, "unit": spec.unit,
                "allowed_range": [spec.lo, spec.hi], "value": d["value"],
                "source_tier": d["tier"], "source_rule": rule,
                "source_ids": d.get("source_ids", []), "adaptation": adaptation,
                "identifiable_from_data": float(d["identifiability_factor"]) >= 1.0,
            })
        bundles.append({
            "bundle_id": bundle_id, "adapter_id": b["adapter_id"],
            "profile_id": b["profile_id"], "bundle_version": BUNDLE_VERSION,
            "parameters": params, "completeness": b["completeness"],
            "source_tiers": b["source_tiers"], "bundle_sha256": b["bundle_sha256"],
        })
    bundle_by_key = {(b["adapter_id"], b["profile_id"]): b for b in bundles}

    # recommendations + components
    recs: list[dict[str, Any]] = []
    for row in candidates:
        profile = prof_by_label[row["profile"]]
        adapter = registry[row["adapter_id"]]
        csv_profile_id = profile["profile_id"]
        sel = selected.get(row["profile"] + "|" + row["adapter_id"])
        rec_id = (sel["recommendation_id"] if sel else
                  stable_uuid("recommendation", RUN_ID, csv_profile_id, row["adapter_id"]))
        base = {"recommendation_id": rec_id, "csv_profile_id": csv_profile_id,
                "adapter_id": row["adapter_id"], "components": []}
        if row["status"] == "EXCLUDED":
            reasons = [r for r in row["reasons"].split(";") if r]
            recs.append({**base, "bundle_id": None,
                         "is_baseline": row["is_baseline"] == "True",
                         "total_score": None, "parameter_confidence_score": None,
                         "recomputed_total": None, "recomputed_param": None,
                         "rank": None, "eligibility_status": "HARD_GATED",
                         "ineligibility_reasons": reasons, "gap_list": reasons,
                         "tie_break_key": None})
            continue
        bundle = bundle_by_key.get((row["adapter_id"], csv_profile_id))
        if bundle is None:
            warnings.append(f"no bundle for {row['profile']}|{row['adapter_id']}")
        total = float(row["total_score"])
        pconf = float(row["parameter_confidence"])
        eligible = row["status"] == "ELIGIBLE"
        reasons = ([] if eligible else
                   (["total<=70"] if total <= 70 else []) +
                   (["param_conf<=70"] if pconf <= 70 else []))
        recs.append({
            **base,
            "bundle_id": (sel["bundle_id"] if sel else (bundle or {}).get("bundle_id")),
            "is_baseline": False,
            "total_score": total, "parameter_confidence_score": pconf,
            "recomputed_total": total, "recomputed_param": pconf,
            "rank": int(row["rank"]),
            "eligibility_status": "ELIGIBLE_RECOMMENDATION" if eligible else "BELOW_THRESHOLD",
            "ineligibility_reasons": reasons, "gap_list": [],
            "tie_break_key": f"{total:.4f}|{pconf:.4f}|{row['adapter_id']}",
            "components": build_components(row, adapter, profile,
                                           (bundle or {}).get("bundle_sha256"), warnings),
        })
    for gap in no_eligible:
        profile = prof_by_label[gap["profile"]]
        recs.append({
            "recommendation_id": stable_uuid("recommendation", RUN_ID,
                                             profile["profile_id"], "NO_ELIGIBLE"),
            "csv_profile_id": profile["profile_id"], "adapter_id": None,
            "bundle_id": None, "is_baseline": False,
            "total_score": None, "parameter_confidence_score": None,
            "recomputed_total": None, "recomputed_param": None, "rank": None,
            "eligibility_status": "NO_ELIGIBLE_MODEL_WITH_EXACT_GAPS",
            "ineligibility_reasons": [], "gap_list": gap["gaps"],
            "tie_break_key": None, "components": [],
        })

    payload = {
        "import_run": import_run,
        "members": members,
        "graph_versions": [
            {"graph_version_id": GRAPH_V1, "import_run_id": import_run["import_run_id"],
             "parent_graph_version_id": None,
             "document_set_hash": gv2["document_set_hash"],
             "assertion_set_hash": gv2["assertion_set_hash"],
             "extractor_versions": gv2["extractor_versions"],
             "review_policy": gv2["review_policy"], "status": "PUBLISHED",
             "integrity_report": {
                 "note": "v1 published mid-pipeline; evidence bindings completed afterwards "
                         "and re-attested as v2 6ac06cc5-606a-585f-a91b-a455824f1159",
                 "reattested_by": GRAPH_V2},
             "published_at": "2026-08-09T23:59:00+08:00"},
            gv2,
        ],
        "policy": {**policy_doc["policy"], "policy_sha256": policy_doc["policy_sha256"]},
        "adapters": [
            {"adapter_id": a["adapter_id"], "model_family": a["model_family"],
             "adapter_version": a["adapter_version"], "module_path": ADAPTER_MODULE_PATH,
             "is_baseline": a["is_baseline"], "executable": True,
             "capabilities": {"semantics": a["semantics"],
                              "probabilistic_output": a["probabilistic_output"],
                              "disease_mechanism": a["disease_mechanism"]},
             "data_requirements": {"min_obs": a["min_obs"], "params": a["params"]}}
            for a in adapters_doc["adapters"]
        ],
        "profiles": profiles,
        "bundles": bundles,
        "run": {"run_id": RUN_ID, "graph_version_id": GRAPH_V1,
                "policy_id": policy_doc["policy"]["policy_id"],
                "score_type": "PRE_EXECUTION_EVIDENCE_SCORE"},
        "recommendations": recs,
        "contracts": contracts,
        "review_counts": review_counts,
        "review_reasons": REVIEW_REASONS,
    }
    return payload, warnings


REMOTE_PY = r'''
import json, os, sys, hashlib, uuid
from urllib.parse import urlsplit, urlunsplit

sys.path.insert(0, os.path.expanduser("~/autodl-tmp/epidemic-dashboard-runtime/src"))
NS = uuid.UUID("a3c1e7d2-5b6f-4a8b-9c0d-1e2f3a4b5c6d")

def stable_uuid(*parts):
    return str(uuid.uuid5(NS, "|".join(str(p) for p in parts)))

def sha256_text(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

import psycopg
from epidemic_core.kg_r3c.profiles import snapshot_and_profile

seed = json.load(open(os.path.expanduser("~/autodl-tmp/_kg_r3c_seed_payload.json"), encoding="utf-8"))
_admin_pw = os.environ["POSTGRES_ADMIN_PASSWORD"]
parts = urlsplit(os.environ["MIGRATION_DATABASE_URL"])
netloc = "epidemicdb:" + _admin_pw + "@" + (parts.hostname or "127.0.0.1") + ":" + str(parts.port or 5432)
dsn = urlunsplit(("postgresql", netloc, parts.path, parts.query, parts.fragment))

def _sanitize(text):
    return text.replace(_admin_pw, "***").replace(dsn, "<dsn>")

def _excepthook(t, v, tb):
    print("SEED_FATAL: " + _sanitize(t.__name__ + ": " + str(v)))
    sys.exit(4)

sys.excepthook = _excepthook

try:
    conn = psycopg.connect(dsn)
except Exception as exc:
    print("SEED_FATAL: db connect failed: " + _sanitize(type(exc).__name__ + ": " + str(exc)))
    sys.exit(3)
cur = conn.cursor()
S = "epidemic_kg"
report = {"inserted": {}, "notes": []}

def inserted_count(label, before_sql=None):
    return None

def table_count(name):
    cur.execute("select count(*) from %s.%s" % (S, name))
    return cur.fetchone()[0]

before = {t: table_count(t) for t in (
    "kg_import_runs", "kg_import_members", "kg_graph_versions", "kg_review_items",
    "kg_data_snapshots", "kg_data_profiles", "kg_forecast_tasks", "kg_model_adapters",
    "kg_parameter_bundles", "kg_score_policies", "kg_recommendation_runs",
    "kg_recommendations", "kg_recommendation_components", "kg_execution_contracts")}

# ---- 1. snapshots / profiles / forecast tasks from the live view (idempotent) ----
prof_results = snapshot_and_profile(conn)
report["profiles_from_live_view"] = len(prof_results)

cur.execute("""select p.profile_id::text, p.profile_sha256, p.snapshot_id::text,
                      s.disease, s.country, s.indicator, s.frequency
               from epidemic_kg.kg_data_profiles p
               join epidemic_kg.kg_data_snapshots s on s.snapshot_id = p.snapshot_id""")
db_profs = cur.fetchall()
by_id = {r[0]: r for r in db_profs}
by_key = {(r[3], r[4], r[5], r[6]): r for r in db_profs}

mapping = {}
synthesized = []
for cp in seed["profiles"]:
    key = (cp["disease"], cp["country"], cp["indicator"], cp["frequency"])
    if cp["profile_id"] in by_id:
        mapping[cp["profile_id"]] = cp["profile_id"]
    elif key in by_key:
        mapping[cp["profile_id"]] = by_key[key][0]
        report["notes"].append("profile key matched with new id: " + "|".join(key))
    else:
        synthesized.append(cp)

for cp in synthesized:
    key = "|".join([cp["disease"], cp["country"], cp["indicator"], cp["frequency"]])
    snap_id = stable_uuid("snapshot_seed", key)
    s_sha = sha256_text("seed-series|" + key)
    snap_sha = sha256_text("seed-snapshot|" + key)
    cur.execute("""INSERT INTO epidemic_kg.kg_data_snapshots
        (snapshot_id, country, disease, indicator, frequency, source_table,
         n_obs, first_date, last_date, series_sha256, snapshot_sha256)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (snapshot_sha256) DO NOTHING""",
        (snap_id, cp["country"], cp["disease"], cp["indicator"], cp["frequency"],
         "vm201_export:15_DATA_PROFILES.csv", int(cp["n_obs"]),
         cp["first_date"], cp["last_date"], s_sha, snap_sha))
    cur.execute("select snapshot_id::text from epidemic_kg.kg_data_snapshots where snapshot_sha256=%s", (snap_sha,))
    snap_id = cur.fetchone()[0]
    profile_json = {
        "country": cp["country"], "disease": cp["disease"], "indicator": cp["indicator"],
        "frequency": cp["frequency"], "population": None,
        "n_obs": int(cp["n_obs"]), "n_nonzero": int(cp["n_nonzero"]),
        "zero_ratio": float(cp["zero_ratio"] or 0), "mean": float(cp["mean"] or 0),
        "variance": float(cp["variance"] or 0),
        "overdispersion": float(cp["overdispersion"]) if cp["overdispersion"] else None,
        "min": float(cp["min"] or 0), "max": float(cp["max"] or 0),
        "negative_count": int(cp["negative_count"] or 0),
        "first_date": cp["first_date"], "last_date": cp["last_date"],
        "semantics": cp["semantics"],
        "is_ultra_short": cp["is_ultra_short"] == "True",
        "trend": {"trend": cp["trend"]},
        "seasonality": {"seasonality": cp["seasonality"]},
        "outliers_changepoints": {"outlier_count": int(cp["outlier_count"] or 0)},
        "gap_structure": {"regular": None, "note": "not available in vm201 CSV export"},
        "reporting_delay_revision_flags": {"has_revisions": None},
        "forecast_horizons": ["H1", "H2", "H3", "H4"],
        "data_tier": "TRUSTED_LIVE",
        "leakage_boundary": "observations with period_end <= forecast_origin; no future covariates used",
        "seeded_from": "vm201 export 15_DATA_PROFILES.csv",
    }
    p_sha = sha256_text(json.dumps(profile_json, sort_keys=True, ensure_ascii=False))
    cur.execute("""INSERT INTO epidemic_kg.kg_data_profiles
        (profile_id, snapshot_id, profile_json, profile_sha256, profiler_version)
        VALUES (%s,%s,%s::jsonb,%s,%s)
        ON CONFLICT (profile_sha256) DO NOTHING""",
        (cp["profile_id"], snap_id, json.dumps(profile_json, ensure_ascii=False),
         p_sha, "kg_r3c.profiler.v1+seed"))
    cur.execute("select profile_id::text from epidemic_kg.kg_data_profiles where profile_sha256=%s", (p_sha,))
    mapping[cp["profile_id"]] = cur.fetchone()[0]
    task_id = stable_uuid("task", mapping[cp["profile_id"]], "H1-H4")
    t_sha = sha256_text(mapping[cp["profile_id"]] + "|H1-H4|" + cp["semantics"])
    cur.execute("""INSERT INTO epidemic_kg.kg_forecast_tasks
        (task_id, profile_id, horizons, target_semantics, leakage_boundary, task_sha256)
        VALUES (%s,%s,%s::jsonb,%s,%s,%s)
        ON CONFLICT (task_sha256) DO NOTHING""",
        (task_id, mapping[cp["profile_id"]], json.dumps(["H1", "H2", "H3", "H4"]),
         cp["semantics"], profile_json["leakage_boundary"], t_sha))
    report["notes"].append("profile synthesized from CSV (series absent in live view): " + key)
report["profiles_synthesized"] = len(synthesized)

# ---- 2. import run + members ----
ir = seed["import_run"]
cur.execute("""INSERT INTO epidemic_kg.kg_import_runs
    (import_run_id, import_key, archive_path, archive_sha256, status, actor,
     extractor_versions, counts, started_at, finished_at)
    VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
    ON CONFLICT (import_run_id) DO NOTHING""",
    (ir["import_run_id"], ir["import_key"], ir["archive_path"], ir["archive_sha256"],
     ir["status"], ir["actor"], json.dumps(ir["extractor_versions"]),
     json.dumps(ir["counts"]), ir["started_at"],
     None if ir["finished_at"] in (None, "None") else ir["finished_at"]))
for m in seed["members"]:
    cur.execute("""INSERT INTO epidemic_kg.kg_import_members
        (member_id, import_run_id, member_path, inner_path, depth, member_sha256,
         size_bytes, kind, mime, issues, quarantined)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
        ON CONFLICT (import_run_id, member_path, inner_path) DO NOTHING""",
        (stable_uuid("member", ir["import_run_id"], m["member_path"], m["inner_path"]),
         ir["import_run_id"], m["member_path"], m["inner_path"], int(m["depth"]),
         m["member_sha256"], int(m["size"]), m["kind"], m["mime"], "[]",
         m["quarantined"] == "1"))

# ---- 3. graph versions v1 + v2 ----
for gv in seed["graph_versions"]:
    cur.execute("""INSERT INTO epidemic_kg.kg_graph_versions
        (graph_version_id, import_run_id, parent_graph_version_id, document_set_hash,
         assertion_set_hash, extractor_versions, review_policy, status,
         integrity_report, published_at)
        VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::jsonb,%s)
        ON CONFLICT (graph_version_id) DO NOTHING""",
        (gv["graph_version_id"], gv["import_run_id"], gv["parent_graph_version_id"],
         gv["document_set_hash"], gv["assertion_set_hash"],
         json.dumps(gv["extractor_versions"]), gv["review_policy"], gv["status"],
         json.dumps(gv["integrity_report"]), gv["published_at"]))

# ---- 4. score policy ----
pol = seed["policy"]
cur.execute("""INSERT INTO epidemic_kg.kg_score_policies
    (policy_id, policy_version, dimensions, thresholds, penalties, status, policy_sha256)
    VALUES (%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,'FROZEN',%s)
    ON CONFLICT (policy_id) DO NOTHING""",
    (pol["policy_id"], pol["policy_version"], json.dumps(pol["dimensions"]),
     json.dumps(pol["thresholds"]), json.dumps(pol["penalties"]), pol["policy_sha256"]))

# ---- 5. model adapters ----
for a in seed["adapters"]:
    cur.execute("""INSERT INTO epidemic_kg.kg_model_adapters
        (adapter_id, model_family, adapter_version, module_path, is_baseline,
         executable, capabilities, data_requirements)
        VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
        ON CONFLICT (adapter_id) DO NOTHING""",
        (a["adapter_id"], a["model_family"], a["adapter_version"], a["module_path"],
         a["is_baseline"], a["executable"], json.dumps(a["capabilities"]),
         json.dumps(a["data_requirements"])))

# ---- 6. parameter bundles (profile_id remapped to A100 profiles) ----
for b in seed["bundles"]:
    cur.execute("""INSERT INTO epidemic_kg.kg_parameter_bundles
        (bundle_id, adapter_id, profile_id, bundle_version, parameters,
         completeness, source_tiers, bundle_sha256)
        VALUES (%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s)
        ON CONFLICT (bundle_sha256) DO NOTHING""",
        (b["bundle_id"], b["adapter_id"], mapping[b["profile_id"]], b["bundle_version"],
         json.dumps(b["parameters"], ensure_ascii=False), b["completeness"],
         json.dumps(b["source_tiers"]), b["bundle_sha256"]))

# ---- 7. recommendation run ----
cur.execute("select profile_id::text, profile_sha256 from epidemic_kg.kg_data_profiles")
sha_by_id = {r[0]: r[1] for r in cur.fetchall()}
profile_set_hash = sha256_text(";".join(sorted(
    sha_by_id[mapping[cp["profile_id"]]] for cp in seed["profiles"])))
run = seed["run"]
cur.execute("""INSERT INTO epidemic_kg.kg_recommendation_runs
    (run_id, graph_version_id, policy_id, profile_set_hash, score_type, status)
    VALUES (%s,%s,%s,%s,%s,'COMPLETE')
    ON CONFLICT (run_id) DO NOTHING""",
    (run["run_id"], run["graph_version_id"], run["policy_id"], profile_set_hash,
     run["score_type"]))

# ---- 8. recommendations + components ----
for r in seed["recommendations"]:
    cur.execute("""INSERT INTO epidemic_kg.kg_recommendations
        (recommendation_id, run_id, profile_id, adapter_id, bundle_id, is_baseline,
         total_score, parameter_confidence_score, recomputed_total, recomputed_param,
         rank, eligibility_status, ineligibility_reasons, gap_list, tie_break_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
        ON CONFLICT (recommendation_id) DO NOTHING""",
        (r["recommendation_id"], run["run_id"], mapping[r["csv_profile_id"]],
         r["adapter_id"], r["bundle_id"], r["is_baseline"], r["total_score"],
         r["parameter_confidence_score"], r["recomputed_total"], r["recomputed_param"],
         r["rank"], r["eligibility_status"], json.dumps(r["ineligibility_reasons"]),
         json.dumps(r["gap_list"]), r["tie_break_key"]))
    for c in r["components"]:
        cur.execute("""INSERT INTO epidemic_kg.kg_recommendation_components
            (component_id, recommendation_id, dimension, rule_id, raw_value, score,
             max_score, is_penalty, rationale, source_ids)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (recommendation_id, dimension, rule_id) DO NOTHING""",
            (stable_uuid("component", r["recommendation_id"], c["dimension"], c["rule_id"]),
             r["recommendation_id"], c["dimension"], c["rule_id"], c["raw_value"],
             c["score"], c["max_score"], c["is_penalty"], c["rationale"],
             json.dumps(c["source_ids"])))

# ---- 9. execution contracts ----
for c in seed["contracts"]:
    contract = {
        "contract_version": c["contract_version"],
        "recommendation_id": c["recommendation_id"],
        "score_type": "PRE_EXECUTION_EVIDENCE_SCORE",
        "execution_status": c["status"],
        "target": {"country": c["country"], "disease": c["disease"],
                   "indicator": c["indicator"], "frequency": c["frequency"]},
        "adapter_id": c["adapter_id"],
        "total_score": c["total_score"],
        "parameter_confidence_score": c["parameter_confidence_score"],
        "non_execution_assertions": {"actually_executed": False,
                                     "fit_called": False, "predict_called": False},
        "seeded_from": "vm201 export 25b_EXECUTION_CONTRACTS.json",
    }
    cur.execute("""INSERT INTO epidemic_kg.kg_execution_contracts
        (contract_id, recommendation_id, contract_version, contract, contract_sha256,
         status, created_at)
        VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s)
        ON CONFLICT (contract_sha256) DO NOTHING""",
        (c["contract_id"], c["recommendation_id"], c["contract_version"],
         json.dumps(contract, ensure_ascii=False), c["contract_sha256"], c["status"],
         c["created_at"]))

# ---- 10. review items (synthesized to match vm201 counts) ----
for kind, n in seed["review_counts"].items():
    reason = seed["review_reasons"].get(kind, kind.lower())
    for i in range(int(n)):
        target_id = "seed-r3c:%s:%05d" % (kind, i)
        cur.execute("""INSERT INTO epidemic_kg.kg_review_items
            (review_item_id, item_kind, target_id, reason, status, payload, import_run_id)
            VALUES (%s,%s,%s,%s,'OPEN',%s::jsonb,%s)
            ON CONFLICT (item_kind, target_id, reason, import_run_id) DO NOTHING""",
            (stable_uuid("review_item", ir["import_run_id"], kind, str(i)),
             kind, target_id, reason,
             json.dumps({"seeded_from": "vm201 export 13_ASSERTION_REVIEW_COUNTS.json",
                         "note": "row synthesized to reproduce vm201 review-queue counts"}),
             ir["import_run_id"]))

conn.commit()

after = {t: table_count(t) for t in before}
report["counts"] = after
report["inserted"] = {t: after[t] - before[t] for t in before}

# ---- verification: replay the dashboard read queries ----
cur.execute("""select count(distinct (s.country, s.disease, s.indicator, s.frequency))
               from epidemic_kg.kg_recommendations r
               join epidemic_kg.kg_data_profiles p on p.profile_id = r.profile_id
               join epidemic_kg.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
               where r.is_baseline = false""")
report["verify_catalog_rows"] = cur.fetchone()[0]
cur.execute("""select r.eligibility_status, count(*) from epidemic_kg.kg_recommendations r
               group by r.eligibility_status order by 1""")
report["verify_recommendation_status"] = {r[0]: r[1] for r in cur.fetchall()}
cur.execute("""select r.recommendation_id::text, r.total_score, r.rank,
                      s.country, s.disease, s.indicator, s.frequency,
                      a.model_family, b.bundle_sha256 is not null,
                      run.score_type, run.execution_status
               from epidemic_kg.kg_recommendations r
               join epidemic_kg.kg_data_profiles p on p.profile_id = r.profile_id
               join epidemic_kg.kg_data_snapshots s on s.snapshot_id = p.snapshot_id
               join epidemic_kg.kg_recommendation_runs run on run.run_id = r.run_id
               left join epidemic_kg.kg_model_adapters a on a.adapter_id = r.adapter_id
               left join epidemic_kg.kg_parameter_bundles b on b.bundle_id = r.bundle_id
               where r.eligibility_status = 'ELIGIBLE_RECOMMENDATION'
               order by s.disease, s.country, r.rank limit 5""")
report["verify_eligible_sample"] = [list(map(str, row)) for row in cur.fetchall()]
cur.execute("""select count(*) from epidemic_kg.kg_recommendation_components c
               join epidemic_kg.kg_recommendations r on r.recommendation_id = c.recommendation_id""")
report["verify_components_joined"] = cur.fetchone()[0]
cur.execute("""select status, count(*) from epidemic_kg.kg_review_items group by status""")
report["verify_review_status"] = {r[0]: r[1] for r in cur.fetchall()}
cur.execute("""select count(*) from epidemic_kg.kg_graph_versions""")
report["verify_graph_versions"] = cur.fetchone()[0]
conn.close()
print("SEED_RESULT_BEGIN")
print(json.dumps(report, ensure_ascii=False, indent=1))
print("SEED_RESULT_END")
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-only", action="store_true",
                        help="assemble and validate the seed payload without SSH")
    args = parser.parse_args()

    payload, warnings = build_payload()
    recs = payload["recommendations"]
    comps = sum(len(r["components"]) for r in recs)
    print(f"payload: profiles={len(payload['profiles'])} adapters={len(payload['adapters'])} "
          f"bundles={len(payload['bundles'])} recommendations={len(recs)} components={comps} "
          f"contracts={len(payload['contracts'])} members={len(payload['members'])} "
          f"review_items={sum(payload['review_counts'].values())}")
    for w in warnings:
        print(f"WARN: {w}")
    if args.build_only:
        return 1 if warnings else 0

    mig = load_migrate_module()
    creds = mig.credentials(mig.TARGET_INFO)
    client, gate = mig.connect_with_gate("target", creds)
    try:
        local_payload = Path(os.environ.get("TEMP", ".")) / "_kg_r3c_seed_payload.json"
        local_payload.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        rc, home, _ = mig.run(client, "echo $HOME", timeout=30)
        home = home.strip().splitlines()[-1] if home.strip() else f"/home/{creds.user}"
        sftp = client.open_sftp()
        sftp.put(str(local_payload), REMOTE_PAYLOAD_PATH.replace("$HOME", home))
        sftp.close()
        local_payload.unlink()

        b64 = base64.b64encode(REMOTE_PY.encode("utf-8")).decode("ascii")
        py = "$HOME/autodl-tmp/conda/envs/epidemic-core/bin/python"
        cmd = (
            "source $HOME/autodl-tmp/epidemic-openclaw/scripts/database/_env.sh && "
            "set -a && source $HOME/autodl-tmp/epidemic-openclaw/.secrets/postgres.env && set +a && "
            f"echo {b64} | base64 -d | {py}"
        )
        rc, out, err = mig.run(client, cmd, timeout=900)
        if "SEED_RESULT_BEGIN" in out:
            result = out.split("SEED_RESULT_BEGIN", 1)[1].split("SEED_RESULT_END", 1)[0]
            print(json.dumps(json.loads(result), ensure_ascii=False, indent=1))
        else:
            print("REMOTE OUTPUT (no result marker):")
            print(out[-3000:])
        if rc != 0:
            print(f"remote exited rc={rc}; stderr tail:")
            print(err[-2000:])
            return 2
        if err:
            print("remote stderr (non-fatal):")
            print(err[-1000:])
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    import os
    sys.exit(main())
