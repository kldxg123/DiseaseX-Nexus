from model_benchmark_agent.backtest_runner import (
    generate_synthetic_feature_records,
    load_fixture_records,
    run_benchmark_matrix,
    run_benchmark_task,
)
from model_benchmark_agent.benchmark_matrix import load_matrix


FIXTURE_MATRIX = "tests/fixtures/model_benchmark/benchmark_matrix_fixture.json"


def test_load_fixture_records_and_synthetic_records_have_targets():
    fixture = load_fixture_records("influenza")
    assert fixture
    assert fixture[0].target_value is not None
    synthetic = generate_synthetic_feature_records(
        disease="influenza",
        country="United States",
        horizons_days=[7],
        weeks=12,
    )
    assert len(synthetic) == 12
    assert synthetic[0].feature_json


def test_run_benchmark_task_executes_on_fixture():
    task = load_matrix(FIXTURE_MATRIX)[0]
    result = run_benchmark_task(task, synthetic_smoke_if_needed=True)
    assert result.status in {"executed", "simulated_proxy"}
    assert result.metrics["regression"]["n"] > 0
    assert result.test_rows > 0


def test_run_benchmark_matrix_returns_results():
    payload = run_benchmark_matrix(FIXTURE_MATRIX, synthetic_smoke_if_needed=True)
    assert payload["results"]
    assert all("feature_json" not in str(row) for row in payload["results"])
