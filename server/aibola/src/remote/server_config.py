from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.remote.sanitizer import sanitized_dict
from src.utils.config import project_root


CONFIG_PATTERNS = [
    "server*.json",
    "server*.yaml",
    "server*.yml",
    "server*.txt",
    "ssh*.config",
    ".env",
    ".env.server",
    "deploy*.yaml",
    "remote*.yaml",
    "remote*.yml",
    "服务器信息.txt",
]


@dataclass
class ServerConfig:
    host: str
    port: int = 22
    user: str = ""
    password: str | None = None
    key_file: str | None = None
    remote_dir: str = "~/aibola"
    conda_env: str = "aibola"
    source_file: str | None = None

    def validate(self) -> None:
        if not self.host or not self.user:
            raise ValueError("Remote host and user are required.")
        if not isinstance(self.port, int) or self.port <= 0 or self.port > 65535:
            raise ValueError("Remote port must be an integer in 1..65535.")
        if self.password is not None and not str(self.password):
            raise ValueError("Password field is present but empty.")

    @property
    def secrets(self) -> list[str]:
        values = [self.host, self.user, str(self.port)]
        if self.password:
            values.append(self.password)
        if self.key_file:
            values.append(self.key_file)
        return [v for v in values if v]

    def sanitize(self) -> dict[str, Any]:
        return sanitized_dict(
            {
                "host": self.host,
                "port": "<remote-port>" if self.port else None,
                "user": self.user,
                "password": self.password,
                "key_file": self.key_file,
                "remote_dir": self.remote_dir,
                "conda_env": self.conda_env,
                "source_file": Path(self.source_file).name if self.source_file else None,
                "auth_method": "password" if self.password else ("key" if self.key_file else "ssh-agent"),
            }
        )

    @classmethod
    def from_project_root(cls, root: str | Path | None = None) -> "ServerConfig":
        found = discover_server_configs(root)
        if not found:
            raise FileNotFoundError("No server config found.")
        config = found[0]
        config.validate()
        return config


def _parse_kv_text(text: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in stripped:
            key, value = stripped.split("=", 1)
        elif ":" in stripped:
            key, value = stripped.split(":", 1)
        else:
            continue
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        mapping[key] = value
    return mapping


def _parse_json_or_yaml(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        loaded = json.loads(stripped)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        pass
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(stripped)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return _parse_kv_text(text)


def _normalise_mapping(mapping: dict[str, Any], source_file: str) -> ServerConfig | None:
    def pick(*keys: str) -> Any:
        for key in keys:
            if key in mapping and mapping[key] not in (None, ""):
                return mapping[key]
        return None

    host = pick("host", "hostname", "ip", "REMOTE_HOST", "remote_host")
    user = pick("user", "username", "REMOTE_USER", "remote_user")
    port_raw = pick("port", "REMOTE_PORT", "remote_port") or 22
    password = pick("password", "passwd", "pwd", "REMOTE_PASSWORD", "remote_password")
    key_file = pick("key_file", "identity_file", "REMOTE_KEY_FILE", "remote_key_file")
    remote_dir_raw = pick("remote_dir", "remote_project_dir", "REMOTE_PROJECT_DIR")
    remote_dir = _normalise_remote_aibola_dir(remote_dir_raw, explicit=remote_dir_raw is not None)
    conda_env = pick("conda_env", "env_name", "REMOTE_CONDA_ENV") or "aibola"
    if not host or not user:
        return None
    try:
        port = int(str(port_raw).strip())
    except ValueError:
        raise ValueError("Remote port is not an integer.")
    return ServerConfig(
        host=str(host).strip(),
        port=port,
        user=str(user).strip(),
        password=str(password).strip() if password else None,
        key_file=str(key_file).strip() if key_file else None,
        remote_dir=str(remote_dir).strip(),
        conda_env=str(conda_env).strip(),
        source_file=source_file,
    )


def _normalise_remote_aibola_dir(raw_value: Any | None, explicit: bool) -> str:
    if not explicit:
        return "~/aibola"
    base = str(raw_value).strip().rstrip("/")
    if not base:
        return "~/aibola"
    # Treat an already-normalized project directory as idempotent.
    if base == "aibola" or base.endswith("/aibola"):
        return base
    return f"{base}/aibola"


def _parse_positional_secret_file(path: Path, text: str) -> ServerConfig | None:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 4:
        return None
    host, port_raw, user, password = [_strip_possible_label(line) for line in lines]
    port_match = re.search(r"\d{1,5}", port_raw)
    if not port_match:
        raise ValueError("Four-line server config found, but port is not an integer.")
    port = int(port_match.group(0))
    if not host or not user or not password:
        raise ValueError("Four-line server config has an empty host, user, or password.")
    return ServerConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        source_file=str(path),
    )


def _strip_possible_label(line: str) -> str:
    for sep in ("=", ":", "："):
        if sep in line:
            return line.split(sep, 1)[1].strip().strip('"').strip("'")
    return line.strip()


def candidate_files(root: str | Path | None = None) -> list[Path]:
    base = project_root(root)
    files: list[Path] = []
    for pattern in CONFIG_PATTERNS:
        files.extend(base.glob(pattern))
    return sorted({f.resolve() for f in files if f.is_file()})


def discover_server_configs(root: str | Path | None = None) -> list[ServerConfig]:
    configs: list[ServerConfig] = []
    for path in candidate_files(root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        positional = _parse_positional_secret_file(path, text)
        if positional:
            configs.append(positional)
            continue
        mapping = _parse_json_or_yaml(text)
        config = _normalise_mapping(mapping, str(path))
        if config:
            configs.append(config)
    return configs
