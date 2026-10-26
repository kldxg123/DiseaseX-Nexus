"""Download selected public datasets that can improve Ebola trend prediction.

The downloader is intentionally conservative: it fetches open, model-relevant
datasets to the remote raw-data area and writes a provenance report. It does
not train models, start services, or pull large raw data back to local.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


REPORT_DIR = Path("data/outputs/reports")
RAW_DIR = Path("data/raw/external_selected")
USER_AGENT = "aibola-data-expansion/1.0 (research; public datasets)"
MAX_FILE_MB_DEFAULT = 850


@dataclass(frozen=True)
class Country:
    iso3: str
    iso2_lower: str
    name: str
    outbreak_scope: str
    geofabrik_slug: str | None = None


CORE_COUNTRIES = [
    Country("GIN", "gin", "Guinea", "west_africa_2014_2016", "guinea"),
    Country("LBR", "lbr", "Liberia", "west_africa_2014_2016", "liberia"),
    Country("SLE", "sle", "Sierra Leone", "west_africa_2014_2016", "sierra-leone"),
    Country("COD", "cod", "Democratic Republic of the Congo", "drc_2018_2020", "congo-democratic-republic"),
    Country("UGA", "uga", "Uganda", "uganda_2022", "uganda"),
]

CONTEXT_BOUNDARY_COUNTRIES = [
    Country("NGA", "nga", "Nigeria", "west_africa_imported_case_context"),
    Country("SEN", "sen", "Senegal", "west_africa_imported_case_context"),
    Country("MLI", "mli", "Mali", "west_africa_imported_case_context"),
]

SLOW_BOUNDARY_FALLBACKS = {
    ("UGA", "ADM2"): "geoBoundaries_UGA_ADM2_stalled_on_remote; use ADM1 plus HDX Uganda districts boundary candidate for now",
}

NASA_POINTS = [
    {
        "id": "guinea_gueckedou_west_africa_2014_2016",
        "scope": "west_africa_2014_2016",
        "lat": 8.5650,
        "lon": -10.1330,
        "start": "20140101",
        "end": "20161231",
    },
    {
        "id": "liberia_monrovia_west_africa_2014_2016",
        "scope": "west_africa_2014_2016",
        "lat": 6.3156,
        "lon": -10.8074,
        "start": "20140101",
        "end": "20161231",
    },
    {
        "id": "sierra_leone_kailahun_west_africa_2014_2016",
        "scope": "west_africa_2014_2016",
        "lat": 8.2789,
        "lon": -10.5730,
        "start": "20140101",
        "end": "20161231",
    },
    {
        "id": "drc_beni_north_kivu_2018_2020",
        "scope": "drc_2018_2020",
        "lat": 0.4911,
        "lon": 29.4731,
        "start": "20180801",
        "end": "20200630",
    },
    {
        "id": "drc_mbandaka_equateur_2018",
        "scope": "drc_2018_2020",
        "lat": 0.0478,
        "lon": 18.2558,
        "start": "20180501",
        "end": "20180731",
    },
    {
        "id": "uganda_mubende_2022",
        "scope": "uganda_2022",
        "lat": 0.5585,
        "lon": 31.3949,
        "start": "20220901",
        "end": "20230131",
    },
    {
        "id": "uganda_kampala_2022_context",
        "scope": "uganda_2022",
        "lat": 0.3476,
        "lon": 32.5825,
        "start": "20220901",
        "end": "20230131",
    },
]

HDX_QUERIES = [
    "Ebola cases",
    "Ebola health zone",
    "Ebola Democratic Republic of the Congo 2018 2020",
    "Ebola Uganda 2022 cases",
    "Ebola Liberia Sierra Leone Guinea cases",
    "Ebola situation report cases deaths",
    "Democratic Republic Congo health zones",
    "RDC zones de sante",
    "DRC health zone boundaries",
    "Uganda districts boundaries",
    "Liberia counties boundaries",
    "Sierra Leone districts boundaries",
    "Guinea prefectures boundaries",
    "Ebola treatment centre locations",
    "Ebola contact tracing",
    "Ebola vaccination DRC health zone",
]

REFERENCE_PAGES = [
    {
        "id": "wikipedia_west_africa_case_timeline",
        "source": "Wikipedia summary of WHO/OCHA/government situation reports",
        "url": "https://en.wikipedia.org/wiki/West_African_Ebola_virus_epidemic_timeline_of_reported_cases_and_deaths",
        "outbreak_scope": "west_africa_2014_2016",
        "value_for_prediction": "candidate_cumulative_case_death_time_series_for_manual_validation",
        "license_or_terms": "CC BY-SA; verify against WHO/OCHA before training use",
    },
    {
        "id": "wikipedia_kivu_ebola_epidemic",
        "source": "Wikipedia summary of WHO/DRC Ministry reports",
        "url": "https://en.wikipedia.org/wiki/Kivu_Ebola_epidemic",
        "outbreak_scope": "drc_2018_2020",
        "value_for_prediction": "candidate_event_timeline_health_zone_names_for_manual_validation",
        "license_or_terms": "CC BY-SA; verify against WHO/DRC Ministry before training use",
    },
    {
        "id": "wikipedia_equateur_2018_case_timeline",
        "source": "Wikipedia summary of WHO/DRC Ministry reports",
        "url": "https://en.wikipedia.org/wiki/2018_%C3%89quateur_province_Ebola_outbreak",
        "outbreak_scope": "drc_2018_equateur_context",
        "value_for_prediction": "candidate_small_outbreak_case_timeline_for_manual_validation",
        "license_or_terms": "CC BY-SA; verify against WHO/DRC Ministry before training use",
    },
    {
        "id": "wikipedia_uganda_2022_case_timeline",
        "source": "Wikipedia summary of WHO/Uganda Ministry reports",
        "url": "https://en.wikipedia.org/wiki/2022%E2%80%932023_Uganda_Ebola_outbreak",
        "outbreak_scope": "uganda_2022",
        "value_for_prediction": "candidate_district_case_death_table_and_intervention_timeline_for_manual_validation",
        "license_or_terms": "CC BY-SA; verify against WHO/Uganda Ministry before training use",
    },
    {
        "id": "wikipedia_list_of_ebola_outbreaks",
        "source": "Wikipedia summary table sourced to WHO",
        "url": "https://en.wikipedia.org/wiki/List_of_Ebola_outbreaks",
        "outbreak_scope": "global_outbreak_context",
        "value_for_prediction": "outbreak_scope_selection_and_context_only_not_training_truth",
        "license_or_terms": "CC BY-SA; context only",
    },
]


@dataclass
class DownloadRecord:
    dataset_id: str
    source: str
    status: str
    url: str
    local_path: str | None = None
    bytes: int | None = None
    sha256: str | None = None
    reason: str | None = None
    value_for_prediction: str | None = None
    outbreak_scope: str | None = None
    license_or_terms: str | None = None
    metadata: dict[str, Any] | None = None


def request_json(url: str, timeout: int = 60) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def head_size(url: str, timeout: int = 30) -> int | None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            return int(length) if length and length.isdigit() else None
    except Exception:
        return None


def safe_name(value: str) -> str:
    kept = [c.lower() if c.isalnum() else "_" for c in value]
    name = "".join(kept).strip("_")
    while "__" in name:
        name = name.replace("__", "_")
    return name[:120] or "dataset"


def download_file(url: str, path: Path, max_file_mb: int, timeout: int = 120) -> tuple[str, int | None, str | None, str | None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    max_bytes = max_file_mb * 1024 * 1024
    if path.exists() and path.stat().st_size > 0:
        return "downloaded", path.stat().st_size, file_sha256(path), "already_exists"
    expected_size = head_size(url)
    if expected_size and expected_size > max_bytes:
        return "skipped", expected_size, None, f"file_size_exceeds_limit_{max_file_mb}MB"
    free = shutil.disk_usage(path.parent).free
    if expected_size and free < expected_size + 5 * 1024 * 1024 * 1024:
        return "skipped", expected_size, None, "insufficient_remote_disk_free_space"
    tmp = path.with_suffix(path.suffix + ".tmp")
    sha = hashlib.sha256()
    total = 0
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as response, tmp.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    handle.close()
                    tmp.unlink(missing_ok=True)
                    return "skipped", total, None, f"file_size_exceeds_limit_{max_file_mb}MB"
                sha.update(chunk)
                handle.write(chunk)
        tmp.replace(path)
        return "downloaded", total, sha.hexdigest(), None
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        return "failed", total or expected_size, None, f"{type(exc).__name__}: {str(exc)[:160]}"


def file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    return sha.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_progress(records: list[DownloadRecord], max_file_mb: int, status: str = "running") -> None:
    record_dicts = [asdict(r) for r in records]
    summary = {
        "total_records": len(record_dicts),
        "downloaded": sum(1 for r in record_dicts if r["status"] == "downloaded"),
        "failed": sum(1 for r in record_dicts if r["status"] == "failed"),
        "skipped": sum(1 for r in record_dicts if r["status"] == "skipped"),
        "downloaded_bytes": sum(int(r.get("bytes") or 0) for r in record_dicts if r["status"] == "downloaded"),
    }
    payload = {
        "status": status,
        "blocked_reason": "none",
        "download_root": RAW_DIR.as_posix(),
        "max_file_mb": max_file_mb,
        "summary": summary,
        "records": record_dicts,
        "recommended_next_action": "inspect_hdx_case_resources_then_build_real_external_feature_panel",
    }
    write_json(REPORT_DIR / "external_dataset_download_report.json", payload)
    (REPORT_DIR / "external_dataset_download_report.md").write_text(build_markdown(payload), encoding="utf-8")
    write_json(RAW_DIR / "selected_external_dataset_manifest.json", payload)


def download_geoboundaries(max_file_mb: int, include_context_boundaries: bool = False) -> list[DownloadRecord]:
    records: list[DownloadRecord] = []
    countries = CORE_COUNTRIES + (CONTEXT_BOUNDARY_COUNTRIES if include_context_boundaries else [])
    for country in countries:
        for adm in ["ADM1", "ADM2"]:
            dataset_id = f"geoboundaries_{country.iso3}_{adm}"
            api = f"https://www.geoboundaries.org/api/current/gbOpen/{country.iso3}/{adm}/"
            if (country.iso3, adm) in SLOW_BOUNDARY_FALLBACKS:
                records.append(
                    DownloadRecord(
                        dataset_id=dataset_id,
                        source="geoBoundaries gbOpen",
                        status="skipped",
                        url=api,
                        reason=SLOW_BOUNDARY_FALLBACKS[(country.iso3, adm)],
                        value_for_prediction="adm2_mapping_candidate_from_alternate_hdx_source",
                        outbreak_scope=country.outbreak_scope,
                        license_or_terms="gbOpen/CC-BY-compatible; alternate source needed",
                        metadata={"country": country.name, "adm": adm, "fallback": "HDX Uganda districts boundaries"},
                    )
                )
                continue
            metadata_path = RAW_DIR / "geoboundaries" / country.iso3 / f"{adm.lower()}_metadata.json"
            try:
                metadata = request_json(api)
                write_json(metadata_path, metadata)
                url = metadata.get("gjDownloadURL") or metadata.get("simplifiedGeometryGeoJSON")
                if not url:
                    records.append(DownloadRecord(dataset_id, "geoBoundaries", "failed", api, reason="missing_geojson_download_url", metadata={"metadata_path": metadata_path.as_posix()}))
                    continue
                target = RAW_DIR / "geoboundaries" / country.iso3 / f"{country.iso3}_{adm}.geojson"
                status, size, sha, reason = download_file(url, target, max_file_mb)
                records.append(
                    DownloadRecord(
                        dataset_id=dataset_id,
                        source="geoBoundaries gbOpen",
                        status=status,
                        url=url,
                        local_path=target.as_posix() if status == "downloaded" else None,
                        bytes=size,
                        sha256=sha,
                        reason=reason,
                        value_for_prediction="adm1_adm2_mapping_centroids_admin_match_confidence",
                        outbreak_scope=country.outbreak_scope,
                        license_or_terms=str(metadata.get("boundaryLicense") or "gbOpen/CC-BY-compatible; attribution required"),
                        metadata={"api": api, "metadata_path": metadata_path.as_posix(), "country": country.name, "adm": adm},
                    )
                )
            except Exception as exc:
                records.append(DownloadRecord(dataset_id, "geoBoundaries gbOpen", "failed", api, reason=f"{type(exc).__name__}: {str(exc)[:160]}", outbreak_scope=country.outbreak_scope))
    return records


def download_worldpop(max_file_mb: int) -> list[DownloadRecord]:
    records: list[DownloadRecord] = []
    for country in CORE_COUNTRIES:
        # 2020 is available across the current WorldPop Global_2000_2020 archive and
        # is close to the DRC 2018-2020 outbreak while still usable as a proxy for
        # West Africa and Uganda until event-year rasters are added.
        year = 2020
        url = f"https://data.worldpop.org/GIS/Population/Global_2000_2020/{year}/{country.iso3}/{country.iso2_lower}_ppp_{year}_UNadj.tif"
        target = RAW_DIR / "worldpop" / country.iso3 / f"{country.iso2_lower}_ppp_{year}_UNadj.tif"
        status, size, sha, reason = download_file(url, target, max_file_mb, timeout=240)
        records.append(
            DownloadRecord(
                dataset_id=f"worldpop_{country.iso3}_{year}_ppp_unadj",
                source="WorldPop population counts",
                status=status,
                url=url,
                local_path=target.as_posix() if status == "downloaded" else None,
                bytes=size,
                sha256=sha,
                reason=reason,
                value_for_prediction="population_density_population_normalized_incidence",
                outbreak_scope=country.outbreak_scope,
                license_or_terms="Creative Commons Attribution 4.0 International License",
                metadata={"country": country.name, "year": year, "resolution": "approximately 100m source / archive GeoTIFF"},
            )
        )
    return records


def download_geofabrik(max_file_mb: int) -> list[DownloadRecord]:
    records: list[DownloadRecord] = []
    for country in CORE_COUNTRIES:
        if not country.geofabrik_slug:
            continue
        url = f"https://download.geofabrik.de/africa/{country.geofabrik_slug}-latest.osm.pbf"
        target = RAW_DIR / "geofabrik_osm" / country.iso3 / f"{country.geofabrik_slug}-latest.osm.pbf"
        status, size, sha, reason = download_file(url, target, max_file_mb, timeout=600)
        records.append(
            DownloadRecord(
                dataset_id=f"geofabrik_osm_{country.iso3}",
                source="Geofabrik OpenStreetMap extract",
                status=status,
                url=url,
                local_path=target.as_posix() if status == "downloaded" else None,
                bytes=size,
                sha256=sha,
                reason=reason,
                value_for_prediction="road_density_nearest_primary_road_cross_border_accessibility",
                outbreak_scope=country.outbreak_scope,
                license_or_terms="OpenStreetMap ODbL 1.0 via Geofabrik",
                metadata={"country": country.name, "format": "osm.pbf"},
            )
        )
    return records


def download_nasa_power(max_file_mb: int) -> list[DownloadRecord]:
    records: list[DownloadRecord] = []
    params = "PRECTOTCORR,T2M,T2M_MAX,T2M_MIN,RH2M,WS2M"
    for point in NASA_POINTS:
        query = urllib.parse.urlencode(
            {
                "parameters": params,
                "community": "AG",
                "longitude": point["lon"],
                "latitude": point["lat"],
                "start": point["start"],
                "end": point["end"],
                "format": "JSON",
                "time-standard": "UTC",
            }
        )
        url = f"https://power.larc.nasa.gov/api/temporal/daily/point?{query}"
        target = RAW_DIR / "nasa_power" / point["scope"] / f"{point['id']}.json"
        status, size, sha, reason = download_file(url, target, max_file_mb, timeout=180)
        records.append(
            DownloadRecord(
                dataset_id=f"nasa_power_{point['id']}",
                source="NASA POWER Daily API",
                status=status,
                url=url,
                local_path=target.as_posix() if status == "downloaded" else None,
                bytes=size,
                sha256=sha,
                reason=reason,
                value_for_prediction="cross_source_weather_rainfall_temperature_humidity_wind",
                outbreak_scope=str(point["scope"]),
                license_or_terms="NASA POWER public API terms",
                metadata={k: point[k] for k in ["lat", "lon", "start", "end", "scope"]},
            )
        )
        time.sleep(0.2)
    return records


def download_reference_pages(max_file_mb: int) -> list[DownloadRecord]:
    records: list[DownloadRecord] = []
    for page in REFERENCE_PAGES:
        target = RAW_DIR / "reference_pages" / f"{page['id']}.html"
        status, size, sha, reason = download_file(page["url"], target, max_file_mb, timeout=120)
        records.append(
            DownloadRecord(
                dataset_id=page["id"],
                source=page["source"],
                status=status,
                url=page["url"],
                local_path=target.as_posix() if status == "downloaded" else None,
                bytes=size,
                sha256=sha,
                reason=reason,
                value_for_prediction=page["value_for_prediction"],
                outbreak_scope=page["outbreak_scope"],
                license_or_terms=page["license_or_terms"],
                metadata={"training_use": "candidate_for_manual_validation_only"},
            )
        )
    return records


def hdx_resource_worth_downloading(resource: dict[str, Any]) -> tuple[bool, str]:
    name = " ".join(str(resource.get(k) or "") for k in ["name", "description", "format", "url"])
    lower = name.lower()
    fmt = str(resource.get("format") or "").lower().strip()
    if not resource.get("url"):
        return False, "missing_url"
    allowed_format = any(token in fmt for token in ["csv", "xls", "xlsx", "json", "geojson", "zip", "shp"])
    allowed_url = any(resource["url"].lower().split("?")[0].endswith(ext) for ext in [".csv", ".xls", ".xlsx", ".json", ".geojson", ".zip"])
    if not (allowed_format or allowed_url):
        return False, "unsupported_format"
    positive = [
        "ebola",
        "evd",
        "case",
        "death",
        "situation",
        "surveillance",
        "health zone",
        "zone de sante",
        "zone de santé",
        "district",
        "prefecture",
        "county",
        "admin",
        "boundary",
        "cod",
        "drc",
        "rdc",
        "uganda",
        "guinea",
        "liberia",
        "sierra leone",
    ]
    if not any(term in lower for term in positive):
        return False, "no_case_or_geo_signal_terms"
    negative = ["emergency vehicle", "external ventricular", "drug", "molecule", "protein", "vaccine candidate"]
    if any(term in lower for term in negative):
        return False, "non_ebola_disease_evd_or_non_surveillance"
    return True, "selected"


def download_hdx(max_file_mb: int, max_resources: int = 60) -> list[DownloadRecord]:
    records: list[DownloadRecord] = []
    seen_urls: set[str] = set()
    downloaded = 0
    for query in HDX_QUERIES:
        api = "https://data.humdata.org/api/3/action/package_search?" + urllib.parse.urlencode({"q": query, "rows": 12})
        try:
            payload = request_json(api, timeout=90)
            packages = payload.get("result", {}).get("results", []) if isinstance(payload, dict) else []
        except Exception as exc:
            records.append(DownloadRecord(f"hdx_query_{safe_name(query)}", "HDX CKAN package_search", "failed", api, reason=f"{type(exc).__name__}: {str(exc)[:160]}", value_for_prediction="case_series_or_admin_event_candidate"))
            continue
        for pkg in packages:
            package_title = str(pkg.get("title") or pkg.get("name") or "")
            package_name = str(pkg.get("name") or safe_name(package_title))
            for resource in pkg.get("resources") or []:
                if downloaded >= max_resources:
                    break
                url = str(resource.get("url") or "")
                if not url or url in seen_urls:
                    continue
                selected, reason = hdx_resource_worth_downloading(resource)
                if not selected:
                    records.append(
                        DownloadRecord(
                            dataset_id=f"hdx_{safe_name(package_name)}_{safe_name(str(resource.get('name') or 'resource'))}",
                            source="HDX",
                            status="skipped",
                            url=url,
                            reason=reason,
                            value_for_prediction="case_series_or_admin_event_candidate",
                            metadata={"query": query, "package": package_title, "resource": resource.get("name"), "format": resource.get("format")},
                        )
                    )
                    continue
                seen_urls.add(url)
                suffix = Path(urllib.parse.urlparse(url).path).suffix or "." + safe_name(str(resource.get("format") or "dat"))
                rel_name = f"{safe_name(package_name)}__{safe_name(str(resource.get('name') or 'resource'))}{suffix}"
                target = RAW_DIR / "hdx" / rel_name
                status, size, sha, dl_reason = download_file(url, target, max_file_mb, timeout=300)
                if status == "downloaded":
                    downloaded += 1
                records.append(
                    DownloadRecord(
                        dataset_id=f"hdx_{safe_name(package_name)}_{safe_name(str(resource.get('id') or resource.get('name') or 'resource'))}",
                        source="HDX",
                        status=status,
                        url=url,
                        local_path=target.as_posix() if status == "downloaded" else None,
                        bytes=size,
                        sha256=sha,
                        reason=dl_reason,
                        value_for_prediction="candidate_case_series_admin_boundary_or_situation_report",
                        outbreak_scope="to_be_classified_by_metadata",
                        license_or_terms=str(pkg.get("license_title") or pkg.get("license_id") or "see HDX package metadata"),
                        metadata={"query": query, "package": package_title, "package_name": package_name, "resource": resource.get("name"), "format": resource.get("format")},
                    )
                )
            if downloaded >= max_resources:
                break
    return records


def build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Selected External Dataset Download Report",
        "",
        f"status: {payload['status']}",
        f"blocked_reason: {payload['blocked_reason']}",
        f"download_root: `{payload['download_root']}`",
        "",
        "## Summary",
        "",
        f"- total_records: {payload['summary']['total_records']}",
        f"- downloaded: {payload['summary']['downloaded']}",
        f"- failed: {payload['summary']['failed']}",
        f"- skipped: {payload['summary']['skipped']}",
        f"- downloaded_bytes: {payload['summary']['downloaded_bytes']}",
        "",
        "## Downloaded Datasets",
        "",
        "| dataset_id | source | scope | bytes | path | value_for_prediction |",
        "|---|---|---:|---:|---|---|",
    ]
    for rec in payload["records"]:
        if rec["status"] != "downloaded":
            continue
        lines.append(
            "| {dataset_id} | {source} | {outbreak_scope} | {bytes} | `{local_path}` | {value_for_prediction} |".format(
                dataset_id=rec["dataset_id"],
                source=rec["source"],
                outbreak_scope=rec.get("outbreak_scope") or "",
                bytes=rec.get("bytes") or 0,
                local_path=rec.get("local_path") or "",
                value_for_prediction=rec.get("value_for_prediction") or "",
            )
        )
    lines.extend(["", "## Failed Or Skipped Worth Reviewing", "", "| dataset_id | source | status | reason | url |", "|---|---|---|---|---|"])
    for rec in payload["records"]:
        if rec["status"] == "downloaded":
            continue
        if rec["status"] == "skipped" and rec.get("reason") in {"unsupported_format", "no_case_or_geo_signal_terms"}:
            continue
        lines.append(
            "| {dataset_id} | {source} | {status} | {reason} | {url} |".format(
                dataset_id=rec["dataset_id"],
                source=rec["source"],
                status=rec["status"],
                reason=(rec.get("reason") or "").replace("|", "/"),
                url=rec.get("url") or "",
            )
        )
    lines.extend(
        [
            "",
            "## Use In Next Accuracy Pass",
            "",
            "- geoBoundaries: replace blank adm1/adm2 matching and reduce country-centroid fallback.",
            "- WorldPop: replace default population proxy and improve population-normalized incidence.",
            "- Geofabrik OSM: build real road-density, nearest-primary-road, and cross-border-accessibility features.",
            "- NASA POWER: cross-check Open-Meteo rainfall/temperature/humidity and add outbreak-scope weather features.",
            "- HDX resources: manually inspect downloaded candidate case/situation/admin files; only mapped date-country-admin records should enter training.",
            "",
            "## Safety",
            "",
            "- No Qwen/LoRA training was started.",
            "- No model weights or LoRA weights were downloaded.",
            "- Raw datasets remain on remote only; pull_reports should only pull this report and manifests.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Download selected public external datasets to remote raw data.")
    parser.add_argument("--max-file-mb", type=int, default=MAX_FILE_MB_DEFAULT)
    parser.add_argument("--skip-hdx", action="store_true")
    parser.add_argument("--skip-worldpop", action="store_true")
    parser.add_argument("--skip-geofabrik", action="store_true")
    parser.add_argument("--skip-reference-pages", action="store_true")
    parser.add_argument("--include-context-boundaries", action="store_true")
    args = parser.parse_args()

    socket.setdefaulttimeout(90)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    records: list[DownloadRecord] = []
    records.extend(download_geoboundaries(args.max_file_mb, include_context_boundaries=args.include_context_boundaries))
    write_progress(records, args.max_file_mb)
    if args.skip_worldpop:
        for country in CORE_COUNTRIES:
            records.append(
                DownloadRecord(
                    dataset_id=f"worldpop_{country.iso3}_2020_ppp_unadj",
                    source="WorldPop population counts",
                    status="skipped",
                    url=f"https://data.worldpop.org/GIS/Population/Global_2000_2020/2020/{country.iso3}/{country.iso2_lower}_ppp_2020_UNadj.tif",
                    reason="heavy_download_deferred_for_background_resume",
                    value_for_prediction="population_density_population_normalized_incidence",
                    outbreak_scope=country.outbreak_scope,
                    license_or_terms="Creative Commons Attribution 4.0 International License",
                    metadata={"country": country.name, "deferred": True},
                )
            )
    else:
        records.extend(download_worldpop(args.max_file_mb))
    write_progress(records, args.max_file_mb)
    if args.skip_geofabrik:
        for country in CORE_COUNTRIES:
            records.append(
                DownloadRecord(
                    dataset_id=f"geofabrik_osm_{country.iso3}",
                    source="Geofabrik OpenStreetMap extract",
                    status="skipped",
                    url=f"https://download.geofabrik.de/africa/{country.geofabrik_slug}-latest.osm.pbf" if country.geofabrik_slug else "",
                    reason="heavy_download_deferred_for_background_resume",
                    value_for_prediction="road_density_nearest_primary_road_cross_border_accessibility",
                    outbreak_scope=country.outbreak_scope,
                    license_or_terms="OpenStreetMap ODbL 1.0 via Geofabrik",
                    metadata={"country": country.name, "deferred": True},
                )
            )
    else:
        records.extend(download_geofabrik(args.max_file_mb))
    write_progress(records, args.max_file_mb)
    records.extend(download_nasa_power(args.max_file_mb))
    write_progress(records, args.max_file_mb)
    if args.skip_reference_pages:
        for page in REFERENCE_PAGES:
            records.append(
                DownloadRecord(
                    dataset_id=page["id"],
                    source=page["source"],
                    status="skipped",
                    url=page["url"],
                    reason="webpage_download_deferred; URL cataloged for manual validation",
                    value_for_prediction=page["value_for_prediction"],
                    outbreak_scope=page["outbreak_scope"],
                    license_or_terms=page["license_or_terms"],
                    metadata={"training_use": "candidate_for_manual_validation_only", "deferred": True},
                )
            )
    else:
        records.extend(download_reference_pages(args.max_file_mb))
    write_progress(records, args.max_file_mb)
    if not args.skip_hdx:
        records.extend(download_hdx(args.max_file_mb))
        write_progress(records, args.max_file_mb)
    write_progress(records, args.max_file_mb, status="ok")
    record_dicts = [asdict(r) for r in records]
    summary = {
        "total_records": len(record_dicts),
        "downloaded": sum(1 for r in record_dicts if r["status"] == "downloaded"),
        "failed": sum(1 for r in record_dicts if r["status"] == "failed"),
        "skipped": sum(1 for r in record_dicts if r["status"] == "skipped"),
        "downloaded_bytes": sum(int(r.get("bytes") or 0) for r in record_dicts if r["status"] == "downloaded"),
    }
    print(json.dumps({"status": "ok", "summary": summary, "report": "data/outputs/reports/external_dataset_download_report.md"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
