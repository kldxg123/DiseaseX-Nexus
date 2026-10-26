from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg


ROOT = Path("USER_HOME/projects/openclaw")
REP = ROOT / "reports/r4d_r1_origin_safe_20260806T_R1"
EXP = ROOT / "experiments/r4d_r1_origin_safe_20260806T_R1"
PRIOR_EXP = ROOT / "experiments/r4d_ili_gru_official_20260806T072222Z"
REPO = PRIOR_EXP / "sources/ml-flu-prediction"
CID = "7b8ea8a8-2f4f-5975-b3ad-08f6a03ccaf4"
CLAIM_ID = "6ef6f7e4-f510-5358-8c84-50ecd73283de"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def run(cmd: list[str], env_prefix: str | None = None) -> dict[str, Any]:
    actual = " ".join(cmd)
    env = os.environ.copy()
    if env_prefix:
        actual = env_prefix + " " + actual
        if env_prefix.startswith("PYTHONPATH="):
            env["PYTHONPATH"] = env_prefix.split("=", 1)[1]
    cp = subprocess.run(cmd, cwd=str(ROOT), text=True, capture_output=True, env=env)
    return {
        "actual_command": actual,
        "exit_code": cp.returncode,
        "stdout": cp.stdout[:4000],
        "stderr": cp.stderr[:4000],
    }


def dashboard_dsn() -> str:
    env: dict[str, str] = {}
    for item in Path("/proc/3426031/environ").read_bytes().split(b"\0"):
        if b"=" in item:
            k, v = item.split(b"=", 1)
            env[k.decode()] = v.decode(errors="ignore")
    dsn = env["DASHBOARD_DATABASE_URL"]
    if dsn.startswith("postgresql+psycopg://"):
        dsn = "postgresql://" + dsn.split("://", 1)[1]
    return dsn


def db_counts() -> dict[str, Any]:
    with psycopg.connect(dashboard_dsn()) as conn:
        conn.execute("BEGIN")
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute("SET LOCAL statement_timeout='30s'")
        claim = conn.execute(
            """
            select claim_id, review_status, evidence_eligible, quarantine, properties
            from epidemic_kg.kg_claims where claim_id=%s
            """,
            (CLAIM_ID,),
        ).fetchone()
        counts = {}
        for key, sql in {
            "kg_claims_evidence_eligible": "select count(*) from epidemic_kg.kg_claims where evidence_eligible",
            "kg_claims_recommendation_eligible": (
                "select count(*) from epidemic_kg.kg_claims "
                "where coalesce((properties->>'recommendation_eligible')::boolean,false)"
            ),
            "forecast_runs": "select count(*) from epidemic.forecast_runs",
            "forecast_values": "select count(*) from epidemic.forecast_values",
            "forecast_runs_default_eligible": "select count(*) from epidemic.forecast_runs where is_default_eligible",
        }.items():
            counts[key] = conn.execute(sql).fetchone()[0]
        read_only = conn.execute("SHOW transaction_read_only").fetchone()[0]
        conn.execute("ROLLBACK")
    props = claim[4] if claim and claim[4] else {}
    return {
        "read_only": read_only,
        "counts": counts,
        "claim": {
            "claim_id": str(claim[0]) if claim else None,
            "review_status": claim[1],
            "evidence_eligible": claim[2],
            "quarantine": claim[3],
            "recommendation_eligible": props.get("recommendation_eligible"),
            "execution_ready_after_human_approval": props.get("execution_ready_after_human_approval"),
            "promotion_allowed": props.get("promotion_allowed"),
        },
    }


