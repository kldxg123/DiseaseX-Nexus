from __future__ import annotations

import importlib.util
from pathlib import Path


def main() -> None:
    test_path = Path("USER_HOME/projects/openclaw/tests/unit/test_forecast_model_kg.py")
    spec = importlib.util.spec_from_file_location("test_forecast_model_kg", test_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load test module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    tests = [
        module.test_graph_integrity_and_required_diseases,
        module.test_recommendation_cases_cover_project_diseases,
        module.test_sparse_and_covariate_warnings_are_explicit,
        module.test_api_service_shapes_without_db_engine,
    ]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")


if __name__ == "__main__":
    main()
