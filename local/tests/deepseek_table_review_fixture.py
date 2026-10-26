from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.table_extraction_runner import run_structured_table_review


def create_mock_table_review(tmp_path: Path, *, disease: str = "Ebola") -> tuple[dict[str, Any], Path]:
    summary = run_structured_table_review(
        disease=disease,
        country_or_region="African Region",
        date_start="2026-05-01",
        date_end="2026-07-01",
        output_dir=str(tmp_path / "table-review"),
        mock_deepseek=True,
        allow_deepseek=False,
    )
    return summary, Path(summary["paths"]["review_json"])


def approve_review_rows(review_path: Path, *, count: int | None = None) -> list[dict[str, Any]]:
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    rows = payload.get("rows") if isinstance(payload, dict) else []
    approved = 0
    for row in rows:
        if count is None or approved < count:
            row["approval_status"] = "approved"
            row["reviewer_note"] = "approved in local test"
            approved += 1
    review_path.write_text(json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return rows
