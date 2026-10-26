from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.remote.p0b_utils import remote_project_prelude
from src.remote.server_config import ServerConfig
from src.remote.ssh_runner import SSHRunner


def main() -> int:
    cfg = ServerConfig.from_project_root(".")
    runner = SSHRunner(cfg, timeout=120)
    remote = "\n".join(
        [
            "set +e",
            "ps -eo pid,etimes,cmd | grep -E 'src.eval.qwen_error_analysis|qwen_lora_eval_samples|qwen25_lora_eval' | grep -v grep || true",
            "printf '\\nREPORTS\\n'",
            "for f in data/outputs/reports/qwen_error_analysis.json data/outputs/reports/qwen_error_analysis.md data/outputs/reports/qwen_lora_eval_samples.jsonl data/outputs/figures/qwen_error_breakdown.png; do",
            '  if [ -e "$f" ]; then ls -lh "$f"; else echo missing:$f; fi',
            "done",
        ]
    )
    result = runner.run(remote_project_prelude(cfg, create=False) + remote + "\n", "logs/remote/p0c3_error_analysis_status_sanitized.log")
    print(result.stdout or result.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
