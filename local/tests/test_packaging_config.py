from __future__ import annotations

import tomllib
from pathlib import Path


def _pyproject() -> dict:
    with Path("pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_pyproject_build_system_and_dependency_groups_are_reproducible() -> None:
    data = _pyproject()

    assert data["build-system"]["build-backend"] == "editable_backend"
    assert data["build-system"]["backend-path"] == ["tools"]
    assert data["build-system"]["requires"] == []
    assert "pytest>=8" in data["project"]["optional-dependencies"]["dev"]
    assert "ruff>=0.5" not in data["project"]["optional-dependencies"]["dev"]
    assert "ruff>=0.5" in data["project"]["optional-dependencies"]["lint"]


def test_pyproject_package_discovery_covers_local_import_roots() -> None:
    data = _pyproject()
    find_config = data["tool"]["setuptools"]["packages"]["find"]
    includes = set(find_config["include"])

    assert data["tool"]["setuptools"]["include-package-data"] is False
    assert {
        "apps*",
        "common*",
        "crawler_agent*",
        "cleaner_agent*",
        "predictor_agent*",
        "worker_agent*",
        "crawler_optimizer_agent*",
        "data_quality_agent*",
        "source_discovery_agent*",
        "scripts*",
    }.issubset(includes)
    assert {"tests*", "migrations*", "infra*", "reports*"}.issubset(set(find_config["exclude"]))


def test_pyproject_console_scripts_include_source_discovery() -> None:
    data = _pyproject()
    scripts = data["project"]["scripts"]

    assert scripts["source-discovery-agent"] == "source_discovery_agent.cli:main"
    assert scripts["epidemic-source-discovery"] == "source_discovery_agent.cli:main"


def test_pyproject_does_not_reference_sensitive_file_patterns() -> None:
    rendered = Path("pyproject.toml").read_text(encoding="utf-8").lower()

    assert "服务器信息.txt" not in rendered
    assert ".env" not in rendered
    assert "*.pem" not in rendered
    assert "*.key" not in rendered
    assert "ssh_key_path" not in rendered
