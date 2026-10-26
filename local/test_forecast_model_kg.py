from __future__ import annotations

from epidemic_core.forecast_model_kg.engine import guidance_for_request
from epidemic_core.forecast_model_kg.loader import load_graph, validate_graph
from epidemic_core.forecast_model_kg.service import disease_graph_response, guidance_response, metadata_response


REQUIRED_DISEASES = {
    "covid_19",
    "influenza",
    "dengue",
    "ebola",
    "hantavirus_puumala",
    "legionella",
    "nipah",
}


def test_graph_integrity_and_required_diseases() -> None:
    graph = load_graph()
    report = validate_graph(graph)
    assert report["schema_validation"] == "PASS"
    assert report["referential_integrity"] == "PASS"
    assert report["alias_integrity"] == "PASS"
    assert report["disease_coverage"] == "7_OF_7"
    disease_ids = {node["id"] for node in graph["nodes"] if node["type"] == "Disease"}
    assert REQUIRED_DISEASES <= disease_ids


def test_recommendation_cases_cover_project_diseases() -> None:
    cases = [
        ("COVID-19", "France", "covid_19"),
        ("Influenza", "Peru", "influenza"),
        ("Dengue", "Brazil", "dengue"),
        ("Ebola", "COD", "ebola"),
        ("Puumala", "Finland", "hantavirus_puumala"),
        ("Legionellosis", "Austria", "legionella"),
        ("Nipah", "Bangladesh", "nipah"),
    ]
    for disease, country, expected_id in cases:
        result = guidance_for_request(disease, country, "cases_new", 4)
        assert result["resolved_disease"]["id"] == expected_id
        assert result["recommended_model_families"]
        assert result["required_baselines"]
        assert result["required_validation"]
        assert result["side_effects"] == {
            "db_write": False,
            "training_started": False,
            "prediction_started": False,
            "external_network": False,
        }


def test_sparse_and_covariate_warnings_are_explicit() -> None:
    puumala = guidance_for_request("Puumala", "FIN", "cases_new", 4)
    legionella = guidance_for_request("Legionella", "Europe", "cases_new", 4)
    nipah = guidance_for_request("Nipah", "BGD", "cases_new", 4)
    assert puumala["missing_requirements"] == ["has_ecological_covariates"]
    assert {"has_weather", "has_pollution"} <= set(legionella["missing_requirements"])
    assert any("Europe" in warning for warning in legionella["warnings"])
    assert nipah["eligibility_track"] == "SPARSE_OUTBREAK"


def test_api_service_shapes_without_db_engine() -> None:
    metadata = metadata_response()
    assert metadata["integrity"]["disease_coverage"] == "7_OF_7"
    disease = disease_graph_response("Legionellosis")
    assert any(node["id"] == "legionella" for node in disease["nodes"])
    guidance = guidance_response(None, "Nipah", "BGD", "cases_new", 4)
    assert guidance["resolved_disease"]["id"] == "nipah"
