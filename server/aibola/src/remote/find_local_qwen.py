from __future__ import annotations

import argparse
import json

from src.models.qwen25_local_model_finder import REPORT_DIR, find_local_qwen, write_search_reports
from src.remote.p0b_utils import mirror_report
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    parser = argparse.ArgumentParser(description="Find local Qwen2.5-7B-Instruct and mirror sanitized reports.")
    parser.add_argument("--root", default=".")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--max-depth", type=int, default=9)
    args = parser.parse_args()
    result = find_local_qwen(args.root, args.timeout_seconds, args.max_depth)
    write_search_reports(result)
    try:
        cfg = ServerConfig.from_project_root(args.root)
        runner = SSHRunner(cfg, timeout=120)
        mirror_report(runner, REPORT_DIR / "local_qwen_model_search_report.md", "local_qwen_model_search_report.md")
        mirror_report(runner, REPORT_DIR / "local_qwen_model_candidates.json", "local_qwen_model_candidates.json")
    except Exception:
        pass
    print(json.dumps({k: result[k] for k in ["status", "blocked_reason", "multiple_candidates_need_confirmation", "search_timed_out"]}, ensure_ascii=False))
    if result["status"] == "ok":
        return 0
    if result.get("multiple_candidates_need_confirmation"):
        return 3
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
