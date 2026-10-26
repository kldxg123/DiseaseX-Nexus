from __future__ import annotations

from worker_agent.cli import main as worker_cli_main


def main() -> int:
    return worker_cli_main(["loop", "--max-iterations", "1"])


if __name__ == "__main__":
    raise SystemExit(main())
