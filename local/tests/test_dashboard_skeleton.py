from __future__ import annotations

from apps.dashboard import main


def test_dashboard_render_function_exists() -> None:
    assert callable(main.render)
    assert main.SYSTEM_STARTED_TEXT == "系统已启动"
