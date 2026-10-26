from __future__ import annotations

import inspect

from epidemic_core.p1_9 import dashboard_app, review_app


def test_review_console_whitelist_excludes_sensitive_fields() -> None:
    assert review_app.WHITELIST == (
        "dt_notific",
        "sg_uf_not",
        "id_municip",
        "nu_ano",
        "case_notification_count",
    )
    assert "age" not in review_app.WHITELIST
    assert "sex" not in review_app.WHITELIST
    assert "death_date" not in review_app.WHITELIST


def test_dashboard_does_not_offer_cross_source_sum() -> None:
    source = inspect.getsource(dashboard_app.dashboard if hasattr(dashboard_app, "dashboard") else dashboard_app)
    assert "不跨来源相加" in source
    assert "pub.id=:publication" in source
