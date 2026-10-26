from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = "configs/config.yaml"


def project_root(start: str | Path | None = None) -> Path:
    """Return the nearest directory that looks like the project root."""
    here = Path(start or os.getcwd()).resolve()
    if here.is_file():
        here = here.parent
    for candidate in [here, *here.parents]:
        if (candidate / "configs").exists() or (candidate / "服务器信息.txt").exists():
            return candidate
    return here


def _load_json_or_yaml_subset(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(stripped)
        return loaded or {}
    except Exception:
        raise ValueError(
            "Config parsing failed. Install PyYAML or keep config files as JSON-compatible YAML."
        )


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    cfg_path = Path(path or os.environ.get("EBOLA_STEP_CONFIG", DEFAULT_CONFIG_PATH))
    if not cfg_path.is_absolute():
        cfg_path = project_root() / cfg_path
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {cfg_path}")
    return _load_json_or_yaml_subset(cfg_path.read_text(encoding="utf-8"))


def deep_get(mapping: dict[str, Any], dotted_key: str, default: Any = None) -> Any:
    current: Any = mapping
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def resolve_path(path: str | Path, root: str | Path | None = None) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return (Path(root) if root else project_root()) / p
