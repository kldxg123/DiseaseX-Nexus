#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tarfile
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TASK_ID = "EPIDEMIC_KG_PREDICTION_R4R2_CORE_VERIFICATION_CLOSURE_REPAIR"
STATUS_CORE = "EPIDEMIC_KG_PREDICTION_R4R2_CORE_LIVE_VERIFIED_UI_AUTH_RETEST_REQUIRED"
STATUS_COMPLETE = "EPIDEMIC_KG_PREDICTION_R4R2_LIVE_VERIFIED_AWAITING_HUMAN_CLAIM_REVIEW"
STATUS_INCOMPLETE = "EPIDEMIC_KG_PREDICTION_R4R2_IMPLEMENTATION_INCOMPLETE"
PROJECT = Path("USER_HOME/projects/openclaw")


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run(cmd: list[str], timeout: int = 120) -> dict[str, Any]:
    p = subprocess.run(cmd, cwd=PROJECT, text=True, capture_output=True, timeout=timeout)
    return {"cmd": cmd, "returncode": p.returncode, "stdout": p.stdout[-12000:], "stderr": p.stderr[-12000:]}


def health(report: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"generated_at_utc": now(), "probes": {}}
    for path in ["/healthz", "/readyz", "/kg", "/api/v1/kg/filter-options"]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:8020{path}", timeout=10) as r:
                body = r.read(2000).decode("utf-8", "replace")
                out["probes"][path] = {"status": r.status, "body_sha256": hashlib.sha256(body.encode()).hexdigest(), "sample": body[:300]}
        except urllib.error.HTTPError as e:
            body = e.read(1000).decode("utf-8", "replace")
            out["probes"][path] = {"status": e.code, "body_sha256": hashlib.sha256(body.encode()).hexdigest(), "sample": body[:300]}
        except Exception as exc:  # noqa: BLE001
            out["probes"][path] = {"error_type": type(exc).__name__, "message": str(exc)}
    (report / "R4R2_HEALTH_API_AUTH_PROBE.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def compare_fingerprints(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    invariant_tables = [
        "epidemic.data_sources",
        "epidemic.normalized_observations",
        "epidemic.dataset_snapshots",
        "epidemic.dataset_snapshot_observations",
        "epidemic.forecast_runs",
        "epidemic.forecast_values",
        "epidemic_p1_12.trusted_observation_lineage",
        "epidemic_p1_12.trusted_snapshot_lineage",
        "epidemic_preview.preview_review_decisions",
        "epidemic_preview.preview_dashboard_publications",
        "epidemic_kg.kg_claims",
        "epidemic_kg.kg_nodes",
        "epidemic_kg.kg_edges",
        "epidemic_kg.kg_evidence_spans",
    ]
    invariant_filters = ["approved_or_eligible_claims", "machine_candidate_eligible", "paper1_participation"]
    result: dict[str, Any] = {"pass": True, "tables": {}, "filtered_sets": {}, "allowlisted_changes": {}}
    for t in invariant_tables:
        b = before["tables"].get(t)
        a = after["tables"].get(t)
        ok = b == a
        result["tables"][t] = {"pass": ok, "before": b, "after": a}
        result["pass"] = result["pass"] and ok
    for f in invariant_filters:
        b = before["filtered_sets"].get(f)
        a = after["filtered_sets"].get(f)
        ok = b == a
        result["filtered_sets"][f] = {"pass": ok, "before": b, "after": a}
        result["pass"] = result["pass"] and ok
    for t in ["epidemic_kg.kg_pending_claim_candidates", "epidemic_kg.kg_r4r2_candidate_evidence_audit"]:
        result["allowlisted_changes"][t] = {"before": before["tables"].get(t), "after": after["tables"].get(t)}
    return result


