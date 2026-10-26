from __future__ import annotations

from src.utils.config import load_config, resolve_path


def main() -> int:
    cfg = load_config()
    report = resolve_path(cfg["paths"]["outputs"]) / "reports" / "outbreak_leave_one_out.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "# Outbreak Leave-One-Out\n\nP2 gate: insufficient real outbreak partitions in P0.\n",
        encoding="utf-8",
    )
    print(str(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
