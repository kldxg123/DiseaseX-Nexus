from __future__ import annotations

import argparse
import json
import urllib.parse
from pathlib import Path
from typing import Any

from src.data_acquisition.common import (
    AcquisitionRecord,
    RAW_ROOT,
    append_manifest,
    default_role_for_scope,
    download_file,
    extension_from_url,
    infer_scope,
    load_yaml,
    polite_sleep,
    records_payload,
    report_records,
    request_json,
    safe_name,
    source_items,
    write_report_pair,
)


STRUCTURED_SUFFIXES = {".csv", ".tsv", ".xls", ".xlsx", ".json", ".geojson", ".zip"}


def _source_max(item: dict[str, Any], default: int = 80) -> int:
    try:
        return int(item.get("max_file_mb", default))
    except Exception:
        return default


def _download_static_source(source_id: str, item: dict[str, Any], subdir: str) -> list[AcquisitionRecord]:
    url = str(item.get("url") or "")
    if not url:
        return []
    ext = extension_from_url(url, ".html")
    target = RAW_ROOT / subdir / source_id / f"{safe_name(source_id)}{ext}"
    status, size, sha, reason = download_file(url, target, _source_max(item), timeout=120)
    scope = infer_scope(f"{source_id} {url}")
    return [
        AcquisitionRecord(
            dataset_id=source_id,
            source=source_id,
            status=status,
            url=url,
            group=str(item.get("group") or ""),
            scope=scope,
            role_hint=default_role_for_scope(scope, {}),
            local_path=target.as_posix() if status == "downloaded" else None,
            bytes=size,
            sha256=sha,
            reason=reason,
            license=str(item.get("license") or ""),
            expected_granularity=str(item.get("expected_granularity") or ""),
            metadata={"source_type": item.get("source_type"), "manual_semantic_review_required": item.get("manual_semantic_review_required")},
        )
    ]


def _hdx_resource_selected(resource: dict[str, Any]) -> tuple[bool, str]:
    name = " ".join(str(resource.get(key) or "") for key in ["name", "description", "format", "url"]).lower()
    url = str(resource.get("url") or "")
    if not url:
        return False, "missing_url"
    suffix = extension_from_url(url, "").lower()
    fmt = str(resource.get("format") or "").lower()
    if suffix not in STRUCTURED_SUFFIXES and not any(token in fmt for token in ["csv", "xls", "json", "geojson", "zip"]):
        return False, "unsupported_or_narrative_format"
    positive = ["ebola", "evd", "case", "death", "health zone", "zone de sante", "district", "surveillance", "uganda", "drc", "rdc", "congo", "guinea"]
    if not any(token in name for token in positive):
        return False, "no_case_admin_signal"
    negative = ["protein", "molecule", "drug", "trial", "vehicle"]
    if any(token in name for token in negative):
        return False, "non_surveillance_resource"
    return True, "selected"


