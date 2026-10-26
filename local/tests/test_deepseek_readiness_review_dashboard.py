from __future__ import annotations


def test_dashboard_client_exposes_readiness_review_methods() -> None:
    from apps.dashboard.api_client import DashboardApiClient

    assert hasattr(DashboardApiClient, "deepseek_readiness_review_summary")
    assert hasattr(DashboardApiClient, "deepseek_readiness_review_table")
    assert hasattr(DashboardApiClient, "deepseek_readiness_manual_review_queue")
    assert hasattr(DashboardApiClient, "deepseek_readiness_review_export")


def test_dashboard_deepseek_page_contains_readiness_review_panel(monkeypatch) -> None:
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
            calls.append(("text_input", label))
            return value

        def selectbox(self, label, options, index=0):
            calls.append(("selectbox", label))
            return list(options)[index]

        def number_input(self, label, min_value=0, max_value=100, value=0):
            return value

        def columns(self, count):
            return [self for _ in range(count)]

        def expander(self, label, expanded=False):
            calls.append(("expander", label))
            return self

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def button(self, label):
            calls.append(("button", label))
            return False

        def caption(self, text):
            calls.append(("caption", text))

        def checkbox(self, label, value=False, **_kwargs):
            return value

        def divider(self):
            pass

        def error(self, text):
            calls.append(("error", text))

        def subheader(self, text):
            calls.append(("subheader", text))

        def dataframe(self, rows, use_container_width=True):
            calls.append(("dataframe", rows))

        def code(self, text, language=None):
            calls.append(("code", text))

    monkeypatch.setitem(__import__("sys").modules, "streamlit", FakeStreamlit())
    render_deepseek_web_search_page(object())
    rendered = " ".join(str(value) for _, value in calls)
    assert "Readiness Review" in rendered
    assert "抽取结果就绪度复核" in rendered

