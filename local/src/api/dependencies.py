from __future__ import annotations

from src.utils.config import load_config


def get_config() -> dict:
    return load_config()