def download_hdx_case_sources(registry: dict[str, Any], max_resources: int = 0) -> list[AcquisitionRecord]:
    item = source_items(registry).get("hdx_case_search", {})
    terms = (item.get("search_terms") or [])[:6]
    max_file_mb = min(_source_max(item, 120), 40)
    records: list[AcquisitionRecord] = []
    seen_urls: set[str] = set()
    downloaded = 0
    for term in terms:
        api = "https://data.humdata.org/api/3/action/package_search?" + urllib.parse.urlencode({"q": term, "rows": 5})
        try:
            payload = request_json(api, timeout=90)
            packages = payload.get("result", {}).get("results", []) if isinstance(payload, dict) else []
        except Exception as exc:
            records.append(
                AcquisitionRecord(
                    dataset_id=f"hdx_query_{safe_name(term)}",
                    source="HDX CKAN",
                    status="failed",
                    url=api,
                    group="case_sources",
                    reason=f"{type(exc).__name__}: {str(exc)[:180]}",
                    metadata={"query": term},
                )
            )
            continue
        metadata_path = RAW_ROOT / "hdx_metadata" / f"{safe_name(term)}.json"
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(json.dumps({"query": term, "packages": packages}, ensure_ascii=False, indent=2), encoding="utf-8")
        records.append(
            AcquisitionRecord(
                dataset_id=f"hdx_metadata_{safe_name(term)}",
                source="HDX CKAN",
                status="downloaded",
                url=api,
                group="case_sources",
                scope=infer_scope(term),
                role_hint="cross_check_only",
                local_path=metadata_path.as_posix(),
                bytes=metadata_path.stat().st_size,
                sha256=None,
                reason="metadata_only_first_pass; resource_file_download_deferred_to_avoid_blocking",
                license=str(item.get("license") or "see_resource_metadata"),
                expected_granularity="package_resource_metadata",
                metadata={"query": term, "package_count": len(packages)},
            )
        )
        for package in packages:
            if downloaded >= max_resources:
                break
            package_title = str(package.get("title") or package.get("name") or "")
            package_name = safe_name(package.get("name") or package_title)
            for resource in package.get("resources") or []:
                if downloaded >= max_resources:
                    break
                url = str(resource.get("url") or "")
                if not url or url in seen_urls:
                    continue
                selected, reason = _hdx_resource_selected(resource)
                scope = infer_scope(" ".join([term, package_title, str(resource.get("name") or ""), url]))
                role = default_role_for_scope(scope, registry)
                dataset_id = f"hdx_{package_name}_{safe_name(resource.get('name') or resource.get('id') or 'resource')}"
                if not selected:
                    records.append(
                        AcquisitionRecord(
                            dataset_id=dataset_id,
                            source="HDX CKAN",
                            status="skipped",
                            url=url,
                            group="case_sources",
                            scope=scope,
                            role_hint=role,
                            reason=reason,
                            license=str(package.get("license_title") or package.get("license_id") or "see_hdx_metadata"),
                            metadata={"query": term, "package": package_title, "resource": resource.get("name"), "format": resource.get("format")},
                        )
                    )
                    continue
                seen_urls.add(url)
                suffix = extension_from_url(url, "." + safe_name(resource.get("format") or "dat"))
                target = RAW_ROOT / "hdx" / scope / f"{dataset_id}{suffix}"
                status, size, sha, dl_reason = download_file(url, target, max_file_mb, timeout=90)
                if status == "downloaded":
                    downloaded += 1
                records.append(
                    AcquisitionRecord(
                        dataset_id=dataset_id,
                        source="HDX CKAN",
                        status=status,
                        url=url,
                        group="case_sources",
                        scope=scope,
                        role_hint=role,
                        local_path=target.as_posix() if status == "downloaded" else None,
                        bytes=size,
                        sha256=sha,
                        reason=dl_reason,
                        license=str(package.get("license_title") or package.get("license_id") or "see_hdx_metadata"),
                        expected_granularity="candidate_case_or_admin_table",
                        metadata={"query": term, "package": package_title, "resource": resource.get("name"), "format": resource.get("format")},
                    )
                )
        polite_sleep()
    return records


def download_case_sources(config: str = "configs/data_source_registry_v3.yaml") -> dict[str, Any]:
    registry = load_yaml(config)
    records = download_hdx_case_sources(registry)
    for source_id, item in source_items(registry, "case_sources").items():
        if item.get("source_type") in {"html_page", "direct_url"}:
            records.extend(_download_static_source(source_id, item, "case_reference_pages"))
    manifest = append_manifest(records)
    payload = report_records("p1b_source_download_report", "P1b Source Download Report", records)
    inventory = {"status": "ok", "blocked_reason": "none", **manifest}
    inventory["downloaded_sources"] = sorted({row["source"] for row in inventory["records"] if row["status"] == "downloaded"})
    write_report_pair("p1b_download_inventory", "P1b Download Inventory", inventory)
    return payload


