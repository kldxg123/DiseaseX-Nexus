import tempfile
from pathlib import Path

from src.remote.sanitizer import sanitize_text
from src.remote.server_config import discover_server_configs


def test_sanitizer_removes_host_user_password():
    text = "host=SERVER_HOST user=root password=secret"
    sanitized = sanitize_text(text, ["secret", "root"])
    assert "SERVER_HOST" not in sanitized
    assert "secret" not in sanitized
    assert "<remote-host>" in sanitized


def test_four_line_server_config_sanitizes():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "服务器信息.txt"
        path.write_text("SERVER_HOST\n22\nadmin\nsecret\n", encoding="utf-8")
        configs = discover_server_configs(td)
        assert len(configs) == 1
        assert configs[0].remote_dir == "~/aibola"
        summary = configs[0].sanitize()
        assert summary["host"] == "<remote-host>"
        assert summary["user"] == "<remote-user>"
        assert summary["password"] == "<redacted>"


def test_explicit_remote_dir_is_used_as_aibola_parent():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "server.yaml"
        path.write_text(
            "\n".join(
                [
                    "host: SERVER_HOST",
                    "port: 22",
                    "user: admin",
                    "password: secret",
                    "remote_dir: /srv/projects",
                ]
            ),
            encoding="utf-8",
        )
        configs = discover_server_configs(td)
        assert len(configs) == 1
        assert configs[0].remote_dir == "/srv/projects/aibola"


def test_explicit_aibola_remote_dir_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "server.yaml"
        path.write_text(
            "\n".join(
                [
                    "host: SERVER_HOST",
                    "port: 22",
                    "user: admin",
                    "password: secret",
                    "remote_dir: ~/aibola",
                ]
            ),
            encoding="utf-8",
        )
        configs = discover_server_configs(td)
        assert len(configs) == 1
        assert configs[0].remote_dir == "~/aibola"
