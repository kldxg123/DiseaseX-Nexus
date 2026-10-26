from __future__ import annotations

import argparse
import math
from collections import defaultdict

from src.utils.config import load_config, resolve_path
from src.utils.io import read_table, write_json, write_table


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def gravity_flow(pop_origin: float, pop_destination: float, distance_km: float, accessibility: float = 1.0) -> float:
    distance = max(distance_km, 1.0)
    return max(pop_origin, 0.0) * max(pop_destination, 0.0) * max(accessibility, 0.0) / (distance * distance)


def infectious_inflow_index(destination: dict, origins: list[dict]) -> float:
    dest_pop = float(destination.get("population", destination.get("population_density", 1.0)) or 1.0)
    score = 0.0
    for origin in origins:
        if origin is destination:
            continue
        distance = origin.get("distance_km")
        if distance is None:
            distance = haversine_km(
                float(origin.get("lat", 0.0) or 0.0),
                float(origin.get("lon", 0.0) or 0.0),
                float(destination.get("lat", 0.0) or 0.0),
                float(destination.get("lon", 0.0) or 0.0),
            )
        flow = origin.get("flow_to_destination")
        if flow is None:
            flow = gravity_flow(
                float(origin.get("population", 1.0) or 1.0),
                dest_pop,
                float(distance),
                float(origin.get("road_accessibility_index", 1.0) or 1.0),
            )
        active_cases = float(origin.get("active_cases", origin.get("new_cases_14d_sum", 0.0)) or 0.0)
        population = max(float(origin.get("population", origin.get("population_density", 1.0)) or 1.0), 1.0)
        score += float(flow) * active_cases / population
    return score


def build_mobility_features(region_rows: list[dict]) -> list[dict]:
    output = []
    for row in region_rows:
        others = [candidate for candidate in region_rows if candidate is not row]
        enriched = dict(row)
        enriched["infectious_inflow_index"] = infectious_inflow_index(row, others)
        enriched["mobility_pressure_index"] = enriched["infectious_inflow_index"]
        output.append(enriched)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Build minimal P0a mobility proxy features.")
    parser.add_argument("--config", default="configs/config.yaml")
    args = parser.parse_args()
    cfg = load_config(args.config)
    in_path = resolve_path(cfg["paths"]["data_processed"]) / "region_metadata.parquet"
    records = read_table(in_path)
    features = build_mobility_features(records)
    write_table(resolve_path(cfg["paths"]["data_processed"]) / "mobility_features.parquet", features)
    write_json(
        resolve_path(cfg["paths"]["outputs"]) / "reports" / "mobility_proxy_report.json",
        {
            "input_records": len(records),
            "feature_records": len(features),
            "status": "ok" if features else "empty",
            "data_quality_flag": "" if features else "mobility_proxy_unavailable",
            "interpretation": "gravity_flow and infectious_inflow_index are local proxy estimates, not real daily traffic volume.",
        },
    )
    print(f"mobility_feature_records={len(features)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
