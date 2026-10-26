from model_benchmark_agent.model_registry import get_benchmark_model_specs, get_model_registry, get_model_spec


def test_model_registry_contains_executable_baselines_and_optional_models():
    registry = get_model_registry()
    assert registry["moving_average"].execution_status == "executable"
    assert registry["seasonal_naive"].execution_status == "executable"
    assert registry["random_forest"].execution_status in {
        "simulated_proxy",
        "skipped_optional_dependency",
    }
    assert registry["lightgbm"].execution_status in {
        "simulated_proxy",
        "fallback_hist_gradient_boosting_proxy",
        "skipped_optional_dependency",
    }
    assert registry["llm_agent"].recommended_for_m2_m1 is False


def test_get_benchmark_model_specs_first_batch():
    names = [spec.model_name for spec in get_benchmark_model_specs()]
    assert names == [
        "moving_average",
        "seasonal_naive",
        "random_forest",
        "gradient_boosting",
        "lightgbm",
        "arima_or_sarima",
    ]
    assert get_model_spec("moving-average").model_name == "moving_average"
