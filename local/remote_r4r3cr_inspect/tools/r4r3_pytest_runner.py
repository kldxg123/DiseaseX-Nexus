#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def run(cmd: list[str], env: dict[str, str], timeout: int = 300) -> dict:
    p = subprocess.run(cmd, cwd="USER_HOME/projects/openclaw", text=True, capture_output=True, env=env, timeout=timeout)
    return {"returncode": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--report-dir", required=True)
    args = ap.parse_args()
    report = Path(args.report_dir)
    env = dict(os.environ)
    env["PYTHONPATH"] = "src:runtime/python-packages"
    env["R4R2_TEST_DB"] = args.db
    env["R4R3_TEST_DB"] = args.db
    tests = [
        "tests/test_r4r1_shadow_and_evidence.py",
        "tests/test_r4r2_semantic_guards.py",
        "tests/test_r4r2_dashboard_auth_dom.py",
        "tests/test_r4r3_review_input_contract.py",
    ]
    collect = run(["python3", "-m", "pytest", "--collect-only", "-q", *tests], env)
    actual = run(["python3", "-m", "pytest", "-q", *tests], env)
    (report / "R4R3_PYTEST_COLLECT.txt").write_text(collect["stdout"] + collect["stderr"], encoding="utf-8")
    (report / "R4R3_PYTEST_FULL.log").write_text(actual["stdout"] + actual["stderr"], encoding="utf-8")
    collected = sum(1 for line in collect["stdout"].splitlines() if line.startswith("tests/"))
    payload = {
        "schema_version": "1.0",
        "task_id": "EPIDEMIC_KG_PREDICTION_R4R3_TRUSTED_REVIEW_INPUT_CLOSURE",
        "execution_id": report.name,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pytest_available": True,
        "pytest_collected": collected,
        "collect_returncode": collect["returncode"],
        "run_returncode": actual["returncode"],
        "run_output_tail": (actual["stdout"] + actual["stderr"])[-8000:],
        "result_status": "PYTEST_PASS" if collect["returncode"] == 0 and actual["returncode"] == 0 else "PYTEST_FAIL",
    }
    (report / "R4R3_PYTEST_RESULTS.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["result_status"] == "PYTEST_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
