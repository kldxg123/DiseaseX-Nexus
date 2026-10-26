from __future__ import annotations

from dataclasses import replace

from live_source_search_orchestrator.types import NormalizedSourceCandidate


def dedupe_candidates(candidates: list[NormalizedSourceCandidate]) -> tuple[list[NormalizedSourceCandidate], dict]:
    by_url: dict[str, NormalizedSourceCandidate] = {}
    merged = 0
    for candidate in candidates:
        existing = by_url.get(candidate.normalized_url)
        if existing is None:
            by_url[candidate.normalized_url] = candidate
            continue
        by_url[candidate.normalized_url] = _merge(existing, candidate)
        merged += 1
    by_domain_title: dict[tuple[str, str], NormalizedSourceCandidate] = {}
    for candidate in by_url.values():
        key = (candidate.domain, _title_key(candidate.normalized_title))
        existing = by_domain_title.get(key)
        if existing is None:
            by_domain_title[key] = candidate
            continue
        by_domain_title[key] = _merge(existing, candidate)
        merged += 1
    rows = sorted(
        by_domain_title.values(),
        key=lambda item: (item.total_source_score, -int(item.raw_rank or 9999), item.domain),
        reverse=True,
    )
    return rows, {"input_count": len(candidates), "output_count": len(rows), "merged_count": merged}


def _merge(left: NormalizedSourceCandidate, right: NormalizedSourceCandidate) -> NormalizedSourceCandidate:
    providers = sorted(set(left.merged_from_providers + right.merged_from_providers + [left.provider, right.provider]))
    data_keywords = sorted(set(left.data_keywords + right.data_keywords))
    disease_mentions = sorted(set(left.disease_mentions + right.disease_mentions))
    country_mentions = sorted(set(left.country_mentions + right.country_mentions))
    warnings = sorted(set(left.warnings + right.warnings))
    preferred = left if left.raw_rank is not None and (right.raw_rank is None or left.raw_rank <= right.raw_rank) else right
    return replace(
        preferred,
        merged_from_providers=providers,
        data_keywords=data_keywords,
        disease_mentions=disease_mentions,
        country_mentions=country_mentions,
        warnings=warnings,
        evidence_summary=f"{preferred.evidence_summary}; merged_providers={','.join(providers)}",
    )


def _title_key(title: str) -> str:
    words = [word for word in title.lower().split() if len(word) > 2]
    return " ".join(words[:6])


__all__ = ["dedupe_candidates"]
