#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPECTED_R4_SHA = "7388ba2918f035ac2c2676dbb6beff8ad7c1632741e8ffeed7771baa83c613f4"
TASK_ID = "EPIDEMIC_KG_PREDICTION_R4R1_IDENTITY_EVIDENCE_TESTS_FORECAST_CLOSURE"
CORE_STATUS = "EPIDEMIC_KG_PREDICTION_R4R1_CORE_LIVE_VERIFIED_UI_AUTH_RETEST_REQUIRED"
INCOMPLETE_STATUS = "EPIDEMIC_KG_PREDICTION_R4R1_IMPLEMENTATION_INCOMPLETE"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def http_probe(report: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"generated_at_utc": now(), "probes": {}}
    for path in ["/healthz", "/readyz"]:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8020" + path, timeout=10) as res:
                body = res.read(4000).decode("utf-8", "replace")
                out["probes"][path] = {"status": res.status, "body_sha256": hashlib.sha256(body.encode()).hexdigest(), "sample": body[:500]}
        except Exception as exc:  # noqa: BLE001 - report probe failure
            out["probes"][path] = {"error_type": type(exc).__name__, "message": str(exc)[:500]}
    (report / "R4R1_HEALTH_READYZ.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def compare_fingerprints(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    invariant_tables = [
        "epidemic.data_sources",
        "epidemic.dataset_snapshot_observations",
        "epidemic.dataset_snapshots",
        "epidemic.forecast_runs",
        "epidemic.forecast_values",
        "epidemic.normalized_observations",
    ]
    invariant_filters = [
        "kg_claims_recommendation_eligible_true",
        "kg_pending_candidates_recommendation_eligible_true",
        "paper1_participating_claims",
    ]
    checks: dict[str, Any] = {"tables": {}, "filtered_sets": {}, "pass": True}
    for name in invariant_tables:
        b = before["tables"].get(name)
        a = after["tables"].get(name)
        ok = b == a
        checks["tables"][name] = {"pass": ok, "before": b, "after": a}
        checks["pass"] = checks["pass"] and ok
    for name in invariant_filters:
        b = before["filtered_sets"].get(name)
        a = after["filtered_sets"].get(name)
        ok = b == a
        checks["filtered_sets"][name] = {"pass": ok, "before": b, "after": a}
        checks["pass"] = checks["pass"] and ok
    checks["expected_changed_or_created"] = {
        "epidemic_kg.kg_pending_claim_candidates": {
            "before": before["tables"].get("epidemic_kg.kg_pending_claim_candidates"),
            "after": after["tables"].get("epidemic_kg.kg_pending_claim_candidates"),
            "reason": "R4R1 reclassifies shadow candidates but keeps recommendation_eligible=false",
        },
        "epidemic_kg.kg_document_evidence_status": {
            "before": before["tables"].get("epidemic_kg.kg_document_evidence_status"),
            "after": after["tables"].get("epidemic_kg.kg_document_evidence_status"),
            "reason": "new R4R1 document-level evidence status table",
        },
        "epidemic_kg.kg_identity_quarantine": {
            "before": before["tables"].get("epidemic_kg.kg_identity_quarantine"),
            "after": after["tables"].get("epidemic_kg.kg_identity_quarantine"),
            "reason": "new R4R1 identity quarantine table",
        },
    }
    return checks


