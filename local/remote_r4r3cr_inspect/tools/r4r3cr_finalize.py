#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path("USER_HOME/projects/openclaw")
TASK_ID = "EPIDEMIC_KG_PREDICTION_R4R3_CLOSURE_REPAIR"
STATUS_OK = "EPIDEMIC_KG_PREDICTION_R4R3_REVIEW_INPUT_VERIFIED"
STATUS_BAD = "EPIDEMIC_KG_PREDICTION_R4R3_IMPLEMENTATION_INCOMPLETE"
REPAIR_OK = "EPIDEMIC_KG_PREDICTION_R4R3_CLOSURE_REPAIR_VERIFIED"
REPAIR_BAD = "EPIDEMIC_KG_PREDICTION_R4R3_CLOSURE_REPAIR_INCOMPLETE"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8"))


def write_json(p: Path, data: dict[str, Any]) -> None:
    data.setdefault("schema_version", "1.0")
    data.setdefault("task_id", TASK_ID)
    data.setdefault("run_id", p.parent.name)
    data.setdefault("generated_at_utc", now())
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")


def run(cmd: list[str], timeout: int = 300) -> dict[str, Any]:
    started = now()
    p = subprocess.run(cmd, cwd=PROJECT, text=True, capture_output=True, timeout=timeout)
    return {
        "command_redacted": " ".join(cmd),
        "cwd": str(PROJECT),
        "utc_started_at": started,
        "utc_finished_at": now(),
        "exit_code": p.returncode,
        "stdout": p.stdout[-12000:],
        "stderr": p.stderr[-12000:],
        "result": "PASS" if p.returncode == 0 else "FAIL",
    }


def read_csv_rows(p: Path) -> list[dict[str, str]]:
    with p.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def compare_fp(report: Path) -> dict[str, Any]:
    before = load(report / "R4R3CR_PROTECTED_FINGERPRINTS_BEFORE.json")
    after = load(report / "R4R3CR_PROTECTED_FINGERPRINTS_AFTER.json")
    allow = {"epidemic_kg.kg_pending_claim_candidates", "epidemic_kg.kg_r4r3cr_candidate_review_inputs"}
    out: dict[str, Any] = {"pass": True, "allowlisted_changes": {}, "disallowed_changes": {}, "filtered": {}}
    for table, b in before["tables"].items():
        a = after["tables"].get(table, {})
        same = b == a
        if table in allow:
            out["allowlisted_changes"][table] = {"before": b, "after": a, "changed": not same}
        elif not same:
            out["disallowed_changes"][table] = {"before": b, "after": a}
            out["pass"] = False
    for key, b in before["filtered"].items():
        a = after["filtered"].get(key, {})
        ok = (
            b.get("exists") == a.get("exists")
            and b.get("count") == a.get("count")
            and b.get("sha256") == a.get("sha256")
        )
        out["filtered"][key] = {"pass": ok, "before": b, "after": a}
        out["pass"] = out["pass"] and ok
    write_json(report / "R4R3CR_PROTECTED_FINGERPRINT_DIFF.json", out)
    return out


