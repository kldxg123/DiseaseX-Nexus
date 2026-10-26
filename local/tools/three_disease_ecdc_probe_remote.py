from __future__ import annotations

import json
import urllib.parse
import urllib.request


BASE = "https://atlas.ecdc.europa.eu/public/AtlasService/rest/"


def get(path: str, params: dict[str, object] | None = None) -> object:
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(url, headers={"User-Agent": "epidemic-openclaw-ecdc-probe/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
        return json.loads(data)


def main() -> None:
    out: dict[str, object] = {}
    datasets = get("GetDatasets")
    all_datasets = datasets.get("Datasets", []) if isinstance(datasets, dict) else []
    matches = [d for d in all_datasets if "LEGI" in str(d.get("Code")) or "LEGI" in str(d.get("Label"))]
    out["dataset_matches"] = matches[:20]
    ds = next((d for d in all_datasets if d.get("Code") == "CURRENT.LEGI.YEARLY"), None)
    out["selected_dataset"] = ds
    if ds:
        dataset_id = ds["Id"]
        try:
            measures_raw = get("GetIndicatorMeasuresForHealthTopicAndDataset", {"healthTopicId": 30, "datasetId": dataset_id})
            measures = measures_raw.get("Measures", []) if isinstance(measures_raw, dict) else []
            out["selected_measures"] = [
                {
                    "Code": m.get("Code"),
                    "Id": m.get("Id"),
                    "Index": m.get("Index"),
                    "Label": m.get("Label"),
                    "Unit": m.get("Unit"),
                    "Type": m.get("Type"),
                    "Resolutions": m.get("ResolutionList", {}).get("Resolutions") if isinstance(m.get("ResolutionList"), dict) else None,
                }
                for m in measures
                if m.get("Code") in {"ALL.COUNT", "CONFIRMED.COUNT", "ALL.AGESTANDARDISED.RATE"}
                or "count" in str(m.get("Label", "")).lower()
            ][:20]
            all_count = next((m for m in measures if m.get("Code") == "ALL.COUNT"), None)
            if all_count:
                measure_id = all_count["Id"]
                tries = [
                    ("measures_results_all_countries", "GetMeasuresResults", {"healthTopicId": 30, "datasetId": dataset_id, "measurePopulation": 1, "measureIds": measure_id, "measureTypes": "I", "timeCodes": "all", "geoCodes": "all"}),
                    ("measures_results_de", "GetMeasureResultsForTimeUnitAndGeoRegion", {"measureId": measure_id, "timeUnit": "Y", "geoCode": "DE"}),
                    ("export_text", "GetMeasuresResultsExportText", {"healthTopicId": 30, "datasetId": dataset_id, "measurePopulation": 1, "measureIds": measure_id, "measureTypes": "I", "timeCodes": "all", "geoCodes": "all", "geoLevel": 2, "timeUnit": "Y", "isPivotByTimePeriod": "false"}),
                ]
                for nm, path, params in tries:
                    try:
                        val = get(path, params)
                        out[nm] = {"params": params, "prefix": json.dumps(val, ensure_ascii=False)[:6000], "keys": list(val.keys()) if isinstance(val, dict) else None}
                    except Exception as exc:
                        out[nm] = {"error": type(exc).__name__, "detail": str(exc)[:300], "params": params}
        except Exception as exc:
            out["measure_selection_error"] = {"error": type(exc).__name__, "detail": str(exc)[:300]}
        for name, path, params in [
            ("topics_for_dataset", "GetHealthTopicsforDataset", {"datasetId": dataset_id}),
            ("health_topics", "GetHealthTopics", {}),
            ("populations", "GetPopulationsForHealthTopicAndDataset", {"healthTopicId": 30, "datasetId": dataset_id}),
            ("indicator_measures", "GetIndicatorMeasuresForHealthTopicAndDataset", {"healthTopicId": 30, "datasetId": dataset_id}),
            ("measure_filters", "GetIndicatorFiltersForMeasureHealthTopicAndDataset", {"measureId": 1, "healthTopicId": 30, "datasetId": dataset_id}),
            ("geo_levels", "GetGeoLevelsForHealthTopicAndDataset", {"healthTopicId": 30, "datasetId": dataset_id}),
            ("time_units", "GetTimeUnitsForHealthTopicAndDataset", {"healthTopicId": 30, "datasetId": dataset_id}),
            ("regions", "GetRegions", {}),
            ("time_periods", "GetTimePeriods", {"datasetId": dataset_id}),
            (
                "data_try1",
                "GetFilteredData",
                {"datasetId": dataset_id, "healthTopicId": 30, "measureId": 1, "geoLevelId": 1, "timeUnitId": 3},
            ),
            (
                "data_try2",
                "GetFilteredData",
                {"datasetId": dataset_id, "healthTopicId": 30, "measureId": "ALL.COUNT"},
            ),
        ]:
            try:
                value = get(path, params)
                text = json.dumps(value, ensure_ascii=False)
                out[name] = {
                    "params": params,
                    "keys": list(value.keys()) if isinstance(value, dict) else None,
                    "prefix": text[:5000],
                }
            except Exception as exc:
                out[name] = {"error": type(exc).__name__, "detail": str(exc)[:300], "params": params}
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
