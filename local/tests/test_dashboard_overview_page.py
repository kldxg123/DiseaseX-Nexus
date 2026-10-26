from __future__ import annotations

import json
import sys
from contextlib import contextmanager

from apps.dashboard.pages import render_overview


FORBIDDEN = (
    "raw_text",
    "<html>",
    "feature_json",
    "metadata_json",
    "lesson_json",
    "token",
    "secret",
    "password",
    "ssh_key_path",
    "服务器信息.txt",
)


def test_overview_page_uses_metrics_and_tables_without_raw_json() -> None:
    with fake_streamlit_module() as fake:
        render_overview(FakeOverviewClient())

    output_text = json.dumps(fake.outputs, ensure_ascii=False).lower()
    metric_labels = [item[1] for item in fake.outputs if item[0] == "metric"]
    table_titles = [item[1] for item in fake.outputs if item[0] == "subheader"]

    assert "系统总览" in str(fake.outputs)
    assert "仅用于研究和决策辅助" in str(fake.outputs)
    assert "风险等级" in metric_labels
    assert "预测目标日期" in metric_labels
    assert "预测生成日期" in metric_labels
    assert "模型版本" in metric_labels
    assert "已启用 active" in metric_labels
    assert "待审核 pending_review" in metric_labels
    assert "反馈总数" in metric_labels
    assert "近期疫情事件" in table_titles
    assert "近期预测记录" in table_titles
    assert fake.json_calls == 0
    assert "暂无" in output_text
    for marker in FORBIDDEN:
        assert marker.lower() not in output_text


class FakeOverviewClient:
    def health(self):
        return {"status": "ok", "service": "api", "metadata_json": {"token": "hidden"}}

    def get_source_health(self):
        return {
            "total": 6,
            "by_status": {"active": 2, "pending_review": 3, "degraded": 1, "disabled": 0},
            "metadata_json": {"secret": 'REDACTED'},
        }

    def get_events(self, **kwargs):
        return [
            {
                "country": "United States",
                "disease": "norovirus",
                "iso_week": "2025-W21",
                "cases": 12,
                "outbreaks_count": 2,
                "confidence_score": 0.9,
                "raw_text": "do-not-show",
                "metadata_json": {"token": "hidden"},
            }
        ]

    def get_predictions(self, **kwargs):
        return [
            {
                "target_date": "2025-05-26",
                "created_at": None,
                "predicted_value": 14.0,
                "risk_level": "critical",
                "model_version": "baseline:moving_average:h28:v1",
                "provenance_json": {"predicted_outbreak_probability": 0.8, "secret": 'REDACTED'},
            }
        ]

    def get_feedback_summary(self, **kwargs):
        return {
            "feedback_count": 5,
            "by_status": {"success": 1, "partial_success": 2, "failure": 1, "unknown": 1},
            "lesson_json": {"password": 'REDACTED'},
        }


class FakeStreamlit:
    def __init__(self):
        self.outputs: list[object] = []
        self.json_calls = 0

    def __getattr__(self, name):
        if name == "sidebar":
            return self
        raise AttributeError(name)

    def header(self, value):
        self.outputs.append(("header", value))

    def subheader(self, value):
        self.outputs.append(("subheader", value))

    def title(self, value):
        self.outputs.append(("title", value))

    def info(self, value):
        self.outputs.append(("info", value))

    def error(self, value):
        self.outputs.append(("error", value))

    def success(self, value):
        self.outputs.append(("success", value))

    def warning(self, value):
        self.outputs.append(("warning", value))

    def caption(self, value):
        self.outputs.append(("caption", value))

    def json(self, value):
        self.json_calls += 1
        self.outputs.append(("json", value))

    def code(self, value, language=None):
        self.outputs.append(("code", value, language))

    def dataframe(self, value, use_container_width=True):
        self.outputs.append(("dataframe", value, use_container_width))

    def metric(self, label, value):
        self.outputs.append(("metric", label, value))

    def columns(self, count):
        return [_FakeContext(self) for _ in range(count)]


class _FakeContext:
    def __init__(self, st):
        self.st = st

    def __enter__(self):
        return self.st

    def __exit__(self, exc_type, exc, tb):
        return False


@contextmanager
def fake_streamlit_module():
    fake = FakeStreamlit()
    previous = sys.modules.get("streamlit")
    sys.modules["streamlit"] = fake
    try:
        yield fake
    finally:
        if previous is None:
            sys.modules.pop("streamlit", None)
        else:
            sys.modules["streamlit"] = previous
