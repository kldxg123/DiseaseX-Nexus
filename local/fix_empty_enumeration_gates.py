from __future__ import annotations

import json
from pathlib import Path

REPORT = Path("/tmp/us_legionellosis_report_dir.txt").read_text().strip()
MANIFEST = Path(REPORT) / "US_LEGIONELLOSIS_STACKS_ENUMERATION_MANIFEST.json"
AUDIT = Path(REPORT) / "US_LEGIONELLOSIS_STACKS_ENUMERATION_GATE_SEMANTICS_FIX.json"

data = json.loads(MANIFEST.read_text(encoding="utf-8"))
before = dict(data.get("gate_status") or {})
if data.get("candidate_count_total") == 0:
    gate = data.setdefault("gate_status", {})
    for key in [
        "canonical_urls_from_official_export",
        "detail_page_ds2_parsed",
        "duplicate_weeks",
        "title_series_filter",
        "txt_downloaded_and_valid",
    ]:
        gate[key] = "NOT_RUN_BLOCKED_UPSTREAM"
    if gate.get("source_authenticity") == "PASS":
        gate["source_authenticity"] = "FAIL_NO_ITEMS"
    data["gate_semantics_corrected_at"] = "candidate_count_total=0"
MANIFEST.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
AUDIT.write_text(
    json.dumps(
        {
            "status": "PASS",
            "reason": "Empty candidate set gates were corrected from vacuous PASS to NOT_RUN_BLOCKED_UPSTREAM.",
            "candidate_count_total": data.get("candidate_count_total"),
            "before": before,
            "after": data.get("gate_status"),
            "raw_http_responses_modified": False,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ),
    encoding="utf-8",
)
print(json.dumps({"status": "PASS", "audit": str(AUDIT)}, sort_keys=True))
