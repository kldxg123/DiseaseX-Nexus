from __future__ import annotations

from epidemic_core.forecast_model_kg.engine import guidance_for_request
from epidemic_core.forecast_model_kg.loader import load_graph, validate_graph
from epidemic_core.forecast_model_kg.service import normalize_country


def main() -> None:
    graph = load_graph()
    print(validate_graph(graph))
    pairs = [
        ("COVID-19", "France"),
        ("Influenza", "Peru"),
        ("Dengue", "Brazil"),
        ("Ebola", "COD"),
        ("Puumala", "Finland"),
        ("Legionellosis", "Austria"),
        ("Nipah", "Bangladesh"),
    ]
    for disease, country in pairs:
        result = guidance_for_request(
            disease=disease,
            country_or_region=country,
            forecast_target="cases_new",
            horizon=4,
        )
        labels = [m["label_en"] for m in result["recommended_model_families"]][:3]
        print(
            disease,
            "=>",
            result["resolved_disease"]["id"],
            result["eligibility_track"],
            labels,
            "warnings",
            len(result["warnings"]),
        )
    print(
        "country_alias",
        normalize_country("芬兰"),
        normalize_country("巴西"),
        normalize_country("奥地利"),
    )


if __name__ == "__main__":
    main()
