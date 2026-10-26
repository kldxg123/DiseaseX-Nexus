from __future__ import annotations

from pathlib import Path


SECRET_FILE_PATTERNS = [
    "服务器信息.txt",
    "*服务器信息*",
    "server*.txt",
    "server*.json",
    "server*.yaml",
    "server*.yml",
    "remote*.yaml",
    "remote*.yml",
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa*",
]


def looks_like_secret_path(path: str | Path) -> bool:
    name = Path(path).name.lower()
    if "服务器信息" in Path(path).name:
        return True
    return (
        name.startswith(".env")
        or name.startswith("server")
        or name.startswith("remote")
        or name.startswith("id_rsa")
        or name.endswith((".pem", ".key"))
    )
