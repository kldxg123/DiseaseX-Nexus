from __future__ import annotations


def test_dashboard_client_exposes_annotation_workspace_methods() -> None:
    from apps.dashboard.api_client import DashboardApiClient

    assert hasattr(DashboardApiClient, "deepseek_annotation_workspace_rows")
    assert hasattr(DashboardApiClient, "deepseek_annotation_workspace_export_template")
    assert hasattr(DashboardApiClient, "deepseek_annotation_workspace_validate")
    assert hasattr(DashboardApiClient, "deepseek_annotation_workspace_preview_promotion")
    assert hasattr(DashboardApiClient, "deepseek_annotation_workspace_export_bundle")


def test_dashboard_page_contains_annotation_workspace_controls(monkeypatch) -> None:
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
            calls.append(("checkbox", label))
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
    assert "Annotation Workspace" in rendered
    assert "Preview promotion decisions" in rendered

