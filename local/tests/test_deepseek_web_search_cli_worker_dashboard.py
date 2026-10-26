from __future__ import annotations

import json

from common.db import get_session, init_db
from deepseek_web_search_agent.cli import main
from worker_agent import TaskRunner


def _database_url(tmp_path) -> str:
    return f"sqlite:///{(tmp_path / 'deepseek-entrypoints.db').as_posix()}"


def test_deepseek_cli_plan_and_mock_run(tmp_path, capsys) -> None:
    plan_code = main(
        [
            "plan",
            "--country",
            "United States",
            "--disease",
            "influenza",
            "--max-search-queries",
            "2",
            "--output-dir",
            str(tmp_path / "plan"),
        ]
    )
    run_code = main(
        [
            "run-mock",
            "--country",
            "United States",
            "--disease",
            "influenza",
            "--max-search-queries",
            "1",
            "--max-results-per-query",
            "2",
            "--max-pages-to-read",
            "0",
            "--max-total-http-requests",
            "2",
            "--output-dir",
            str(tmp_path / "run"),
            "--database-url",
            _database_url(tmp_path),
        ]
    )

    assert plan_code == 0
    assert run_code == 0
    rendered = capsys.readouterr().out.lower()
    assert "api_key" not in rendered
    assert "password" not in rendered
    assert "<html" not in rendered


def test_deepseek_worker_tasks_are_mock_safe(tmp_path) -> None:
    database_url = _database_url(tmp_path)
    init_db(database_url).dispose()
    with get_session(database_url) as session:
        runner = TaskRunner(session)
        plan = runner.run_task("deepseek_web_search_plan", country="United States", disease="influenza")
        run = runner.run_task(
            "deepseek_web_search_run",
            country="United States",
            disease="influenza",
            search_backend="mock",
            max_search_queries=1,
            max_search_results_per_query=2,
            max_pages_to_read=0,
            max_total_http_requests=2,
            output_dir=str(tmp_path / "worker"),
        )
        visual = runner.run_task("deepseek_web_search_3d_refresh", country="United States", disease="influenza")

    assert plan["status"] == "success"
    assert run["status"] == "success"
    assert visual["status"] == "success"
    assert run["job"]["summary"]["search_backend"] == "mock"
    rendered = json.dumps(run, ensure_ascii=False).lower()
    assert "api_key" not in rendered
    assert "password" not in rendered


def test_deepseek_dashboard_page_import_and_mock_render(monkeypatch) -> None:
    from apps.dashboard.pages import render_deepseek_web_search_page

    calls: list[tuple[str, object]] = []

    class FakeStreamlit:
        session_state: dict = {}

        def header(self, text):
            calls.append(("header", text))

        def info(self, text):
            calls.append(("info", text))

        def warning(self, text):
            calls.append(("warning", text))

        def text_input(self, label, value="", key=None):
            return value

        def selectbox(self, label, options, index=0):
            return list(options)[index]

        def number_input(self, label, min_value=0, max_value=100, value=0):
            return value

        def columns(self, count):
            return [self for _ in range(count)]

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def button(self, label):
            return False

        def expander(self, label, expanded=False):
            return self

        def caption(self, text):
            calls.append(("caption", text))

        def checkbox(self, label, value=False, **_kwargs):
            return value

        def divider(self):
            calls.append(("divider", None))

        def error(self, text):
            calls.append(("error", text))

        def subheader(self, text):
            calls.append(("subheader", text))

        def dataframe(self, rows, use_container_width=True):
            calls.append(("dataframe", rows))

        def code(self, text, language=None):
            calls.append(("code", text))

    class FakeClient:
        pass

    monkeypatch.setitem(__import__("sys").modules, "streamlit", FakeStreamlit())
    render_deepseek_web_search_page(FakeClient())
    assert any("DeepSeek" in str(value) for _, value in calls)