def _country_meta(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    meta = registry.get("country_metadata", {})
    return meta if isinstance(meta, dict) else {}


def download_geoboundaries(registry: dict[str, Any]) -> list[AcquisitionRecord]:
    item = source_items(registry).get("geoboundaries", {})
    countries = item.get("countries") or []
    admin_levels = item.get("admin_levels") or ["ADM1", "ADM2"]
    records: list[AcquisitionRecord] = []
    for iso3 in countries:
        for adm in admin_levels:
            api = str(item.get("url_template") or "").format(iso3=iso3, adm=adm)
            dataset_id = f"geoboundaries_{iso3}_{adm}"
            try:
                meta = request_json(api, timeout=90)
                meta_path = RAW_ROOT / "geoboundaries" / str(iso3) / f"{adm.lower()}_metadata.json"
                meta_path.parent.mkdir(parents=True, exist_ok=True)
                meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                url = meta.get("gjDownloadURL") or meta.get("simplifiedGeometryGeoJSON")
                if not url:
                    raise ValueError("missing_geojson_download_url")
                target = RAW_ROOT / "geoboundaries" / str(iso3) / f"{iso3}_{adm}.geojson"
                status, size, sha, reason = download_file(str(url), target, _source_max(item, 80), timeout=240)
                records.append(AcquisitionRecord(dataset_id, "geoBoundaries", status, str(url), "admin_population_weather", "background_feature", "background_feature", target.as_posix() if status == "downloaded" else None, size, sha, reason, str(item.get("license") or "gbOpen"), "admin_boundary", {"api": api, "metadata_path": meta_path.as_posix()}))
            except Exception as exc:
                records.append(AcquisitionRecord(dataset_id, "geoBoundaries", "failed", api, "admin_population_weather", "background_feature", "background_feature", reason=f"{type(exc).__name__}: {str(exc)[:180]}", license=str(item.get("license") or "gbOpen"), expected_granularity="admin_boundary"))
    return records


def download_worldpop(registry: dict[str, Any]) -> list[AcquisitionRecord]:
    item = source_items(registry).get("worldpop", {})
    countries = item.get("countries") or []
    years = item.get("years") or [2020]
    meta = _country_meta(registry)
    records: list[AcquisitionRecord] = []
    for iso3 in countries:
        country = meta.get(str(iso3), {})
        iso2 = str(country.get("iso2") or str(iso3).lower())
        for year in years:
            url = str(item.get("url_template") or "").format(year=year, iso3=iso3, iso2=iso2)
            records.append(
                AcquisitionRecord(
                    f"worldpop_{iso3}_{year}",
                    "WorldPop",
                    "skipped",
                    url,
                    "admin_population_weather",
                    "background_feature",
                    "background_feature",
                    reason="heavy_raster_deferred_for_nonblocking_p1b; download later only if needed for feature enrichment",
                    license=str(item.get("license") or ""),
                    expected_granularity="raster_population",
                    metadata={"country": country.get("name"), "year": year},
                )
            )
    return records


def download_weather(registry: dict[str, Any]) -> list[AcquisitionRecord]:
    nasa = source_items(registry).get("nasa_power", {})
    openmeteo = source_items(registry).get("openmeteo", {})
    records: list[AcquisitionRecord] = []
    for point in registry.get("weather_points", []) or []:
        start = str(point.get("start", "")).replace("-", "")
        end = str(point.get("end", "")).replace("-", "")
        nasa_params = urllib.parse.urlencode(
            {
                "parameters": "PRECTOTCORR,T2M,T2M_MAX,T2M_MIN,RH2M,WS2M",
                "community": "AG",
                "longitude": point.get("lon"),
                "latitude": point.get("lat"),
                "start": start,
                "end": end,
                "format": "JSON",
                "time-standard": "UTC",
            }
        )
        nasa_url = f"{nasa.get('url')}?{nasa_params}"
        nasa_target = RAW_ROOT / "nasa_power" / str(point.get("scope")) / f"{safe_name(point.get('id'))}.json"
        status, size, sha, reason = download_file(nasa_url, nasa_target, _source_max(nasa, 10), timeout=180)
        records.append(AcquisitionRecord(f"nasa_power_{safe_name(point.get('id'))}", "NASA POWER", status, nasa_url, "admin_population_weather", str(point.get("scope") or ""), "background_feature", nasa_target.as_posix() if status == "downloaded" else None, size, sha, reason, str(nasa.get("license") or ""), "daily_point_weather", {"point": point}))
        open_params = urllib.parse.urlencode(
            {
                "latitude": point.get("lat"),
                "longitude": point.get("lon"),
                "start_date": point.get("start"),
                "end_date": point.get("end"),
                "daily": "temperature_2m_mean,precipitation_sum,relative_humidity_2m_mean",
                "timezone": "UTC",
            }
        )
        open_url = f"{openmeteo.get('url')}?{open_params}"
        open_target = RAW_ROOT / "openmeteo" / str(point.get("scope")) / f"{safe_name(point.get('id'))}.json"
        status, size, sha, reason = download_file(open_url, open_target, _source_max(openmeteo, 10), timeout=180)
        records.append(AcquisitionRecord(f"openmeteo_{safe_name(point.get('id'))}", "Open-Meteo", status, open_url, "admin_population_weather", str(point.get("scope") or ""), "background_feature", open_target.as_posix() if status == "downloaded" else None, size, sha, reason, str(openmeteo.get("license") or ""), "daily_point_weather", {"point": point}))
        polite_sleep()
    return records


def download_admin_population_weather(config: str = "configs/data_source_registry_v3.yaml") -> dict[str, Any]:
    registry = load_yaml(config)
    records: list[AcquisitionRecord] = []
    records.extend(download_geoboundaries(registry))
    records.extend(download_worldpop(registry))
    records.extend(download_weather(registry))
    manifest = append_manifest(records)
    payload = report_records("p1b_admin_population_weather_download_report", "P1b Admin Population Weather Download Report", records)
    inventory = {"status": "ok", "blocked_reason": "none", **manifest}
    inventory["downloaded_sources"] = sorted({row["source"] for row in inventory["records"] if row.get("status") == "downloaded"})
    write_report_pair("p1b_download_inventory", "P1b Download Inventory", inventory)
    return payload


def download_mobility_sources(config: str = "configs/data_source_registry_v3.yaml") -> dict[str, Any]:
    registry = load_yaml(config)
    records: list[AcquisitionRecord] = []
    meta = _country_meta(registry)
    geofabrik = source_items(registry).get("geofabrik_osm", {})
    for iso3 in geofabrik.get("countries") or []:
        country = meta.get(str(iso3), {})
        slug = country.get("geofabrik_slug")
        if not slug:
            continue
        url = str(geofabrik.get("url_template") or "").format(geofabrik_slug=slug)
        records.append(
            AcquisitionRecord(
                f"geofabrik_{iso3}",
                "Geofabrik OSM",
                "skipped",
                url,
                "mobility_sources",
                str(iso3),
                "background_feature",
                reason="heavy_osm_pbf_deferred_for_nonblocking_p1b; use as road feature source in a later dedicated pull",
                license=str(geofabrik.get("license") or ""),
                expected_granularity="road_network",
                metadata={"country": country.get("name")},
            )
        )
    for source_id in ["ourairports", "gtmd", "opensky"]:
        item = source_items(registry).get(source_id, {})
        if item.get("source_type") == "direct_url":
            records.extend(_download_static_source(source_id, item, "mobility"))
        else:
            records.append(AcquisitionRecord(source_id, source_id, "metadata_only", str(item.get("url") or ""), "mobility_sources", "global", "background_feature", reason="metadata_only_or_credentials_required", license=str(item.get("license") or ""), expected_granularity=str(item.get("expected_granularity") or "")))
    manifest = append_manifest(records)
    inventory = {"status": "ok", "blocked_reason": "none", **manifest}
    inventory["downloaded_sources"] = sorted({row["source"] for row in inventory["records"] if row.get("status") == "downloaded"})
    write_report_pair("p1b_download_inventory", "P1b Download Inventory", inventory)
    return report_records("p1b_mobility_download_report", "P1b Mobility Download Report", records)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run P1b acquisition download groups.")
    parser.add_argument("group", choices=["case_sources", "admin_population_weather", "mobility_sources"])
    parser.add_argument("--config", default="configs/data_source_registry_v3.yaml")
    args = parser.parse_args()
    if args.group == "case_sources":
        payload = download_case_sources(args.config)
    elif args.group == "admin_population_weather":
        payload = download_admin_population_weather(args.config)
    else:
        payload = download_mobility_sources(args.config)
    print(json.dumps({"status": payload["status"], "summary": payload["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
