from __future__ import annotations

import json

from scripts.check_compose import check_compose, main


def test_check_compose_validates_required_services_and_ports() -> None:
    result = check_compose()

    assert result["status"] == "ok"
    assert all(result["services"].values())
    assert result["ports"]["api"]["8000"] is True
    assert result["ports"]["dashboard"]["8501"] is True
    assert result["ports"]["mlflow"]["5000"] is True
    assert result["ports"]["minio"]["9000"] is True
    assert result["ports"]["minio"]["9001"] is True
    assert result["ports_checked"] is True
    assert result["commands"]["api"] is True
    assert result["commands"]["dashboard"] is True
    assert result["commands"]["worker"] is True
    assert result["forbidden_mounts_found"] == []
    assert result["warnings"] == []
    assert result["docker_started"] is False


def test_check_compose_cli_outputs_safe_json(capsys) -> None:
    result = main([])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["status"] == "ok"
    assert "secret" not in captured.out.lower()
    assert "token" not in captured.out.lower()
    assert "password" not in captured.out.lower()
    assert "private key" not in captured.out.lower()


def test_check_compose_detects_forbidden_mount_without_docker(tmp_path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  api:
    image: local
    ports:
      - "8000:8000"
    volumes:
      - .env:/app/.env
""",
        encoding="utf-8",
    )

    result = check_compose(compose)

    assert result["status"] == "failed"
    assert result["forbidden_mounts_found"] == [".env mount"]


def test_check_compose_detects_sensitive_file_mount_without_docker(tmp_path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  api:
    image: local
    command: uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
    volumes:
      - ./local.pem:/app/local.pem
""",
        encoding="utf-8",
    )

    result = check_compose(compose)

    assert result["status"] == "failed"
    assert "key-file mount" in result["forbidden_mounts_found"]


def test_check_compose_rejects_unsafe_worker_command_without_docker(tmp_path) -> None:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        """
services:
  postgres:
    image: local
  redis:
    image: local
  minio:
    image: local
    ports:
      - "9000:9000"
      - "9001:9001"
  api:
    image: local
    command: uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
    ports:
      - "8000:8000"
  worker:
    image: local
    command: ssh example
  dashboard:
    image: local
    command: streamlit run apps/dashboard/streamlit_app.py --server.port 8501
    ports:
      - "8501:8501"
  mlflow:
    image: local
    ports:
      - "5000:5000"
""",
        encoding="utf-8",
    )

    result = check_compose(compose)

    assert result["status"] == "failed"
    assert result["commands"]["worker"] is False
