import tempfile
from pathlib import Path

from src.step.experience_buffer import ExperienceBuffer


def test_experience_write_and_replay_balance():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "buffer.jsonl"
        buffer = ExperienceBuffer(path)
        for idx, label in enumerate(["上升", "下降", "稳定", "上升", "下降", "稳定"]):
            buffer.append({"experience_id": str(idx), "true_label": label, "outbreak": "o1"})
        replay = buffer.sample_replay(3)
        assert len(replay) == 3
        assert set(record["true_label"] for record in replay) <= {"上升", "下降", "稳定"}


def test_outbreak_partition():
    with tempfile.TemporaryDirectory() as td:
        buffer = ExperienceBuffer(Path(td) / "buffer.jsonl")
        buffer.append({"experience_id": "1", "true_label": "上升", "outbreak": "2014_west_africa"})
        assert "2014_west_africa" in buffer.partition("outbreak")


def test_query_helpers_filter_experiences():
    with tempfile.TemporaryDirectory() as td:
        buffer = ExperienceBuffer(Path(td) / "buffer.jsonl")
        buffer.append({"experience_id": "1", "true_label": "up", "country": "A", "horizon_days": 7, "error_type": "up_as_stable"})
        buffer.append({"experience_id": "2", "true_label": "down", "country": "B", "horizon_days": 14, "error_type": "correct"})
        assert [item["experience_id"] for item in buffer.query_by_horizon(7)] == ["1"]
        assert [item["experience_id"] for item in buffer.query_by_country("B")] == ["2"]
        assert [item["experience_id"] for item in buffer.query_by_error_type("up_as_stable")] == ["1"]
