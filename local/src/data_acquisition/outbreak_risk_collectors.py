from __future__ import annotations

import argparse
import hashlib
import json
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.data_acquisition.common import (
    AcquisitionRecord,
    download_file,
    load_yaml,
    request_json,
    safe_name,
    write_report_pair,
)
from src.utils.config import resolve_path
from src.utils.io import ensure_dir, write_json


RAW_ROOT = Path("data/raw/outbreak_risk")
REPORT_DIR = "data/outputs/reports"
CONFIG_PATH = "configs/outbreak_risk_sources.yaml"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source_governance(source_id: str, item: dict[str, Any], *, allowed_use: str | None = None) -> dict[str, Any]:
    license_or_terms = str(item.get("license_or_terms") or "")
    unclear = not license_or_terms or "review required" in license_or_terms.lower()
    return {
        "source_name": item.get("source_name") or source_id,
        "license_or_terms": license_or_terms or "unknown",
        "download_time": _now(),
        "role_hint": item.get("role_hint", "metadata"),
        "allowed_use": allowed_use or item.get("allowed_use", "metadata_or_cross_check"),
        "usage_status": "metadata_only_or_needs_review" if unclear else "usable_subject_to_semantic_gate",
        "blocked_reason_if_excluded": "license_or_terms_need_review" if unclear else "none",
    }


