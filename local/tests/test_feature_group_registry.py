from model_benchmark_agent.feature_groups import (
    build_feature_availability_report,
    feature_keys_for_groups,
    filter_feature_json,
    get_feature_group_registry,
    get_feature_group_sets,
)


def test_feature_group_registry_marks_weather_and_mobility_planned():
    registry = get_feature_group_registry()
    assert registry["epidemiological_lag"].m1_status == "available"
    assert registry["weather"].m1_status == "planned"
    assert registry["mobility"].m1_status == "planned"
    assert "baseline_minimal" in get_feature_group_sets()


def test_feature_availability_detects_present_and_missing_fields():
    records = [
        {
            "feature_json": {
                "lag_1w_cases": 1,
                "rolling_4w_mean_cases": 2,
                "season_sin": 0.1,
                "positivity_rate": 0.2,
            }
        }
    ]
    report = build_feature_availability_report(records)
    groups = {item["name"]: item for item in report["groups"]}
    assert groups["epidemiological_lag"]["availability_status"] == "partially_available"
    assert groups["rolling_statistics"]["present_keys"]
    assert groups["weather"]["availability_status"] == "planned"


def test_filter_feature_json_keeps_only_requested_group_keys():
    keys = feature_keys_for_groups(["epidemiological_lag"])
    assert "lag_1w_cases" in keys
    filtered = filter_feature_json(
        {"lag_1w_cases": 3, "positivity_rate": 0.2},
        ["epidemiological_lag"],
    )
    assert filtered == {"lag_1w_cases": 3}