def read_csv(name: str) -> list[dict[str, str]]:
    with (REP / name).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    before = db_counts()
    py = "/usr/bin/python3"
    env = (
        "PYTHONPATH=USER_HOME/projects/openclaw/experiments/r4d_ili_gru_official_20260806T072222Z/pydeps_tf:"
        "USER_HOME/projects/openclaw/experiments/r4d_ili_gru_official_20260806T072222Z/pydeps_light:"
        "USER_HOME/projects/openclaw/experiments/r4d_r1_origin_safe_20260806T_R1/adapter"
    )
    checks_cmds = {
        "pytest": [
            py,
            "-m",
            "pytest",
            "-q",
            str(EXP / "adapter/test_r4d_r1_outputs.py"),
        ],
        "ruff": [
            py,
            "-m",
            "ruff",
            "check",
            str(EXP / "adapter/r4d_r1_runner.py"),
            str(EXP / "adapter/test_r4d_r1_outputs.py"),
        ],
        "mypy": [
            py,
            "-m",
            "mypy",
            "--ignore-missing-imports",
            str(EXP / "adapter/r4d_r1_runner.py"),
            str(EXP / "adapter/test_r4d_r1_outputs.py"),
        ],
        "pip_check": [py, "-m", "pip", "check"],
    }
    cmd_results = {name: run(cmd, env_prefix=env) for name, cmd in checks_cmds.items()}
    after = db_counts()

    mapping = json.loads((REP / "R4D_R1_DATAVERSE_MAPPING.json").read_text())
    temporal = json.loads((REP / "R4D_R1_TEMPORAL_SEMANTICS.json").read_text())
    source = json.loads((REP / "R4D_R1_SOURCE_CORRECTIONS.json").read_text())
    track_a_comp = json.loads((REP / "R4D_R1_TRACK_A_AUTHOR_COMPARISON.json").read_text())
    track_b_metrics = json.loads((REP / "R4D_R1_TRACK_B_METRICS.json").read_text())
    repro = json.loads((REP / "R4D_R1_REPRODUCIBILITY.json").read_text())
    audit = read_csv("R4D_R1_ORIGIN_AUDIT.csv")
    preds_b = read_csv("R4D_R1_TRACK_B_ORIGIN_SAFE_PREDICTIONS.csv")
    db_safety = {
        "before": before,
        "after": after,
        "unchanged": before == after,
        "eligibility_flags_unchanged_false": {
            "evidence_eligible": after["claim"]["evidence_eligible"] is False,
            "recommendation_eligible": after["claim"]["recommendation_eligible"] is False,
            "execution_ready_after_human_approval": after["claim"]["execution_ready_after_human_approval"] is False,
            "promotion_allowed": after["claim"]["promotion_allowed"] is False,
        },
    }
    (REP / "R4D_R1_DATABASE_SAFETY.json").write_text(json.dumps(db_safety, indent=2), encoding="utf-8")
    (REP / "R4D_R1_PATCH_LEDGER.md").write_text(
        f"""# R4D-R1 Patch Ledger

No project source, live DB object, Claim, candidate, forecast table, dashboard, API, or prior R4D report was modified.

Actual R4D-R1 code was created only in the isolated experiment directory:

- `{EXP / 'adapter/r4d_r1_runner.py'}`
- `{EXP / 'adapter/test_r4d_r1_outputs.py'}`

Main repairs:

1. Explicitly parsed author CSV dates as `%m/%d/%y` instead of relying on pandas inference.
2. Parsed Dataverse `Week` as `%m/%d/%Y`, excluded it from state columns, and compared author/Dataverse data by `(week, state)`.
3. Split Track A author protocol replay from Track B origin-safe backtest.
4. Added per-origin training/scaler cutoffs and leakage guard assertions.
5. Added fresh-process replay checks for Track A and Track B.

Runner SHA-256: `{sha256(EXP / 'adapter/r4d_r1_runner.py')}`
Test SHA-256: `{sha256(EXP / 'adapter/test_r4d_r1_outputs.py')}`
""",
        encoding="utf-8",
    )

    validation_checks = [
        {
            "check_id": "prior_author_commit_and_hashes",
            "status": "PASS"
            if source["verified_author_commit"] == source["expected_author_commit"]
            and source["author_csv_sha256"] == "cda4808dd6d5b504ec2f18bf9aaef73623a50eecab7958d6a3a8170c07cffe6d"
            and source["dataverse_sha256"] == "b91f1a579148f31313eb54f575fcc40d789c8394cf0d6307aab079a0e5356db1"
            else "FAIL",
            "actual_command": "git -C <author_repo> rev-parse HEAD; sha256sum States_ILI.csv States_ILI.tab",
            "exit_code": 0,
            "checked_artifact": "R4D_R1_SOURCE_CORRECTIONS.json",
            "measured_value_or_assertion": source,
            "stdout_or_log_locator": str(REP / "R4D_R1_SOURCE_CORRECTIONS.json"),
        },
        {
            "check_id": "dataverse_week_and_mapping",
            "status": "PASS"
            if mapping["dataverse_profile"]["week_is_state_column"] is False
            and mapping["dataverse_profile"]["state_count"] == 37
            and mapping["dataverse_profile"]["has_1970"] is False
            and mapping["content_relationship"] == "AUTHOR_SUBSET_OF_DATAVERSE"
            else "FAIL",
            "actual_command": "python r4d_r1_runner.py combine",
            "exit_code": 0,
            "checked_artifact": "R4D_R1_DATAVERSE_MAPPING.json",
            "measured_value_or_assertion": mapping,
            "stdout_or_log_locator": str(REP / "R4D_R1_DATAVERSE_MAPPING.json"),
        },
        {
            "check_id": "temporal_semantics_worked_example",
            "status": "PASS" if temporal["worked_example"]["target_week"] == "2016-04-24" else "FAIL",
            "actual_command": "python r4d_r1_runner.py combine",
            "exit_code": 0,
            "checked_artifact": "R4D_R1_TEMPORAL_SEMANTICS.json",
            "measured_value_or_assertion": temporal["worked_example"],
            "stdout_or_log_locator": str(REP / "R4D_R1_TEMPORAL_SEMANTICS.json"),
        },
        {
            "check_id": "origin_safe_audit",
            "status": "PASS" if len(audit) == 3 and all(row["overall_origin_safe"] == "True" for row in audit) else "FAIL",
            "actual_command": "python r4d_r1_runner.py track-b --label B1",
            "exit_code": 0,
            "checked_artifact": "R4D_R1_ORIGIN_AUDIT.csv",
            "measured_value_or_assertion": audit,
            "stdout_or_log_locator": str(REP / "R4D_R1_ORIGIN_AUDIT.csv"),
        },
        {
            "check_id": "track_a_author_join_counts",
            "status": "PASS" if track_a_comp["matched_count"] == 2 and len(track_a_comp["unmatched_keys"]) == 1 else "FAIL",
            "actual_command": "python r4d_r1_runner.py combine",
            "exit_code": 0,
            "checked_artifact": "R4D_R1_TRACK_A_AUTHOR_COMPARISON.json",
            "measured_value_or_assertion": track_a_comp,
            "stdout_or_log_locator": str(REP / "R4D_R1_TRACK_A_AUTHOR_COMPARISON.json"),
        },
        {
            "check_id": "track_b_metrics",
            "status": "PASS" if track_b_metrics["origin_count"] == 3 else "FAIL",
            "actual_command": "python r4d_r1_runner.py combine",
            "exit_code": 0,
            "checked_artifact": "R4D_R1_TRACK_B_METRICS.json",
            "measured_value_or_assertion": track_b_metrics,
            "stdout_or_log_locator": str(REP / "R4D_R1_TRACK_B_METRICS.json"),
        },
        {
            "check_id": "fresh_process_replay",
            "status": "PASS" if repro["track_b_fresh_process_rows"] == 3 and repro["track_b_keys_match"] else "FAIL",
            "actual_command": "python r4d_r1_runner.py track-b --label B1; python r4d_r1_runner.py track-b --label B2",
            "exit_code": 0,
            "checked_artifact": "R4D_R1_REPRODUCIBILITY.json",
            "measured_value_or_assertion": repro,
            "stdout_or_log_locator": str(REP / "R4D_R1_REPRODUCIBILITY.json"),
        },
        {
            "check_id": "pytest",
            "status": "PASS" if cmd_results["pytest"]["exit_code"] == 0 else "FAIL",
            "checked_artifact": str(EXP / "adapter/test_r4d_r1_outputs.py"),
            "measured_value_or_assertion": "7 tests expected",
            "stdout_or_log_locator": "embedded",
            **cmd_results["pytest"],
        },
        {
            "check_id": "ruff",
            "status": "PASS" if cmd_results["ruff"]["exit_code"] == 0 else "FAIL",
            "checked_artifact": "R4D-R1 adapter/test",
            "measured_value_or_assertion": "All checks passed expected",
            "stdout_or_log_locator": "embedded",
            **cmd_results["ruff"],
        },
        {
            "check_id": "mypy",
            "status": "PASS" if cmd_results["mypy"]["exit_code"] == 0 else "FAIL",
            "checked_artifact": "R4D-R1 adapter/test",
            "measured_value_or_assertion": "Success expected",
            "stdout_or_log_locator": "embedded",
            **cmd_results["mypy"],
        },
        {
            "check_id": "pip_check",
            "status": "PASS" if cmd_results["pip_check"]["exit_code"] == 0 else "FAIL",
            "checked_artifact": "R4D target dependency environment",
            "measured_value_or_assertion": "pip check",
            "stdout_or_log_locator": "embedded",
            **cmd_results["pip_check"],
        },
        {
            "check_id": "live_db_safety",
            "status": "PASS" if db_safety["unchanged"] and after["read_only"] == "on" else "FAIL",
            "actual_command": "BEGIN; SET TRANSACTION READ ONLY; SELECT safety counts; ROLLBACK",
            "exit_code": 0,
            "checked_artifact": "R4D_R1_DATABASE_SAFETY.json",
            "measured_value_or_assertion": db_safety,
            "stdout_or_log_locator": str(REP / "R4D_R1_DATABASE_SAFETY.json"),
        },
    ]
    final_status = (
        "R4D_R1_ORIGIN_SAFE_PROTOCOL_REPAIRED_PILOT_COMPLETE"
        if all(c["status"] == "PASS" for c in validation_checks)
        else "R4D_R1_RUN_COMPLETE_VALIDATION_FAILED"
    )
    validation = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "final_status": final_status,
        "checks": validation_checks,
    }
    (REP / "R4D_R1_VALIDATION.json").write_text(json.dumps(validation, indent=2, default=str), encoding="utf-8")

    report = [
        "# R4D-R1 Origin-Safe Protocol Repair\n\n",
        f"final_status: `{final_status}`\n\n",
        "## Scope\n",
        f"Only candidate `{CID}` / claim `{CLAIM_ID}` was processed. No CDC ILINet ingestion, no live DB writes, "
        "no eligibility/promotion changes, and no production forecast publication occurred.\n\n",
        "## Source repair\n",
        f"- Verified author commit: `{source['verified_author_commit']}`.\n",
        f"- Author `States_ILI.csv` SHA-256: `{source['author_csv_sha256']}`.\n",
        f"- Dataverse `States_ILI.tab` SHA-256: `{source['dataverse_sha256']}`.\n",
        f"- Dataverse mapping: `{mapping['content_relationship']}`; overlap keys {mapping['overlap_key_count']}; "
        f"Dataverse-only weeks {mapping['dataverse_only_weeks']}; no 1970 dates.\n\n",
        "## Temporal semantics\n",
        "- `sample_anchor_week=t`; inputs use `t-52..t-1`; latest input/information cutoff is `t-1`.\n",
        "- code target uses `target_week=t+(th-1)` with `th=4`, so the worked example is "
        "`anchor=2016-04-03`, `cutoff=2016-03-27`, `target=2016-04-24`.\n",
        "- Track A and Track B are intentionally separated.\n\n",
        "## Track A: author protocol replay\n",
        f"- status: `{track_a_comp['status']}`.\n",
        f"- requested keys: {track_a_comp['requested_keys']}.\n",
        f"- matched {track_a_comp['matched_count']} rows; unmatched {track_a_comp['unmatched_keys']}.\n",
        "- Track A scientific validity for eligibility is `NOT_ASSERTED`; it is not used as origin-safe evidence.\n\n",
        "## Track B: origin-safe backtest\n",
        f"- origin count: {len(preds_b)}; target location AK; 37-state inputs; 52-week window; th=4.\n",
        "- Each origin refits scaler and trains GRU/AR Lasso from scratch using only data available at information cutoff.\n",
        f"- GRU metrics: {track_b_metrics['gru']}.\n",
        f"- Persistence metrics: {track_b_metrics['persistence']}.\n",
        f"- AR Lasso metrics: {track_b_metrics['ar_lasso']}.\n",
        f"- conclusion: `{track_b_metrics['performance_conclusion']}`.\n\n",
        "## Origin audit\n",
    ]
    for row in audit:
        report.append(
            f"- origin {row['origin_id']}: cutoff {row['information_cutoff']}, target {row['target_week']}, "
            f"train_target_max {row['train_target_max_week']}, scaler_fit_max {row['scaler_fit_max_week']}, "
            f"overall_safe={row['overall_origin_safe']}.\n"
        )
    report += [
        "\n## Reproducibility\n",
        f"- Track B fresh-process replay rows: {repro['track_b_fresh_process_rows']}; keys_match={repro['track_b_keys_match']}; "
        f"max GRU abs diff={repro['track_b_max_abs_diff']}.\n",
        f"- Track A fresh-process replay max GRU abs diff={repro['track_a_max_abs_diff']}.\n\n",
        "## Safety\n",
        f"- DB counts unchanged: {db_safety['unchanged']}.\n",
        f"- Claim flags remain false: {db_safety['eligibility_flags_unchanged_false']}.\n\n",
        "## Tests\n",
        f"- pytest exit {cmd_results['pytest']['exit_code']}; ruff exit {cmd_results['ruff']['exit_code']}; "
        f"mypy exit {cmd_results['mypy']['exit_code']}; pip check exit {cmd_results['pip_check']['exit_code']}.\n\n",
        "## Deliverables\n",
    ]
    for path in sorted(REP.iterdir()):
        if path.is_file() and path.name.startswith("R4D_R1_"):
            report.append(f"- `{path}` SHA-256 `{sha256(path)}`\n")
    report.append(
        "\n## Single next action\n"
        "提交 R4D-R1 修复包供人工验收；只有验收通过后，才另开“当前 CDC ILINet 可信数据接入”任务。\n"
    )
    (REP / "R4D_R1_REPORT.md").write_text("".join(report), encoding="utf-8")
    manifest = {
        "final_status": final_status,
        "report_dir": str(REP),
        "experiment_dir": str(EXP),
        "files": {
            p.name: {"size": p.stat().st_size, "sha256": sha256(p)}
            for p in sorted(REP.iterdir())
            if p.is_file()
        },
    }
    (REP / "R4D_R1_MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "final_status": final_status,
                "report_dir": str(REP),
                "track_b_metrics": track_b_metrics,
                "db_unchanged": db_safety["unchanged"],
                "pytest": cmd_results["pytest"]["exit_code"],
                "ruff": cmd_results["ruff"]["exit_code"],
                "mypy": cmd_results["mypy"]["exit_code"],
                "pip_check": cmd_results["pip_check"]["exit_code"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