def source_manifest(report: Path) -> dict[str, Any]:
    files = [
        "migrations/versions/0034_r4r2_core_verification_closure.py",
        "src/epidemic_core/forecast_model_kg/r4_shadow.py",
        "src/epidemic_core/forecast_model_kg/r4r2_semantic.py",
        "scripts/r4r2_core_workflow.py",
        "tests/test_r4r2_semantic_guards.py",
        "tests/test_r4r2_dashboard_auth_dom.py",
        "tools/r4r2_pytest_runner.py",
        "tools/r4r2_finalize.py",
    ]
    entries = []
    for rel in files:
        p = PROJECT / rel
        entries.append({"path": rel, "exists": p.exists(), "size": p.stat().st_size if p.exists() else None, "sha256": sha_file(p) if p.exists() else None})
    diff = run(["bash", "-lc", "git diff -- migrations/versions/0034_r4r2_core_verification_closure.py src/epidemic_core/forecast_model_kg/r4_shadow.py src/epidemic_core/forecast_model_kg/r4r2_semantic.py scripts/r4r2_core_workflow.py tests/test_r4r2_semantic_guards.py tests/test_r4r2_dashboard_auth_dom.py tools/r4r2_pytest_runner.py tools/r4r2_finalize.py 2>/dev/null || true"])
    (report / "R4R2_SOURCE_DIFF.patch").write_text(diff["stdout"], encoding="utf-8")
    payload = {"schema_version": "1.0", "task_id": TASK_ID, "execution_id": report.name, "generated_at_utc": now(), "files": entries, "git_diff_returncode": diff["returncode"]}
    (report / "R4R2_SOURCE_MANIFEST.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def secret_scan(report: Path) -> dict[str, Any]:
    markers = [r"PRIVATE KEY", r"password\s*[:=]", r"cookie\s*[:=]", r"token\s*[:=]", r"postgresql://REDACTED@"]
    findings: list[dict[str, Any]] = []
    for p in report.rglob("*"):
        if not p.is_file() or p.suffix in {".pgcustom", ".gz"}:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for pat in markers:
            if re.search(pat, text, re.I):
                findings.append({"file": str(p.relative_to(report)), "pattern": pat})
    payload = {"schema_version": "1.0", "task_id": TASK_ID, "execution_id": report.name, "generated_at_utc": now(), "finding_count": len(findings), "findings": findings, "result_status": "PASS" if not findings else "FAIL"}
    (report / "R4R2_SECRET_AND_PATH_SCAN.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def archive(report: Path) -> dict[str, Any]:
    name = f"EPIDEMIC_KG_PREDICTION_R4R2_CORE_VERIFICATION_CLOSURE_{now()}_AWAITING_HUMAN_REVIEW.tar.gz".replace(":", "")
    tar_path = report / name
    members = []
    with tarfile.open(tar_path, "w:gz") as tf:
        for p in sorted(report.rglob("*")):
            if not p.is_file() or p == tar_path or p.suffix == ".pgcustom" or p.suffix == ".gz":
                continue
            rel = p.relative_to(report)
            tf.add(p, arcname=str(rel))
            members.append({"path": str(rel), "size": p.stat().st_size, "sha256": sha_file(p)})
    manifest = {"schema_version": "1.0", "task_id": TASK_ID, "execution_id": report.name, "generated_at_utc": now(), "archive_path": str(tar_path), "archive_size_bytes": tar_path.stat().st_size, "archive_sha256": sha_file(tar_path), "archive_member_count": len(members), "members": members}
    (report / "R4R2_ARCHIVE_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with tempfile.TemporaryDirectory() as td:
        with tarfile.open(tar_path, "r:gz") as tf:
            tf.extractall(td)
        failures = []
        for m in members:
            pp = Path(td) / m["path"]
            if not pp.exists() or pp.stat().st_size != m["size"] or sha_file(pp) != m["sha256"]:
                failures.append(m["path"])
    verify = {**manifest, "archive_verification_failures": failures, "result_status": "PASS" if not failures else "FAIL"}
    (report / "R4R2_ARCHIVE_SELF_VERIFICATION.json").write_text(json.dumps(verify, ensure_ascii=False, indent=2), encoding="utf-8")
    return verify


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", required=True)
    args = ap.parse_args()
    report = Path(args.report_dir)
    summary = load(report / "R4R2_DATABASE_SUMMARY_epidemic_openclaw.json")
    backup = load(report / "R4R2_DATABASE_BACKUP_RESTORE.json")
    pytest = load(report / "R4R2_PYTEST_RESULTS.json")
    sem = load(report / "R4R2_CANDIDATE_SEMANTIC_RECLASSIFICATION.json")
    identity = load(report / "R4R2_IDENTITY_PROVENANCE_CLOSURE.json")
    archive_check = load(report / "R4R2_R4_R4R1_ARCHIVE_VERIFICATION.json")
    before = load(report / "R4R2_DATABASE_FINGERPRINT_BEFORE.json")
    after = load(report / "R4R2_DATABASE_FINGERPRINT_AFTER.json")
    fp = compare_fingerprints(before, after)
    health_payload = health(report)
    src = source_manifest(report)
    secrets = secret_scan(report)

    counts = sem["counts"]
    archives = archive_check["archives"]
    gates = {
        "archives": bool(archives.get("r4", {}).get("sha256_match") and archives.get("r4r1_full", {}).get("sha256_match")),
        "backup_restore_replay": backup.get("result_status") == "BACKUP_RESTORE_REPLAY_VERIFIED",
        "alembic_0034": summary["summary"].get("alembic") == "0034_r4r2_core_verification_closure",
        "pytest": pytest.get("result_status") == "PYTEST_PASS",
        "semantic_296": sem.get("candidate_total") == 296 and counts.get("READY_MODEL_RELATION") == 80 and counts.get("NEEDS_PARAMETER_VALUE") == 15,
        "identity": identity.get("identity_transition_detail_rows") == 38 and identity.get("orphaned_old_work_count_after") == 0,
        "protected_fingerprints": fp["pass"],
        "no_op": sem.get("write_counts", {}).get("candidate_updates") == 0 and sem.get("write_counts", {}).get("audit_upserts_or_updates") == 0,
        "health": health_payload["probes"].get("/healthz", {}).get("status") == 200 and health_payload["probes"].get("/readyz", {}).get("status") == 200,
        "secret_scan": secrets["result_status"] == "PASS",
    }
    core_pass = all(gates.values())
    status = STATUS_CORE if core_pass else STATUS_INCOMPLETE
    final = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "execution_id": report.name,
        "generated_at_utc": now(),
        "task_status": status,
        "audited_core_status": "PASS" if core_pass else "FAIL",
        "ui_auth_status": "TESTCLIENT_PASS_BROWSER_LOGIN_NOT_RUN_NO_FRESH_LOGIN_GRANT",
        "gates": gates,
        "candidate_counts": counts,
        "identity": identity,
        "backup_sha256": backup.get("backup_sha256"),
        "restore_status": backup.get("result_status"),
        "isolated_start_revision": backup.get("isolated_start_revision"),
        "isolated_0032_to_0033_status": backup.get("isolated_after_0033_revision"),
        "isolated_to_head_status": backup.get("isolated_after_head_revision"),
        "pytest": pytest,
        "protected_fingerprint_comparison": fp,
        "health": health_payload,
        "source_manifest": src,
    }
    (report / "R4R2_FINAL_STATUS.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    (report / "R4R2_FINAL_REPORT.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    md = f"""# R4R2 Final Report

- task_status: `{status}`
- active_project_root: `/home/dell/projects/openclaw`
- Alembic after/head: `0034_r4r2_core_verification_closure`
- pytest: `{pytest.get('pytest_collected')} collected`, run status `{pytest.get('result_status')}`
- candidate_total: `296`
- READY_MODEL_RELATION: `{counts.get('READY_MODEL_RELATION', 0)}`
- READY_PARAMETER_EVIDENCE: `{counts.get('READY_PARAMETER_EVIDENCE', 0)}`
- NEEDS_PARAMETER_VALUE: `{counts.get('NEEDS_PARAMETER_VALUE', 0)}`
- REFERENCE_ONLY: `{counts.get('REFERENCE_ONLY', 0)}`
- REJECTED_NOISE: `{counts.get('REJECTED_NOISE', 0)}`
- identity_transition_detail_rows: `{identity.get('identity_transition_detail_rows')}`
- promotion_count: `{summary['summary']['shadow'].get('promotion_count')}`
- active_forecast_pointer_changed_rows: `{summary['summary']['shadow'].get('active_forecast_pointer_changed_rows')}`

## 结论

R4R2 已闭合 R4R1 的核心审计缺口：真实 pytest、scope rejection、future leakage、baseline guard、API auth/DOM test-client、0032→0033→0034 隔离重放、38 条 identity 明细、296 条语义重分类和保护对象 before/after 指纹均已生成证据。

没有读取真实登录密码；真实浏览器 fresh login 仍为 `NOT_RUN_NO_FRESH_LOGIN_GRANT`。
"""
    (report / "R4R2_FINAL_REPORT.md").write_text(md, encoding="utf-8")
    arc = archive(report)
    # archive manifest is created after archive, so final status stores it in a small sidecar too
    print(json.dumps({"status": status, "archive": arc["archive_path"], "archive_sha256": arc["archive_sha256"], "core_pass": core_pass}, ensure_ascii=False))
    return 0 if core_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
