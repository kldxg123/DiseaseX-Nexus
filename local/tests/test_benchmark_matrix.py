from model_benchmark_agent.benchmark_matrix import build_benchmark_matrix, load_matrix, matrix_to_markdown
from model_benchmark_agent.report import write_benchmark_matrix_reports
from model_benchmark_agent.types import BenchmarkRunConfig


def test_build_benchmark_matrix_covers_diseases_horizons_models_and_feature_sets(tmp_path):
    matrix = build_benchmark_matrix(config=BenchmarkRunConfig())
    tasks = matrix["tasks"]
    assert {"influenza", "norovirus"} <= {task["disease"] for task in tasks}
    assert {7, 14, 28} <= {task["horizon_days"] for task in tasks}
    assert "moving_average" in {task["model_name"] for task in tasks}
    assert "source_quality_augmented" in matrix["feature_group_sets"]
    paths = write_benchmark_matrix_reports(matrix, tmp_path)
    loaded = load_matrix(paths["json"])
    assert loaded
    assert "Benchmark Matrix" in matrix_to_markdown(matrix)
