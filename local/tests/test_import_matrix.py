from __future__ import annotations

import importlib


def test_core_import_matrix() -> None:
    modules = [
        "common",
        "crawler_agent",
        "cleaner_agent",
        "predictor_agent",
        "worker_agent",
        "crawler_optimizer_agent",
        "data_quality_agent",
        "source_discovery_agent",
        "apps.api.main",
        "apps.dashboard.streamlit_app",
    ]

    for module_name in modules:
        assert importlib.import_module(module_name)