def _record(
    dataset_id: str,
    source_id: str,
    item: dict[str, Any],
    status: str,
    url: str,
    *,
    local_path: str | None = None,
    bytes_: int | None = None,
    sha256: str | None = None,
    reason: str | None = None,
    group: str = "outbreak_risk",
    scope: str = "ebola_only",
    allowed_use: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AcquisitionRecord:
    governance = _source_governance(source_id, item, allowed_use=allowed_use)
    governance.update(metadata or {})
    governance["source_url"] = url
    governance["file_size"] = bytes_
    governance["checksum"] = sha256
    governance["blocked_reason_if_excluded"] = reason or governance["blocked_reason_if_excluded"]
    return AcquisitionRecord(
        dataset_id=dataset_id,
        source=str(item.get("source_name") or source_id),
        status=status,
        url=url,
        group=group,
        scope=scope,
        role_hint=str(item.get("role_hint") or "metadata"),
        local_path=local_path,
        bytes=bytes_,
        sha256=sha256,
        reason=reason,
        license=str(item.get("license_or_terms") or "unknown"),
        expected_granularity=str(item.get("source_type") or ""),
        metadata=governance,
    )


def _records_payload(records: list[AcquisitionRecord], *, status: str = "ok", blocked_reason: str = "none") -> dict[str, Any]:
    rows = [record.__dict__ for record in records]
    return {
        "status": status,
        "blocked_reason": blocked_reason,
        "download_time": _now(),
        "disease_scope": "ebola_only",
        "records": rows,
        "summary": {
            "total_records": len(rows),
            "downloaded": sum(1 for row in rows if row["status"] == "downloaded"),
            "metadata_only": sum(1 for row in rows if row["status"] == "metadata_only"),
            "failed": sum(1 for row in rows if row["status"] == "failed"),
            "skipped": sum(1 for row in rows if row["status"] == "skipped"),
            "downloaded_bytes": sum(int(row.get("bytes") or 0) for row in rows if row["status"] == "downloaded"),
        },
        "source_governance_fields": [
            "source_url",
            "source_name",
            "license_or_terms",
            "download_time",
            "file_size",
            "checksum",
            "role_hint",
            "allowed_use",
            "blocked_reason_if_excluded",
        ],
        "forbidden_actions_not_performed": ["qwen_training", "lora_training", "tmux", "model_download", "promote"],
    }


def _write_records_report(stem: str, title: str, records: list[AcquisitionRecord], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _records_payload(records)
    payload.update(extra or {})
    lines = [
        f"# {title}",
        "",
        f"status: {payload['status']}",
        f"blocked_reason: {payload['blocked_reason']}",
        f"disease_scope: {payload['disease_scope']}",
        "",
        "## Summary",
        "",
        "| total | downloaded | metadata_only | failed | skipped | bytes |",
        "|---:|---:|---:|---:|---:|---:|",
        "| {total_records} | {downloaded} | {metadata_only} | {failed} | {skipped} | {downloaded_bytes} |".format(
            **payload["summary"]
        ),
        "",
        "## Records",
        "",
        "| dataset_id | source | status | allowed_use | path | reason |",
        "|---|---|---|---|---|---|",
    ]
    for row in payload["records"]:
        meta = row.get("metadata") or {}
        lines.append(
            "| {dataset_id} | {source} | {status} | {allowed_use} | `{path}` | {reason} |".format(
                dataset_id=row.get("dataset_id", ""),
                source=row.get("source", ""),
                status=row.get("status", ""),
                allowed_use=meta.get("allowed_use", ""),
                path=row.get("local_path") or "",
                reason=str(row.get("reason") or meta.get("blocked_reason_if_excluded") or "").replace("|", "/"),
            )
        )
    lines.extend(["", "## JSON", "```json", json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), "```"])
    write_report_pair(stem, title, payload, lines)
    return payload


def load_sources(config: str = CONFIG_PATH) -> dict[str, Any]:
    return load_yaml(config)


def validate_outbreak_source_registry(config: str = CONFIG_PATH) -> dict[str, Any]:
    registry = load_sources(config)
    problems: list[dict[str, str]] = []
    required_top = ["sources", "seed_events"]
    for key in required_top:
        if key not in registry:
            problems.append({"item": key, "problem": "missing"})
    for source_id, item in (registry.get("sources") or {}).items():
        for field in ["source_name", "source_type", "license_or_terms", "role_hint", "allowed_use"]:
            if field not in item:
                problems.append({"item": f"sources.{source_id}", "problem": f"missing_{field}"})
        if not item.get("urls") and not item.get("search_terms") and not item.get("url_template"):
            problems.append({"item": f"sources.{source_id}", "problem": "missing_urls_or_search_terms"})
    for idx, event in enumerate(registry.get("seed_events") or []):
        for field in ["outbreak_id", "country", "iso3", "region_id", "event_start_date", "source_url", "usage"]:
            if field not in event:
                problems.append({"item": f"seed_events[{idx}]", "problem": f"missing_{field}"})
    payload = {
        "status": "blocked" if problems else "ok",
        "blocked_reason": "registry_validation_failed" if problems else "none",
        "config_path": config,
        "source_count": len(registry.get("sources") or {}),
        "seed_event_count": len(registry.get("seed_events") or []),
        "problems": problems,
    }
    write_report_pair("outbreak_source_registry_report", "Outbreak Source Registry Validation", payload)
    print(json.dumps({"status": payload["status"], "blocked_reason": payload["blocked_reason"]}, ensure_ascii=False))
    return payload


def _download_url(source_id: str, item: dict[str, Any], url: str, group: str) -> AcquisitionRecord:
    max_mb = int(item.get("max_file_mb") or 5)
    suffix = Path(urllib.parse.urlparse(url).path).suffix or ".html"
    target = RAW_ROOT / group / source_id / f"{safe_name(source_id)}_{_sha_text(url)[:8]}{suffix}"
    try:
        status, size, sha, reason = download_file(url, target, max_file_mb=max_mb, timeout=90)
    except Exception as exc:
        status, size, sha, reason = "failed", None, None, f"{type(exc).__name__}: {str(exc)[:180]}"
    return _record(
        f"{source_id}_{_sha_text(url)[:10]}",
        source_id,
        item,
        status,
        url,
        local_path=target.as_posix() if status == "downloaded" else None,
        bytes_=size,
        sha256=sha,
        reason=reason,
        group=group,
        allowed_use=item.get("allowed_use"),
    )


def collect_direct_data(config: str = CONFIG_PATH) -> dict[str, Any]:
    registry = load_sources(config)
    records: list[AcquisitionRecord] = []
    for source_id, item in (registry.get("sources") or {}).items():
        if item.get("source_type") not in {"label_source", "label_source_or_metadata", "dataset_search"}:
            continue
        if source_id == "hdx":
            records.extend(_collect_hdx_metadata(source_id, item, registry))
            continue
        for url in item.get("urls") or []:
            records.append(_download_url(source_id, item, str(url), "direct_sources"))
    return _write_records_report("outbreak_data_acquisition_report", "Outbreak Data Acquisition Report", records)


def _collect_hdx_metadata(source_id: str, item: dict[str, Any], registry: dict[str, Any]) -> list[AcquisitionRecord]:
    records: list[AcquisitionRecord] = []
    for term in (item.get("search_terms") or [])[:8]:
        api = "https://data.humdata.org/api/3/action/package_search?" + urllib.parse.urlencode({"q": term, "rows": 5})
        try:
            payload = request_json(api, timeout=60)
            packages = payload.get("result", {}).get("results", []) if isinstance(payload, dict) else []
            target = RAW_ROOT / "hdx_metadata" / f"{safe_name(term)}.json"
            ensure_dir(target.parent)
            write_json(target, {"query": term, "packages": packages})
            status = "downloaded"
            reason = "metadata_only_resource_download_deferred"
            size = target.stat().st_size
            sha = None
            local_path = target.as_posix()
        except Exception as exc:
            status, reason, size, sha, local_path = "failed", f"{type(exc).__name__}: {str(exc)[:180]}", None, None, None
        records.append(
            _record(
                f"hdx_metadata_{safe_name(term)}",
                source_id,
                item,
                status,
                api,
                local_path=local_path,
                bytes_=size,
                sha256=sha,
                reason=reason,
                group="direct_sources",
                allowed_use="source_discovery_or_structured_dataset_review",
                metadata={"query": term},
            )
        )
    return records


def find_literature_datasets(config: str = CONFIG_PATH) -> dict[str, Any]:
    registry = load_sources(config)
    literature_terms = [
        "Ebola outbreak prediction dataset",
        "Ebola DRC 2018 health zone cases",
        "Uganda 2022 Ebola district cases",
        "Ebola outbreak early warning dataset",
        "GDELT infectious disease outbreak",
    ]
    records: list[AcquisitionRecord] = []
    candidates: list[dict[str, Any]] = []
    endpoints = {
        "crossref": "https://api.crossref.org/works?" + urllib.parse.urlencode({"query": "", "rows": 5}),
        "zenodo": "https://zenodo.org/api/records?" + urllib.parse.urlencode({"q": "", "size": 5}),
        "pubmed": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?",
    }
    for term in literature_terms:
        for source_id in ["crossref", "zenodo", "pubmed"]:
            if source_id == "crossref":
                url = "https://api.crossref.org/works?" + urllib.parse.urlencode({"query": term, "rows": 5})
            elif source_id == "zenodo":
                url = "https://zenodo.org/api/records?" + urllib.parse.urlencode({"q": term, "size": 5})
            else:
                url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(
                    {"db": "pubmed", "term": term, "retmode": "json", "retmax": 5}
                )
            item = {
                "source_name": source_id,
                "license_or_terms": "API terms; metadata only",
                "role_hint": "metadata",
                "allowed_use": "source_discovery_only",
                "source_type": "literature_search",
            }
            try:
                payload = request_json(url, timeout=45)
                target = RAW_ROOT / "literature" / source_id / f"{safe_name(term)}.json"
                ensure_dir(target.parent)
                write_json(target, payload if isinstance(payload, dict) else {"payload": payload})
                count = _candidate_count(source_id, payload)
                candidates.extend(_extract_candidates(source_id, term, payload))
                records.append(
                    _record(
                        f"{source_id}_{safe_name(term)}",
                        source_id,
                        item,
                        "metadata_only",
                        url,
                        local_path=target.as_posix(),
                        bytes_=target.stat().st_size,
                        reason="literature_metadata_only_not_training_truth",
                        group="literature",
                        allowed_use="source_discovery_only",
                        metadata={"query": term, "candidate_count": count},
                    )
                )
            except Exception as exc:
                records.append(
                    _record(
                        f"{source_id}_{safe_name(term)}",
                        source_id,
                        item,
                        "failed",
                        url,
                        reason=f"{type(exc).__name__}: {str(exc)[:180]}",
                        group="literature",
                        allowed_use="source_discovery_only",
                        metadata={"query": term},
                    )
                )
    return _write_records_report(
        "outbreak_literature_survey_report",
        "Outbreak Literature Survey Report",
        records,
        {"dataset_links": candidates, "dataset_link_count": len(candidates)},
    )


def _candidate_count(source_id: str, payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    if source_id == "crossref":
        return len(payload.get("message", {}).get("items", []) or [])
    if source_id == "zenodo":
        return len(payload.get("hits", {}).get("hits", []) or [])
    if source_id == "pubmed":
        return len(payload.get("esearchresult", {}).get("idlist", []) or [])
    return 0


def _extract_candidates(source_id: str, term: str, payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    if source_id == "crossref":
        for item in (payload.get("message", {}).get("items", []) or [])[:5]:
            title = item.get("title", [""])[0] if isinstance(item.get("title"), list) else item.get("title")
            rows.append({"source": "Crossref", "query": term, "title": title, "doi": item.get("DOI"), "url": item.get("URL"), "suitable_for_label_source": False})
    elif source_id == "zenodo":
        for item in (payload.get("hits", {}).get("hits", []) or [])[:5]:
            meta = item.get("metadata", {})
            rows.append({"source": "Zenodo", "query": term, "title": meta.get("title"), "doi": meta.get("doi"), "url": item.get("links", {}).get("self_html"), "downloadable": bool(item.get("files")), "structured": "needs_review"})
    elif source_id == "pubmed":
        for pmid in payload.get("esearchresult", {}).get("idlist", [])[:5]:
            rows.append({"source": "PubMed", "query": term, "pmid": pmid, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "suitable_for_label_source": False})
    return rows


def collect_geo_pop_weather(config: str = CONFIG_PATH) -> dict[str, Any]:
    registry = load_sources(config)
    records: list[AcquisitionRecord] = []
    for source_id in ["geoboundaries", "nasa_power", "ourairports", "geofabrik"]:
        item = (registry.get("sources") or {}).get(source_id, {})
        if not item:
            continue
        if source_id == "geoboundaries":
            for iso3 in item.get("countries") or []:
                for adm in item.get("admin_levels") or ["ADM1"]:
                    url = str(item.get("url_template")).format(iso3=iso3, adm=adm)
                    records.append(_download_url(f"geoboundaries_{iso3}_{adm}", item, url, "geo_pop_weather"))
        elif source_id == "nasa_power":
            for row in registry.get("seed_events", [])[:5]:
                # Small representative weather probes; feature builder uses availability metadata only in P2a.
                url = str(item.get("url_template")).format(lon=0, lat=0, start="20220101", end="20220107")
                records.append(_download_url(f"nasa_power_{safe_name(row.get('iso3'))}", item, url, "geo_pop_weather"))
                break
        else:
            for url in item.get("urls") or []:
                records.append(_download_url(source_id, item, str(url), "geo_pop_weather"))
    return _write_records_report("outbreak_geo_pop_weather_collection_report", "Outbreak Geo Population Weather Collection Report", records)


def collect_osint_signals(config: str = CONFIG_PATH) -> dict[str, Any]:
    registry = load_sources(config)
    item = (registry.get("sources") or {}).get("gdelt", {})
    records: list[AcquisitionRecord] = []
    for query in (item.get("queries") or ["ebola"])[:4]:
        url = str(item.get("url_template")).format(query=urllib.parse.quote(str(query)), start="20220101000000", end="20221231235959")
        records.append(_download_url(f"gdelt_{safe_name(query)}", item, url, "osint_signals"))
    payload = _write_records_report("outbreak_osint_signal_report", "Outbreak OSINT Signal Report", records)
    payload["osint_policy"] = {
        "aggregate_only": True,
        "raw_personal_data_collected": False,
        "forbidden_raw_article_text_as_pre_event_feature": True,
    }
    write_report_pair("outbreak_osint_signal_report", "Outbreak OSINT Signal Report", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Run P2a outbreak risk acquisition stages.")
    parser.add_argument("stage", choices=["validate", "direct", "literature", "geo_pop_weather", "osint"])
    parser.add_argument("--config", default=CONFIG_PATH)
    args = parser.parse_args()
    if args.stage == "validate":
        payload = validate_outbreak_source_registry(args.config)
    elif args.stage == "direct":
        payload = collect_direct_data(args.config)
    elif args.stage == "literature":
        payload = find_literature_datasets(args.config)
    elif args.stage == "geo_pop_weather":
        payload = collect_geo_pop_weather(args.config)
    else:
        payload = collect_osint_signals(args.config)
    print(json.dumps({"status": payload.get("status"), "blocked_reason": payload.get("blocked_reason", "none")}, ensure_ascii=False))
    return 0 if payload.get("status") == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
