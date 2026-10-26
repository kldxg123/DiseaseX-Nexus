from __future__ import annotations

from pathlib import Path

ROOT = Path("USER_HOME/projects/openclaw")
REPORT = ROOT / "reports/us_legionellosis_all_forecasts_v1_20260729T160650Z_90fbc361"

PATS = [
    "V3",
    "V4",
    "V5",
    "v3",
    "v4",
    "v5",
    "212343",
    "154970",
    "176876",
    "177262",
    "CHECKPOINT",
    "LEDGER",
    "MANIFEST",
    "PREVIOUS",
    "RECENT",
    "COVERAGE",
    "zip",
    "sha256",
    "detail",
    "txt",
]


def main() -> None:
    print("report_exists", REPORT.exists())
    if not REPORT.exists():
        return
    files = []
    for p in REPORT.rglob("*"):
        if p.is_file() and any(token in str(p) for token in PATS):
            files.append(str(p.relative_to(REPORT)))
    files.sort()
    print("matched_count", len(files))
    for item in files[:1000]:
        print(item)


if __name__ == "__main__":
    main()
