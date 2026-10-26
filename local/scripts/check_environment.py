from __future__ import annotations

import importlib
import importlib.util
import json
import site
import sys
import tomllib
from pathlib import Path
from typing import Any

CORE_IMPORTS = (
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
)

REQUIRED_PACKAGE_PATTERNS = (
    "apps*",
    "common*",
    "crawler_agent*",
    "cleaner_agent*",
    "predictor_agent*",
    "worker_agent*",
    "crawler_optimizer_agent*",
    "data_quality_agent*",
    "source_discovery_agent*",
)

SENSITIVE_PACKAGE_TEXT = (
    "服务器信息.txt",
    ".env",
    "*.pem",
    "*.key",
)


def main(argv: list[str] | None = None) -> int:
    _ = argv
    root = Path(__file__).resolve().parents[1]
    payload = build_environment_report(root)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if payload.get("status") == "ok" else 1


def build_environment_report(root: Path | None = None) -> dict[str, Any]:
    project_root = root or Path(__file__).resolve().parents[1]
    pyproject_path = project_root / "pyproject.toml"
    pyproject_ok, pyproject_data, pyproject_error = _load_pyproject(pyproject_path)
    import_results = _check_imports()
    package_config = _check_package_config(pyproject_data if isinstance(pyproject_data, dict) else {})
    manual_path_workaround_found = _manual_path_workaround_found()
    pythonpath_workaround_found = _pythonpath_workaround_found(project_root)
    warnings: list[str] = []
    if manual_path_workaround_found:
        warnings.append("manual local path workaround detected; prefer standard editable installation.")
    if pythonpath_workaround_found:
        warnings.append("PYTHONPATH points at local source packages; prefer standard editable installation.")
    if not _module_available("setuptools"):
        warnings.append("setuptools is not importable in this environment.")
    if not pyproject_ok and pyproject_error:
        warnings.append("pyproject could not be parsed.")
    if package_config["missing_patterns"]:
        warnings.append("package discovery is missing expected project packages.")

    status = "ok"
    if not pyproject_ok or not all(import_results.values()) or package_config["missing_patterns"]:
        status = "failed"

    return _safe_json(
        {
            "status": status,
            "python_version": ".".join(str(item) for item in sys.version_info[:3]),
            "pyproject_ok": pyproject_ok,
            "pytest_available": _module_available("pytest"),
            "setuptools_available": _module_available("setuptools"),
            "source_discovery_agent_importable": bool(import_results.get("source_discovery_agent")),
            "imports": import_results,
            "package_config": package_config,
            "manual_path_workaround_found": manual_path_workaround_found,
            "pythonpath_workaround_found": pythonpath_workaround_found,
            "sensitive_file_pattern_reference_count": _sensitive_reference_count(pyproject_path),
            "warnings": warnings,
        }
    )


def _load_pyproject(path: Path) -> tuple[bool, dict[str, Any] | None, str | None]:
    try:
        with path.open("rb") as handle:
            return True, tomllib.load(handle), None
    except Exception as exc:
        return False, None, type(exc).__name__


def _check_imports() -> dict[str, bool]:
    results: dict[str, bool] = {}
    for module_name in CORE_IMPORTS:
        try:
            importlib.import_module(module_name)
        except Exception:
            results[module_name] = False
        else:
            results[module_name] = True
    return results


def _check_package_config(pyproject_data: dict[str, Any]) -> dict[str, Any]:
    find_config = (
        pyproject_data.get("tool", {})
        .get("setuptools", {})
        .get("packages", {})
        .get("find", {})
    )
    includes = list(find_config.get("include") or [])
    missing = [pattern for pattern in REQUIRED_PACKAGE_PATTERNS if pattern not in includes]
    return {
        "include_count": len(includes),
        "missing_patterns": missing,
        "include_package_data": bool(
            pyproject_data.get("tool", {}).get("setuptools", {}).get("include-package-data", True)
        ),
    }


def _module_available(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _manual_path_workaround_found() -> bool:
    candidates: list[Path] = []
    for value in list(site.getsitepackages()) + [site.getusersitepackages()]:
        if value:
            candidates.append(Path(value) / "aibola-local-packages.pth")
    return any(path.exists() for path in candidates)


def _pythonpath_workaround_found(project_root: Path) -> bool:
    import os

    pythonpath = os.environ.get("PYTHONPATH", "")
    if not pythonpath:
        return False
    packages_path = str(project_root / "packages").lower()
    values = [item.strip().lower() for item in pythonpath.split(os.pathsep) if item.strip()]
    return any(value == "packages" or value == packages_path for value in values)


def _sensitive_reference_count(pyproject_path: Path) -> int:
    try:
        text = pyproject_path.read_text(encoding="utf-8").lower()
    except OSError:
        return 0
    return sum(1 for pattern in SENSITIVE_PACKAGE_TEXT if pattern.lower() in text)


def _safe_json(value: Any) -> Any:
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for field_name, item in value.items():
            if _is_sensitive_text(str(field_name)):
                safe["redacted_field"] = "***"
            else:
                safe[field_name] = _safe_json(item)
        return safe
    if isinstance(value, list):
        return [_safe_json(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_json(item) for item in value]
    if isinstance(value, str):
        return "[redacted]" if _is_sensitive_text(value) else value
    return value


def _is_sensitive_text(value: str) -> bool:
    lowered = value.lower()
    return any(part in lowered for part in ("password", "token", "secret", "ssh_key_path"))


if __name__ == "__main__":
    raise SystemExit(main())
