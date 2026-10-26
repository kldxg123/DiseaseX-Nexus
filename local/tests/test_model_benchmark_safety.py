import json

from model_benchmark_agent.backtest_runner import run_benchmark_matrix
from model_benchmark_agent.benchmark_matrix import build_benchmark_matrix
from model_benchmark_agent.ranking import rank_benchmark_results


FORBIDDEN = [
    "服务器信息.txt",
    "password",
    "token",
    "secret",
    "ssh_key_path",
    "raw_text",
    "html",
    "metadata_json",
    "feature_json",
    "provenance_json",
    "lesson_json",
]


def test_model_benchmark_outputs_do_not_expose_sensitive_payloads(tmp_path):
    matrix = build_benchmark_matrix()
    matrix_text = json.dumps(matrix, ensure_ascii=False).lower()
    assert "feature_json" not in matrix_text

    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(json.dumps(matrix), encoding="utf-8")
    results = run_benchmark_matrix(matrix_path, synthetic_smoke_if_needed=True, max_tasks=3)
    ranking = rank_benchmark_results(results)
    combined = json.dumps({"results": results, "ranking": ranking}, ensure_ascii=False).lower()
    for term in FORBIDDEN:
        assert term.lower() not in combined
