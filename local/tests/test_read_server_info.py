from __future__ import annotations

import json

import pytest

from scripts.read_server_info import (
    MASK,
    ServerInfoError,
    main,
    parse_server_info_text,
    read_server_info,
    sanitize_server_info_record,
)


def _server_info_text() -> str:
    return "\n".join(
        [
            "HOST=SERVER_HOST",
            "PORT=2222",
            "USER=deploy-user",
            "SSH_KEY_PATH=/tmp/fake_id_rsa",
            "DEPLOY_PATH=/tmp/fake-deploy",
            "DOMAIN=example.invalid",
        ]
    )


def test_read_key_value_format_from_temp_file(tmp_path) -> None:
    path = tmp_path / "server-info.txt"
    path.write_text(_server_info_text(), encoding="utf-8")

    info = read_server_info(path)

    assert info.host == "SERVER_HOST"
    assert info.port == 2222
    assert info.user == "deploy-user"
    assert info.ssh_key_path == "/tmp/fake_id_rsa"
    assert info.deploy_path == "/tmp/fake-deploy"
    assert info.domain == "example.invalid"


def test_parse_json_format() -> None:
    text = json.dumps(
        {
            "HOST": "SERVER_HOST",
            "PORT": 22,
            "USER": "json-user",
            "SSH_KEY_PATH": "/tmp/json_id_rsa",
            "DEPLOY_PATH": "/tmp/json-deploy",
        }
    )

    info = parse_server_info_text(text)

    assert info.host == "SERVER_HOST"
    assert info.port == 22
    assert info.user == "json-user"


def test_parse_colon_format() -> None:
    info = parse_server_info_text(
        "\n".join(
            [
                "host: SERVER_HOST",
                "port: 2200",
                "user: colon-user",
                "ssh_key_path: /tmp/colon_id_rsa",
                "deploy_path: /tmp/colon-deploy",
            ]
        )
    )

    assert info.host == "SERVER_HOST"
    assert info.port == 2200
    assert info.user == "colon-user"


def test_repr_and_redacted_dict_do_not_expose_sensitive_values() -> None:
    info = parse_server_info_text(_server_info_text())

    rendered = repr(info)
    redacted = info.redacted_dict()

    assert "SERVER_HOST" not in rendered
    assert "deploy-user" not in rendered
    assert "/tmp/fake_id_rsa" not in rendered
    assert redacted["host"] == MASK
    assert redacted["user"] == MASK
    assert redacted["ssh_key_path"] == MASK
    assert redacted["deploy_path"] == MASK
    assert redacted["domain"] == MASK


def test_sanitize_server_info_record_masks_sensitive_fields() -> None:
    sanitized = sanitize_server_info_record(
        {
            "host": "SERVER_HOST",
            "user": "deploy-user",
            "nested": {
                "ssh_key_path": "/tmp/fake_id_rsa",
                "safe_count": 2,
            },
        }
    )

    assert sanitized["host"] == MASK
    assert sanitized["user"] == MASK
    assert sanitized["nested"]["ssh_key_path"] == MASK
    assert sanitized["nested"]["safe_count"] == 2


def test_invalid_input_errors_do_not_include_values() -> None:
    with pytest.raises(ServerInfoError) as exc_info:
        parse_server_info_text(
            "\n".join(
                [
                    "HOST=SERVER_HOST",
                    "PORT=not-a-port",
                    "USER=bad-user",
                    "SSH_KEY_PATH=/tmp/bad_id_rsa",
                    "DEPLOY_PATH=/tmp/bad-deploy",
                ]
            )
        )

    message = str(exc_info.value)
    assert "SERVER_HOST" not in message
    assert "bad-user" not in message
    assert "/tmp/bad_id_rsa" not in message


def test_missing_file_error_does_not_include_path(tmp_path) -> None:
    missing = tmp_path / "missing-server-info.txt"

    with pytest.raises(ServerInfoError) as exc_info:
        read_server_info(missing)

    assert str(missing) not in str(exc_info.value)


def test_cli_prints_only_redacted_json(tmp_path, capsys) -> None:
    path = tmp_path / "server-info.txt"
    path.write_text(_server_info_text(), encoding="utf-8")

    assert main([str(path)]) == 0
    captured = capsys.readouterr()

    assert "SERVER_HOST" not in captured.out
    assert "deploy-user" not in captured.out
    assert "/tmp/fake_id_rsa" not in captured.out
    assert MASK in captured.out