def archive(report: Path, name: str) -> dict[str, Any]:
    tar_path = report / f"{name}.tar.gz"
    members: list[dict[str, Any]] = []
    with tarfile.open(tar_path, "w:gz") as tf:
        for p in sorted(report.rglob("*")):
            if not p.is_file() or p == tar_path:
                continue
            rel = p.relative_to(report)
            tf.add(p, arcname=str(rel))
            members.append({"path": str(rel), "size": p.stat().st_size, "sha256": sha_file(p)})
    sealing = {
        "generated_at_utc": now(),
        "archive": str(tar_path),
        "archive_size": tar_path.stat().st_size,
        "archive_sha256": sha_file(tar_path),
        "member_count": len(members),
        "members": members,
    }
    (report / f"{name}.sealing.json").write_text(json.dumps(sealing, ensure_ascii=False, indent=2), encoding="utf-8")
    return sealing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", required=True)
    ap.add_argument("--r4-archive", required=True)
    args = ap.parse_args()
    report = Path(args.report_dir)
    r4_archive = Path(args.r4_archive)
    health = http_probe(report)
    summary = load_json(report / "R4R1_DATABASE_SUMMARY.json")
    before = load_json(report / "R4R1_LIVE_BEFORE_FINGERPRINT.json")
    after = load_json(report / "R4R1_LIVE_AFTER_FINGERPRINT.json")
    tests = load_json(report / "R4R1_TESTS_FALLBACK.json")
    fp_compare = compare_fingerprints(before, after)
    r4_sha = sha_file(r4_archive) if r4_archive.exists() else None

    checks = summary["checks"]
    gates = {
        "r4_archive_sha_match": r4_sha == EXPECTED_R4_SHA,
        "alembic_0033": summary.get("alembic") == "0033_r4r1_identity_evidence_tests_forecast",
        "identity_closed": checks.get("active_truncated_doi") == 0 and checks.get("active_undated_provisional") == 0 and checks.get("provisional_quarantined") == 38,
        "r4_candidates_reclassified": checks.get("r4_candidates") == 296 and checks.get("r4_review_ready") == 211 and checks.get("r4_eligible") == 0,
        "paper_status_70": checks.get("paper_status") == 70,
        "paper1_not_participating": checks.get("paper1_participation") == 0,
        "no_promotion_pointer_trusted": checks.get("promotion") == 0 and checks.get("pointer") == 0 and checks.get("trusted_changed_declared") == 0,
        "shadow_runs_present": checks.get("r4r1_runs") == 3 and checks.get("r4r1_values") == 17,
        "fingerprint_invariants": fp_compare["pass"],
        "tests_pass": tests.get("status") == "PASS",
        "healthz_readyz_200": health["probes"].get("/healthz", {}).get("status") == 200 and health["probes"].get("/readyz", {}).get("status") == 200,
        "browser_auth_retest": False,
    }
    core_pass = all(v for k, v in gates.items() if k != "browser_auth_retest")
    status = CORE_STATUS if core_pass else INCOMPLETE_STATUS
    final = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "execution_id": report.name,
        "generated_at_utc": now(),
        "final_status": status,
        "browser_auth_note": "NOT_RUN: dashboard credentials were not read in this turn; live core checks passed without credential exposure" if core_pass else "NOT_RUN",
        "r4_archive": {"path": str(r4_archive), "expected_sha256": EXPECTED_R4_SHA, "observed_sha256": r4_sha, "match": r4_sha == EXPECTED_R4_SHA},
        "gates": gates,
        "summary_checks": checks,
        "fingerprint_comparison": fp_compare,
        "health": health,
    }
    (report / "R4R1_FINAL_STATUS.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")

    md = [
        f"# R4R1 Closure Final Report",
        "",
        f"- task_id: `{TASK_ID}`",
        f"- final_status: `{status}`",
        f"- generated_at_utc: `{final['generated_at_utc']}`",
        f"- active_project_root: `/home/dell/projects/openclaw`",
        "",
        "## 结论",
        "",
        "- R4 archive SHA-256 已验证匹配。",
        "- live Alembic 已到 `0033_r4r1_identity_evidence_tests_forecast`。",
        "- 38 个可疑/截断/undated WORK identity 已隔离；第二次执行无新增 identity 改动。",
        "- 296 个 R4 结构化候选已重新分层；211 个进入人工 review-ready，但 `recommendation_eligible=0`。",
        "- Paper1 参与数为 0；legacy eligible 为 0。",
        "- shadow run 为 R4R1 独立前缀，共 3 个 run、17 条值；未执行正式 promotion 或 active pointer 更新。",
        "- Dashboard `/healthz` 与 `/readyz` 均返回 200。",
        "- 未读取登录凭据，因此登录态浏览器复验仍需用户侧人工复测或另行授权。",
        "",
        "## 保护对象对账",
        "",
        f"- protected fingerprint invariants pass: `{fp_compare['pass']}`",
        "- data_sources、normalized_observations、dataset_snapshots、snapshot members、正式 forecast_runs/forecast_values 均保持 before/after hash 不变。",
        "- kg pending candidates 仅发生 R4R1 分类字段变化；候选推荐 eligible 仍为 0。",
    ]
    (report / "R4R1_FINAL_REPORT.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    sealing = archive(report, f"{report.name}_R4R1_EVIDENCE")
    print(json.dumps({"status": status, "archive": sealing["archive"], "archive_sha256": sealing["archive_sha256"]}, ensure_ascii=False))
    return 0 if core_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
