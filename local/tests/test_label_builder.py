from src.eval.metrics import LABEL_DOWN, LABEL_STABLE, LABEL_UP
from src.processors.label_builder import PREDICTION_TIME_CONVENTION, build_labels, label_growth


def test_label_growth_classes():
    assert label_growth(0.21) == LABEL_UP
    assert label_growth(-0.21) == LABEL_DOWN
    assert label_growth(0.0) == LABEL_STABLE


def test_cumulative_cases_do_not_define_downward_label():
    panel = []
    cum = 0
    for idx in range(40):
        new = 10 if idx < 20 else 1
        cum += new
        panel.append({"date": f"2014-01-{idx+1:02d}" if idx < 31 else f"2014-02-{idx-30:02d}", "country": "A", "adm1": "X", "new_cases": new, "cum_cases": cum})
    samples = build_labels(panel, horizon_days=7, past_window_days=14)
    assert samples
    assert any(sample["label"] == LABEL_DOWN for sample in samples)
    assert all(sample["growth"] <= 100 for sample in samples)


def test_future_window_starts_after_prediction_date():
    panel = []
    for idx in range(10):
        panel.append({"date": f"2014-01-{idx+1:02d}", "country": "A", "adm1": "X", "new_cases": 1 if idx < 6 else 10})
    samples = build_labels(panel, horizon_days=2, past_window_days=2)
    sample = next(item for item in samples if item["date"] == "2014-01-06")
    assert sample["future_window_sum"] == 20
    assert sample["past_window_sum"] == 2
    assert sample["prediction_time_convention"] == PREDICTION_TIME_CONVENTION
