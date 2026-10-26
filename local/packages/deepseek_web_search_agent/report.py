from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from deepseek_web_search_agent.table_extraction_runner import run_structured_table_review
from deepseek_web_search_agent.source_pack_builder import load_source_pack, source_pack_readiness, source_pack_to_dict


def make_run_id(disease: str, country: str) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    safe = "_".join(f"{disease}_{country}".lower().replace("/", " ").split())
    return f"run_deepseek_{safe}_{stamp}"


def make_run_dir(output_dir: str | Path, run_id: str) -> Path:
    root = Path(output_dir) / run_id
    root.mkdir(parents=True, exist_ok=True)
    return root


def write_run_reports(
    *,
    run_dir: str | Path,
    summary: Any,
    query_plan: Any,
    search_results: list[dict[str, Any]],
    page_reads: list[dict[str, Any]],
    extraction_records: list[dict[str, Any]],
    persistence_summary: dict[str, Any],
    cleaning_summary: dict[str, Any],
    visualization_data: dict[str, Any],
    safety_audit: dict[str, Any],
) -> dict[str, str]:
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    payloads = {
        "deepseek_web_search_summary.json": _as_dict(summary),
        "query_plan.json": _as_dict(query_plan),
        "search_results_summary.json": {"results": search_results},
        "page_read_summary.json": {"page_reads": page_reads},
        "deepseek_structured_extraction_replay.json": _as_dict(summary)
        | {
            "records": extraction_records,
            "records_extracted_by_deepseek": len(extraction_records),
        },
        "database_persistence_summary.json": persistence_summary,
        "cleaning_bridge_summary.json": cleaning_summary,
        "visualization_3d_data.json": visualization_data,
        "safety_audit.json": safety_audit,
    }
    paths: dict[str, str] = {}
    for name, payload in payloads.items():
        path = root / name
        path.write_text(json.dumps(_safe(payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths[name.removesuffix(".json")] = str(path)
    page_sources = load_source_pack(root / "page_read_summary.json")
    if page_sources:
        page_pack = root / "page_read_source_pack.json"
        structured_pack = root / "structured_source_pack.json"
        readiness_path = root / "source_pack_readiness.json"
        pack_payload = source_pack_to_dict(page_sources)
        page_pack.write_text(json.dumps(_safe(pack_payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        structured_pack.write_text(json.dumps(_safe(pack_payload), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        readiness_path.write_text(json.dumps(_safe(source_pack_readiness(page_sources)), ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["page_read_source_pack"] = str(page_pack)
        paths["structured_source_pack"] = str(structured_pack)
        paths["source_pack_readiness"] = str(readiness_path)
    md = root / "deepseek_web_search_summary.md"
    md.write_text(_summary_markdown(_as_dict(summary)), encoding="utf-8")
    paths["summary_markdown"] = str(md)
    return paths


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_dict"):
        payload = value.to_dict()
        return payload if isinstance(payload, dict) else {}
    return {}


def _safe(value: Any) -> Any:
    forbidden = {"prompt", "completion", "raw_text", "html", "response_body", "metadata_json", "feature_json", "provenance_json", "lesson_json"}
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items() if str(key).lower() not in forbidden}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    if isinstance(value, str):
        return value if len(value) <= 1000 else value[:997] + "..."
    return value


def _summary_markdown(summary: dict[str, Any]) -> str:
    lines = ["# DeepSeek Web Search Summary", ""]
    for key in ("status", "run_id", "disease", "country", "records_extracted", "records_persisted"):
        lines.append(f"- `{key}`: `{summary.get(key)}`")
    return "\n".join(lines) + "\n"


__all__ = ["make_run_dir", "make_run_id", "run_structured_table_review", "write_run_reports"]
