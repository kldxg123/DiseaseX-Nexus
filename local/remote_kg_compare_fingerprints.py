from __future__ import annotations

import json
from pathlib import Path

REPORT_DIR = Path("USER_HOME/projects/openclaw/reports/forecast_model_kg_v1_20260729T053519Z_nogit")


def main() -> None:
    before = json.loads((REPORT_DIR / "PROTECTED_STATE_FINGERPRINT_BEFORE_RUNTIME_RELOAD.json").read_text(encoding="utf-8"))
    after = json.loads((REPORT_DIR / "PROTECTED_STATE_FINGERPRINT_CURRENT.json").read_text(encoding="utf-8"))
    before.pop("generated_at", None)
    after.pop("generated_at", None)
    ok = before == after
    result = {
        "comparison": "PASS" if ok else "FAIL",
        "database_writes_expected": 0,
        "protected_state_unchanged": ok,
    }
    output = REPORT_DIR / "PROTECTED_STATE_COMPARISON.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(output)
    if not ok:
        raise SystemExit("PROTECTED_STATE_CHANGED")


if __name__ == "__main__":
    main()