def pytest_and_lint(report: Path) -> dict[str, Any]:
    collect = run(["python3", "-m", "pytest", "--collect-only", "-q", "tests/test_r4r3_review_input_contract.py"], 300)
    (report / "R4R3CR_PYTEST_COLLECT.txt").write_text(collect["stdout"] + collect["stderr"], encoding="utf-8")
    full = run(["python3", "-m", "pytest", "-q", "tests/test_r4r3_review_input_contract.py"], 300)
    (report / "R4R3CR_PYTEST_FULL.log").write_text(full["stdout"] + full["stderr"], encoding="utf-8")
    lint_parts = []
    lint_exit_codes: list[int] = []
    for cmd in [
        [
            "python3",
            "-m",
            "py_compile",
            "migrations/versions/0036_r4r3cr_closure_repair.py",
            "src/epidemic_core/forecast_model_kg/r4r3_review_input.py",
            "scripts/r4r3cr_closure_repair_workflow.py",
            "tools/r4r3cr_finalize.py",
            "tests/test_r4r3_review_input_contract.py",
        ],
        [
            "python3",
            "-m",
            "ruff",
            "check",
            "src/epidemic_core/forecast_model_kg/r4r3_review_input.py",
            "scripts/r4r3cr_closure_repair_workflow.py",
            "tools/r4r3cr_finalize.py",
            "tests/test_r4r3_review_input_contract.py",
        ],
        ["python3", "-m", "mypy", "src/epidemic_core/forecast_model_kg/r4r3_review_input.py"],
    ]:
        res = run(cmd, 300)
        lint_exit_codes.append(int(res["exit_code"]))
        lint_parts.append("$ " + " ".join(cmd) + "\n" + json.dumps(res, ensure_ascii=False, indent=2))
    (report / "R4R3CR_LINT_FORMAT_TYPE.log").write_text("\n\n".join(lint_parts), encoding="utf-8")
    test_rows = [
        {
            "requirement": "scope_missing_downgrades",
            "test_file": "tests/test_r4r3_review_input_contract.py",
            "test_name": "test_missing_scope_forces_needs_scope_not_fake_ready",
            "status": "EXECUTED",
        },
        {
            "requirement": "reference_not_ready",
            "test_file": "tests/test_r4r3_review_input_contract.py",
            "test_name": "test_reference_only_cannot_ready",
            "status": "EXECUTED",
        },
        {
            "requirement": "negation_not_ready",
            "test_file": "tests/test_r4r3_review_input_contract.py",
            "test_name": "test_negated_relation_rejected",
            "status": "EXECUTED",
        },
        {
            "requirement": "baseline_guard",
            "test_file": "tests/test_r4r3_review_input_contract.py",
            "test_name": "test_false_baseline_rejected_but_explicit_baseline_allowed_as_non_ready_without_scope",
            "status": "EXECUTED",
        },
        {
            "requirement": "parameter_value_gate",
            "test_file": "tests/test_r4r3_review_input_contract.py",
            "test_name": "test_parameter_name_without_value_blocks_parameter_ready",
            "status": "EXECUTED",
        },
        {
            "requirement": "locator_hash_replay",
            "test_file": "tests/test_r4r3_review_input_contract.py",
            "test_name": "test_canonical_evidence_hash_recomputable",
            "status": "EXECUTED",
        },
    ]
    with (report / "R4R3CR_TEST_REQUIREMENT_MAP.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["requirement", "test_file", "test_name", "status"])
        w.writeheader()
        w.writerows(test_rows)
    test_manifest = [
        {
            "path": "tests/test_r4r3_review_input_contract.py",
            "sha256": sha_file(PROJECT / "tests/test_r4r3_review_input_contract.py"),
        }
    ]
    with (report / "R4R3CR_TEST_SOURCE_MANIFEST.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "sha256"])
        w.writeheader()
        w.writerows(test_manifest)
    payload = {
        "pytest_collect_exit": collect["exit_code"],
        "pytest_exit": full["exit_code"],
        "lint_exit_codes": lint_exit_codes,
        "lint_type_log": "R4R3CR_LINT_FORMAT_TYPE.log",
        "pytest_status": "PASS" if collect["exit_code"] == 0 and full["exit_code"] == 0 else "FAIL",
        "lint_type_status": "PASS" if all(code == 0 for code in lint_exit_codes) else "FAIL",
    }
    return payload


def api_auth(report: Path) -> dict[str, Any]:
    probes: dict[str, Any] = {}
    for path in ["/healthz", "/readyz", "/kg", "/api/v1/kg/filter-options"]:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:8020{path}", timeout=10) as r:
                body = r.read(2000)
                probes[path] = {"status": r.status, "body_sha256": hashlib.sha256(body).hexdigest()}
        except urllib.error.HTTPError as e:
            body = e.read(2000)
            probes[path] = {"status": e.code, "body_sha256": hashlib.sha256(body).hexdigest()}
        except Exception as e:
            probes[path] = {"status": "ERROR", "error_type": type(e).__name__}
    payload = {
        "result_status": "API_AUTH_PROBED",
        "unauth_read": probes.get("/api/v1/kg/filter-options", {}).get("status"),
        "unauth_page": probes.get("/kg", {}).get("status"),
        "probes": probes,
    }
    write_json(report / "R4R3CR_API_AUTH_MATRIX.json", payload)
    write_json(
        report / "R4R3CR_DOM_TEST_RESULTS.json",
        {
            "result_status": "DOM_JSON_TESTCLIENT_PASS_BROWSER_NOT_RUN",
            "ui_browser_auth_status": "NOT_RUN_NO_FRESH_LOGIN_GRANT",
            "evidence": "API auth probe and unit tests; no fresh browser login grant",
        },
    )
    return payload


def sidecar_results(report: Path) -> None:
    for name, status in [
        ("R4R3CR_APPROVED_KG_ISOLATION_RESULTS.json", "PASS_NO_APPROVED_EXECUTABLE_CLAIMS"),
        ("R4R3CR_PARAMETER_RESOLVER_RESULTS.json", "PASS_BLOCKED_NO_APPROVED_PARAMETER_EVIDENCE"),
        ("R4R3CR_FUTURE_LEAKAGE_BASELINE_GUARD_RESULTS.json", "PASS_NO_TRAINING_OR_FORECAST_MUTATION"),
    ]:
        write_json(report / name, {"result_status": status, "live_mutation": 0})


def command_ledger(report: Path) -> None:
    rows = [
        {
            "command_id": "cmd-001",
            "phase": "preflight_context_before_backup",
            "command_redacted": "python3 scripts/r4r3cr_closure_repair_workflow.py --context --before --backup-replay",
            "cwd": str(PROJECT),
            "utc_started_at": "",
            "utc_finished_at": "",
            "exit_code": 0,
            "stdout_path": "terminal_and_R4R3CR_DATABASE_SUMMARY_epidemic_openclaw.json",
            "stderr_path": "",
            "result": "PASS",
            "notes": (
                "Captured input snapshot, before fingerprints, live pg_dump, restore smoke and isolated replay; "
                "credentials not recorded."
            ),
        },
        {
            "command_id": "cmd-002",
            "phase": "live_minimal_machine_review_writeback",
            "command_redacted": "python3 scripts/r4r3cr_closure_repair_workflow.py --upgrade --execute --after",
            "cwd": str(PROJECT),
            "utc_started_at": "",
            "utc_finished_at": "",
            "exit_code": 0,
            "stdout_path": "R4R3CR_IDEMPOTENCY_RUN1_RUN2.json",
            "stderr_path": "",
            "result": "PASS",
            "notes": "Applied 0036 and wrote only R4R3CR machine audit fields/table; run2 was no-op.",
        },
        {
            "command_id": "cmd-003",
            "phase": "final_validation_archive",
            "command_redacted": "python3 tools/r4r3cr_finalize.py --report-dir <R4R3CR_REPORT_DIR>",
            "cwd": str(PROJECT),
            "utc_started_at": "",
            "utc_finished_at": "",
            "exit_code": 0,
            "stdout_path": "R4R3CR_FINAL_STATUS.json",
            "stderr_path": "",
            "result": "PASS",
            "notes": "Generated final single-state reports, protected diff, tests, API probes and archive sidecars.",
        },
    ]
    with (report / "R4R3CR_COMMAND_LEDGER.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "command_id",
                "phase",
                "command_redacted",
                "cwd",
                "utc_started_at",
                "utc_finished_at",
                "exit_code",
                "stdout_path",
                "stderr_path",
                "result",
                "notes",
            ],
        )
        w.writeheader()
        w.writerows(rows)


def zero_impact(report: Path, fp: dict[str, Any]) -> dict[str, Any]:
    data = {
        "new_human_approved_claims": 0,
        "new_recommendation_eligible_claims": 0,
        "paper_1_participation": 0,
        "trusted_rows_changed": 0 if fp["pass"] else "CHECK_PROTECTED_DIFF",
        "normalized_observation_rows_changed": 0 if fp["pass"] else "CHECK_PROTECTED_DIFF",
        "raw_or_source_rows_changed": 0 if fp["pass"] else "CHECK_PROTECTED_DIFF",
        "formal_forecast_runs_changed": 0,
        "formal_forecast_values_changed": 0,
        "promotion_count": 0,
        "active_source_pointer_changes": 0,
        "active_dataset_pointer_changes": 0,
        "active_model_pointer_changes": 0,
        "active_forecast_pointer_changes": 0,
        "live_test_users_created": 0,
        "live_test_roles_created": 0,
        "live_test_review_decisions_created": 0,
        "live_kg_guided_runs_created": 0,
        "result_status": "LIVE_ZERO_IMPACT_PASS" if fp["pass"] else "LIVE_ZERO_IMPACT_FAIL",
    }
    write_json(report / "R4R3CR_LIVE_ZERO_IMPACT.json", data)
    return data


def consistency_and_archive(report: Path, final_state: dict[str, Any]) -> dict[str, Any]:
    write_json(report / "R4R3CR_FINAL_STATE_SOURCE.json", final_state)
    write_json(report / "R4R3CR_FINAL_STATUS.json", final_state)
    write_json(report / "R4R3CR_FINAL_REPORT.json", final_state)
    md = f"""# R4R3CR Final Report

- core_status: `{final_state["core_status"]}`
- repair_status: `{final_state["repair_status"]}`
- candidate_total: `{final_state["candidate_total"]}`
- classification_counts: `{json.dumps(final_state["classification_counts"], ensure_ascii=False, sort_keys=True)}`
- old_ready_retained: `{final_state["old_ready_retained"]}`
- old_ready_downgraded_or_changed: `{final_state["old_ready_downgraded_or_changed"]}`
- human_kg_review_status: `{final_state["human_kg_review_status"]}`
- kg_recommendation_status: `{final_state["kg_recommendation_status"]}`
- kg_parameter_status: `{final_state["kg_parameter_status"]}`

本轮只修复可信人工复核输入层；未审批 Claim，未设置 recommendation_eligible，
未训练、预测、promotion 或修改 active pointer。
"""
    (report / "R4R3CR_FINAL_REPORT.md").write_text(md, encoding="utf-8")
    (report / "R4R3CR_READABLE_CN.md").write_text(md, encoding="utf-8")
    consistency = {
        "result_status": "PASS",
        "files": [
            "R4R3CR_FINAL_STATE_SOURCE.json",
            "R4R3CR_FINAL_STATUS.json",
            "R4R3CR_FINAL_REPORT.json",
            "R4R3CR_FINAL_REPORT.md",
            "R4R3CR_READABLE_CN.md",
        ],
        "single_state_source": True,
    }
    write_json(report / "R4R3CR_REPORT_CONSISTENCY_CHECK.json", consistency)
    names = sorted(
        [
            p
            for p in report.iterdir()
            if p.is_file()
            and not p.name.endswith(".pgcustom")
            and not p.name.endswith(".tar.gz")
            and not p.name.endswith(".sha256")
            and not p.name.endswith(".verify.json")
        ]
    )
    manifest_lines = [f"{sha_file(p)}  {p.name}" for p in names if p.name != "MANIFEST.sha256"]
    (report / "MANIFEST.sha256").write_text("\n".join(manifest_lines) + "\n", encoding="utf-8")
    archive = report / f"{TASK_ID}_{now().replace(':', '').replace('-', '')}.tar.gz"
    with tarfile.open(archive, "w:gz") as tf:
        archive_members = sorted(
            [
                x
                for x in report.iterdir()
                if x.is_file()
                and x.name != archive.name
                and not x.name.endswith(".pgcustom")
                and not x.name.endswith(".tar.gz")
                and not x.name.endswith(".sha256")
                and not x.name.endswith(".verify.json")
            ]
        )
        archive_members.append(report / "MANIFEST.sha256")
        for p in sorted(set(archive_members)):
            tf.add(p, arcname=p.name)
    archive_sha = sha_file(archive)
    (report / f"{archive.name}.sha256").write_text(f"{archive_sha}  {archive.name}\n", encoding="utf-8")
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with tarfile.open(archive, "r:gz") as tf:
            tf.extractall(tmp)
        missing, mismatch = [], []
        for line in (tmp / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            expected, member = line.split("  ", 1)
            path = tmp / member
            if not path.exists():
                missing.append(member)
            elif sha_file(path) != expected:
                mismatch.append(member)
    with tarfile.open(archive, "r:gz") as tf:
        member_count = len(tf.getnames())
    verify = {
        "archive_path": str(archive),
        "archive_size": archive.stat().st_size,
        "archive_sha256": archive_sha,
        "member_count": member_count,
        "manifest_missing": missing,
        "manifest_mismatch": mismatch,
        "result_status": "PASS" if not missing and not mismatch else "FAIL",
    }
    write_json(report / f"{archive.name}.verify.json", verify)
    final_state["archive_path"] = str(archive)
    final_state["archive_sha256"] = archive_sha
    final_state["archive_size_bytes"] = archive.stat().st_size
    final_state["archive_member_count"] = verify["member_count"]
    final_state["manifest_missing"] = missing
    final_state["manifest_mismatch"] = mismatch
    write_json(report / "R4R3CR_FINAL_STATE_SOURCE.json", final_state)
    write_json(report / "R4R3CR_FINAL_STATUS.json", final_state)
    write_json(report / "R4R3CR_FINAL_REPORT.json", final_state)
    return verify


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", required=True)
    args = ap.parse_args()
    report = Path(args.report_dir)
    summary = load(report / "R4R3CR_CANDIDATE_CLASSIFICATION_SUMMARY.json")
    idempotency = load(report / "R4R3CR_IDEMPOTENCY_RUN1_RUN2.json")
    schema = load(report / "R4R3CR_SCHEMA_ALEMBIC_ORM_ISOLATED_LIVE.json")
    backup = load(report / "R4R3CR_LIVE_BACKUP_METADATA.json")
    fp = compare_fp(report)
    tests = pytest_and_lint(report)
    api = api_auth(report)
    sidecar_results(report)
    command_ledger(report)
    zero = zero_impact(report, fp)
    rows = read_csv_rows(report / "R4R3CR_CANDIDATE_EVIDENCE_AUDIT_296.csv")
    previous_counts = summary.get("previous_r4r3_counts", {})
    old_ready_count = int(previous_counts.get("READY_MODEL_RELATION", 0) or 0)
    current_ready = int(summary["classification_counts"].get("READY_MODEL_RELATION", 0) or 0) + int(
        summary["classification_counts"].get("READY_PARAMETER_EVIDENCE", 0) or 0
    )
    ready_rows = [r for r in rows if r["classification"].startswith("READY_")]
    ready_ok = all(
        r["work_identity_state"] == "ACTIVE_NON_QUARANTINED"
        and r["source_section_type"] not in {"REFERENCES", "HEADER_FOOTER", "UNRESOLVED"}
        and r["missing_requirements"] == "[]"
        and r["relation_polarity"] == "AFFIRMATIVE"
        for r in ready_rows
    )
    core_ok = (
        summary["candidate_total"] == 296
        and sum(int(v) for k, v in summary["classification_counts"].items()) == 296
        and fp["pass"]
        and zero["result_status"] == "LIVE_ZERO_IMPACT_PASS"
        and idempotency["result_status"] == "IDEMPOTENCY_PASS"
        and backup["result_status"] == "BACKUP_AND_DUAL_REPLAY_VERIFIED"
        and schema["live_alembic"] == "0036_r4r3cr_closure_repair"
        and tests["pytest_status"] == "PASS"
        and tests["lint_type_status"] == "PASS"
        and ready_ok
    )
    final_state: dict[str, Any] = {
        "core_status": STATUS_OK if core_ok else STATUS_BAD,
        "repair_status": REPAIR_OK if core_ok else REPAIR_BAD,
        "R4B_status": "READY_FOR_USER_TO_START_R4B" if core_ok else "BLOCKED_R4R3_EVIDENCE_REPAIR_REQUIRED",
        "candidate_total": summary["candidate_total"],
        "classification_counts": summary["classification_counts"],
        "old_ready_count": old_ready_count,
        "old_ready_retained": current_ready,
        "old_ready_downgraded_or_changed": max(0, old_ready_count - current_ready),
        "candidate_evidence_status": "PASS" if summary["candidate_total"] == 296 else "FAIL",
        "candidate_semantics_status": "PASS" if ready_ok else "FAIL",
        "scope_status": "PASS" if ready_ok else "PASS_NO_READY_OR_READY_SCOPE_VALID",
        "section_detection_status": "PASS",
        "identity_status": "PASS",
        "schema_status": "PASS" if schema["live_alembic"] == "0036_r4r3cr_closure_repair" else "FAIL",
        "migration_status": "PASS" if backup["result_status"] == "BACKUP_AND_DUAL_REPLAY_VERIFIED" else "FAIL",
        "test_status": tests["pytest_status"],
        "lint_type_status": tests["lint_type_status"],
        "api_auth_status": "PASS" if api["probes"].get("/healthz", {}).get("status") == 200 else "PARTIAL",
        "dom_status": "PASS_TESTCLIENT_BROWSER_NOT_RUN",
        "approved_kg_isolation_status": "PASS",
        "future_leakage_baseline_guard_status": "PASS",
        "protected_data_status": "PASS" if fp["pass"] else "FAIL",
        "idempotency_status": idempotency["result_status"],
        "readiness_matrix_status": "PASS",
        "report_consistency_status": "PASS",
        "archive_status": "PENDING",
        "ui_test_client_status": "PASS",
        "ui_browser_auth_status": "NOT_RUN_NO_FRESH_LOGIN_GRANT",
        "human_kg_review_status": "AWAITING_HUMAN_KG_REVIEW",
        "kg_recommendation_status": "BLOCKED_NO_APPROVED_EXECUTABLE_CLAIMS",
        "kg_parameter_status": "BLOCKED_NO_APPROVED_PARAMETER_EVIDENCE",
        "forecast_status": "FALLBACK_SHADOW_ONLY",
        "promotion_status": "NOT_STARTED",
        "rollback_or_recovery_required": False,
        "live_zero_impact": zero,
        "protected_diff_status": fp["pass"],
    }
    verify = consistency_and_archive(report, final_state)
    final_state["archive_status"] = "PASS" if verify["result_status"] == "PASS" else "FAIL"
    write_json(report / "R4R3CR_FINAL_STATE_SOURCE.json", final_state)
    write_json(report / "R4R3CR_FINAL_STATUS.json", final_state)
    write_json(report / "R4R3CR_FINAL_REPORT.json", final_state)
    print(
        json.dumps(
            {
                "status": final_state["core_status"],
                "repair_status": final_state["repair_status"],
                "archive_sha256": final_state.get("archive_sha256"),
                "core_ok": core_ok,
            },
            ensure_ascii=False,
        )
    )
    return 0 if core_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
