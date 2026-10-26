from __future__ import annotations

import argparse
import json
import urllib.parse
from typing import Any

from src.data_acquisition.common import (
    AcquisitionRecord,
    append_manifest,
    load_yaml,
    polite_sleep,
    records_payload,
    report_records,
    request_json,
    safe_name,
    source_items,
    write_report_pair,
)


def _safe_query(url: str) -> Any:
    try:
        return request_json(url, timeout=45)
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {str(exc)[:180]}"}


def _pubmed(term: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"db": "pubmed", "term": term, "retmode": "json", "retmax": 8})
    url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?{query}"
    payload = _safe_query(url)
    ids = payload.get("esearchresult", {}).get("idlist", []) if isinstance(payload, dict) else []
    return [{"source": "PubMed", "query": term, "pmid": item, "url": f"https://pubmed.ncbi.nlm.nih.gov/{item}/"} for item in ids]


def _europe_pmc(term: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"query": term, "format": "json", "pageSize": 8})
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?{query}"
    payload = _safe_query(url)
    results = payload.get("resultList", {}).get("result", []) if isinstance(payload, dict) else []
    rows = []
    for item in results[:8]:
        rows.append(
            {
                "source": "Europe PMC",
                "query": term,
                "title": item.get("title"),
                "doi": item.get("doi"),
                "url": item.get("fullTextUrlList", {}).get("fullTextUrl", [{}])[0].get("url") if isinstance(item.get("fullTextUrlList"), dict) else item.get("doi"),
            }
        )
    return rows


def _crossref(term: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"query": term, "rows": 8})
    url = f"https://api.crossref.org/works?{query}"
    payload = _safe_query(url)
    items = payload.get("message", {}).get("items", []) if isinstance(payload, dict) else []
    rows = []
    for item in items[:8]:
        title = item.get("title", [""])[0] if isinstance(item.get("title"), list) else item.get("title")
        rows.append({"source": "Crossref", "query": term, "title": title, "doi": item.get("DOI"), "url": item.get("URL")})
    return rows


def _zenodo(term: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"q": term, "size": 8})
    url = f"https://zenodo.org/api/records?{query}"
    payload = _safe_query(url)
    hits = payload.get("hits", {}).get("hits", []) if isinstance(payload, dict) else []
    rows = []
    for item in hits[:8]:
        meta = item.get("metadata", {})
        files = item.get("files", [])
        rows.append(
            {
                "source": "Zenodo",
                "query": term,
                "record_id": item.get("id"),
                "title": meta.get("title"),
                "doi": meta.get("doi"),
                "url": item.get("links", {}).get("self_html"),
                "file_count": len(files) if isinstance(files, list) else 0,
            }
        )
    return rows


def _github(term: str) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({"q": term, "per_page": 8})
    url = f"https://api.github.com/search/repositories?{query}"
    payload = _safe_query(url)
    items = payload.get("items", []) if isinstance(payload, dict) else []
    rows = []
    for item in items[:8]:
        rows.append(
            {
                "source": "GitHub",
                "query": term,
                "name": item.get("full_name"),
                "url": item.get("html_url"),
                "description": item.get("description"),
                "license": (item.get("license") or {}).get("spdx_id"),
            }
        )
    return rows


def find_literature_dataset_links(config: str = "configs/data_source_registry_v3.yaml") -> dict[str, Any]:
    registry = load_yaml(config)
    link_sources = source_items(registry, "literature_dataset_links")
    candidates: list[dict[str, Any]] = []
    records: list[AcquisitionRecord] = []
    for source_name, item in link_sources.items():
        terms = item.get("search_terms") or []
        for term in terms:
            if item.get("source_type") == "pubmed_search":
                rows = _pubmed(term)
            elif item.get("source_type") == "europe_pmc_search":
                rows = _europe_pmc(term)
            elif item.get("source_type") == "crossref_search":
                rows = _crossref(term)
            elif item.get("source_type") == "zenodo_search":
                rows = _zenodo(term)
            elif item.get("source_type") == "github_search":
                rows = _github(term)
            else:
                rows = []
            candidates.extend({**row, "source_registry_id": source_name} for row in rows)
            records.append(
                AcquisitionRecord(
                    dataset_id=f"{source_name}_{safe_name(term)}",
                    source=source_name,
                    status="metadata_only" if rows else "failed",
                    url=str(item.get("url") or ""),
                    group="literature_dataset_links",
                    role_hint="cross_check_only",
                    reason=None if rows else "no_candidates_or_query_failed",
                    metadata={"query": term, "candidate_count": len(rows)},
                )
            )
            polite_sleep()
    append_manifest(records)
    payload = records_payload(records)
    payload["candidate_links"] = candidates
    payload["candidate_count"] = len(candidates)
    write_report_pair(
        "p1b_prisma_dataset_links",
        "P1b PRISMA Dataset Links",
        payload,
        [
            "# P1b PRISMA Dataset Links",
            "",
            f"status: {payload['status']}",
            f"blocked_reason: {payload['blocked_reason']}",
            f"candidate_count: {payload['candidate_count']}",
            "",
            "## Candidate Links",
            "",
            "| source | title/name | doi | url |",
            "|---|---|---|---|",
            *[
                f"| {row.get('source','')} | {str(row.get('title') or row.get('name') or row.get('pmid') or row.get('record_id') or '').replace('|','/')} | {row.get('doi','')} | {row.get('url','')} |"
                for row in candidates[:80]
            ],
        ],
    )
    report_records("p1b_prisma_query_report", "P1b PRISMA Query Report", records)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Find literature-linked Ebola datasets for P1b.")
    parser.add_argument("--config", default="configs/data_source_registry_v3.yaml")
    args = parser.parse_args()
    payload = find_literature_dataset_links(args.config)
    print(json.dumps({"status": payload["status"], "candidate_count": payload["candidate_count"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
