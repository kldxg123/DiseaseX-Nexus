from __future__ import annotations

import json
import time
from pathlib import Path

from live_source_search_orchestrator.types import DiseaseAliasSet, LiveSourceSearchRequest, ProviderPlan

VALID_PROVIDERS = {"auto", "data_gov", "official_directory", "gdelt"}
AUTO_ORDER = ["data_gov", "official_directory", "gdelt"]
CURATED_OFFICIAL_DIRECTORY_URLS = {
    "cholera": "https://www.cdc.gov/cholera/",
    "dengue": "https://www.cdc.gov/dengue/",
    "influenza": "https://www.cdc.gov/fluview/",
    "flu": "https://www.cdc.gov/fluview/",
    "measles": "https://www.cdc.gov/measles/",
    "norovirus": "https://www.cdc.gov/norovirus/",
}


def plan_providers(
    request: LiveSourceSearchRequest,
    aliases: DiseaseAliasSet,
    *,
    gdelt_cooldown_active: bool | None = None,
) -> ProviderPlan:
    if gdelt_cooldown_active is None:
        gdelt_cooldown_active = (not request.dry_run) and detect_gdelt_cooldown()
    provider_text = str(request.providers or "auto").strip().lower()
    if provider_text not in VALID_PROVIDERS:
        raise ValueError("provider must be auto, data_gov, official_directory, or gdelt.")
    budget = max(1, min(int(request.max_total_requests), 3))
    warnings: list[str] = []
    skipped: list[dict] = []
    if provider_text == "auto":
        candidates = list(AUTO_ORDER)
    else:
        candidates = [provider_text]
    planned: list[str] = []
    for provider in candidates:
        if provider == "gdelt" and gdelt_cooldown_active and not request.include_rate_limited_provider:
            skipped.append({"provider": "gdelt", "reason": "rate_limited_cooldown"})
            warnings.append("GDELT skipped because a cooldown is active.")
            continue
        if provider == "official_directory" and not request.curated_official_directory_url:
            if not curated_official_directory_url(request.disease, aliases):
                skipped.append({"provider": "official_directory", "reason": "missing_curated_directory_url"})
                warnings.append("Official directory skipped because no curated URL is available for this disease.")
                continue
        planned.append(provider)
        if len(planned) >= budget:
            break
    if not planned and provider_text == "auto":
        planned = ["data_gov"][:budget]
        warnings.append("Provider plan was empty; falling back to data_gov dry planning.")
    return ProviderPlan(
        providers_requested=provider_text,
        providers_planned=planned,
        max_total_requests=budget,
        skipped_providers=skipped,
        warnings=warnings,
    )


def curated_official_directory_url(disease: str, aliases: DiseaseAliasSet | None = None) -> str | None:
    terms = [str(disease or "").strip().lower()]
    if aliases is not None:
        terms.extend(str(item or "").strip().lower() for item in aliases.aliases)
    for term in terms:
        if term in CURATED_OFFICIAL_DIRECTORY_URLS:
            return CURATED_OFFICIAL_DIRECTORY_URLS[term]
    text = " ".join(terms)
    for key, url in CURATED_OFFICIAL_DIRECTORY_URLS.items():
        if key in text:
            return url
    return None


def detect_gdelt_cooldown(
    report_roots: tuple[str, ...] = ("reports/live_source_search", "reports/source_discovery/live_runs"),
    *,
    cooldown_seconds: int = 24 * 60 * 60,
) -> bool:
    cutoff = time.time() - max(1, int(cooldown_seconds))
    for root_text in report_roots:
        root = Path(root_text)
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:80]:
            try:
                if path.stat().st_mtime < cutoff:
                    continue
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if _payload_has_gdelt_rate_limit(payload):
                return True
    return False


def _payload_has_gdelt_rate_limit(value) -> bool:
    if isinstance(value, dict):
        provider = str(value.get("provider") or "").lower()
        failure_type = str(value.get("failure_type") or "").lower()
        if provider == "gdelt" and failure_type == "rate_limited":
            return True
        failure_types = value.get("failure_types")
        if isinstance(failure_types, dict) and str(failure_types.get("gdelt") or "").lower() == "rate_limited":
            return True
        return any(_payload_has_gdelt_rate_limit(item) for item in value.values())
    if isinstance(value, list):
        return any(_payload_has_gdelt_rate_limit(item) for item in value[:200])
    return False


__all__ = [
    "AUTO_ORDER",
    "CURATED_OFFICIAL_DIRECTORY_URLS",
    "VALID_PROVIDERS",
    "curated_official_directory_url",
    "detect_gdelt_cooldown",
    "plan_providers",
]
