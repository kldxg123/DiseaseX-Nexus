from __future__ import annotations

import inspect

from apps.dashboard import pages


def test_dashboard_goal_mode_controls_are_visible_in_demo_page() -> None:
    source = inspect.getsource(pages.render_deepseek_demo_pipeline_page)

    assert "Goal Mode：自动切换病种直到生成可审批行" in source
    assert "运行 Goal Mode 自动尝试" in source
    assert "允许受控文档预览" in source
    assert "找到行后停止" in source


def test_goal_mode_attempt_rows_are_human_readable() -> None:
    rows = pages._goal_mode_attempt_rows(
        [
            {
                "target": "dengue:Global",
                "status": "mock_success",
                "review_rows_from_live_material": 0,
                "date_anchor_count": 2,
                "metric_anchor_count": 3,
                "context_linked_candidate_count": 1,
                "blockers": ["needs_structured_document_or_dataset"],
                "review_table": "review.json",
            }
        ]
    )

    assert rows[0]["目标"] == "dengue:Global"
    assert rows[0]["上下文关联候选"] == 1
    assert "needs_structured_document_or_dataset" in rows[0]["阻断原因"]
