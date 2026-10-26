# ruff: noqa: E501
"""Tests for the held-out governance repair (0043): global evaluation identity,
time chain, fresh certification gates, paired same-sample comparison and the
non-vacuity fixes of the test-label / qwen-context sentinels.

All tests are pure (no database): registry persistence is exercised through
SQL-string-free helpers and monkeypatched cell builders.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from epidemic_core.forecast_model_kg.dual_loop import period_add
from epidemic_core.kg_pt.split import make_split
from epidemic_core.kg_qwen import pipeline as kq_pipeline
from epidemic_core.kg_qwen import sentinels
from epidemic_core.kg_r3c.execution import TrainingRow

TRACK = {
    "key": "DEU:legionella:legionnaires_reported_cases:month",
    "frequency": "month",
    "train_n": 168,
    "val_n": 36,
    "test_n": 0,
    "hidden_n": 36,
    "tuning_n": 204,
    "eval_identity": "FRESH_HELDOUT_LOCKED",
    "eval_flags": ["FRESH_HELDOUT_LOCKED"],
}
WINDOW = {
    "identity": "FRESH_HELDOUT_LOCKED",
    "flags": ["FRESH_HELDOUT_LOCKED"],
    "start_period": "2022-01-01",
    "end_period": "2024-12-01",
    "n_periods": 36,
    "frequency": "month",
}


# ------------------------------------------------------- global identity
def test_global_identity_payload_excludes_run_id_and_is_stable() -> None:
    p1 = kq_pipeline.heldout_identity_payload(TRACK, "sha-snap", WINDOW, "sha-split")
    p2 = kq_pipeline.heldout_identity_payload(TRACK, "sha-snap", WINDOW, "sha-split")
    assert p1 == p2
    assert "run_id" not in str(p1)
    assert kq_pipeline.heldout_registry_id(p1) == kq_pipeline.heldout_registry_id(p2)


def test_global_identity_changes_with_any_component() -> None:
    base = kq_pipeline.heldout_identity_payload(TRACK, "sha-snap", WINDOW, "sha-split")
    base_id = kq_pipeline.heldout_registry_id(base)
    for mutate in (
        lambda p: p.update(track_key="XXX"),
        lambda p: p.update(snapshot_sha256="other"),
        lambda p: p.update(identity="REUSED_HISTORICAL_BENCHMARK"),
        lambda p: p.update(split_version="other-split"),
        lambda p: p["eval_window"].update(start_period="2022-02-01"),
    ):
        payload = kq_pipeline.heldout_identity_payload(TRACK, "sha-snap", WINDOW, "sha-split")
        mutate(payload)
        assert kq_pipeline.heldout_registry_id(payload) != base_id


def test_global_identity_window_key_order_irrelevant() -> None:
    shuffled = dict(reversed(list(WINDOW.items())))
    p1 = kq_pipeline.heldout_identity_payload(TRACK, "sha-snap", WINDOW, "sha-split")
    p2 = kq_pipeline.heldout_identity_payload(TRACK, "sha-snap", shuffled, "sha-split")
    assert kq_pipeline.heldout_registry_id(p1) == kq_pipeline.heldout_registry_id(p2)


# ------------------------------------------------------- time chain
def _stamps() -> dict[str, datetime]:
    t0 = datetime(2026, 8, 12, 8, 0, 0, tzinfo=timezone.utc)
    return {
        "registered_at": t0,
        "locked_at": t0 + timedelta(seconds=1),
        "first_qwen_started_at": t0 + timedelta(minutes=1),
        "tuning_finished_at": t0 + timedelta(minutes=40),
        "consumed_at": t0 + timedelta(minutes=41),
    }


def test_time_chain_ok() -> None:
    result = kq_pipeline.validate_time_chain(**_stamps())
    assert result["ok"] is True
    assert result["violations"] == []


def test_time_chain_missing_timestamp_blocks_certification() -> None:
    stamps = _stamps()
    stamps["first_qwen_started_at"] = None  # type: ignore[assignment]
    result = kq_pipeline.validate_time_chain(**stamps)
    assert result["ok"] is False
    assert "MISSING_TIMESTAMP:first_qwen_started_at" in result["violations"]


@pytest.mark.parametrize(
    "broken",
    [
        {"locked_at": datetime(2026, 8, 12, 9, 0, 0, tzinfo=timezone.utc)},  # locked AFTER qwen start
        {"tuning_finished_at": datetime(2026, 8, 12, 7, 0, 0, tzinfo=timezone.utc)},  # tuning ended before qwen
        {"consumed_at": datetime(2026, 8, 12, 8, 30, 0, tzinfo=timezone.utc)},  # consumed mid-tuning
    ],
)
def test_time_chain_order_violations(broken: dict[str, datetime]) -> None:
    stamps = _stamps()
    stamps.update(broken)
    result = kq_pipeline.validate_time_chain(**stamps)
    assert result["ok"] is False
    assert any(v.startswith("ORDER:") for v in result["violations"])


def test_time_chain_replay_does_not_require_consumption() -> None:
    stamps = _stamps()
    stamps["consumed_at"] = None  # type: ignore[assignment]
    assert kq_pipeline.validate_time_chain(**stamps, require_consumed=False)["ok"] is True
    assert kq_pipeline.validate_time_chain(**stamps)["ok"] is False


# ------------------------------------------------------- fresh certification
def _cert_metrics(**over: float) -> dict[str, float]:
    m = {"wape": 0.25, "agreement_pct": 75.0, "coverage": 0.9}
    m.update(over)
    return m


def _paired(improvement: float | None, match: bool = True) -> dict[str, object]:
    return {
        "relative_improvement_vs_best_baseline": improvement,
        "paired_sample_match": match,
        "paired_gate_failures": [] if match else ["PAIRED_SAMPLE_MISMATCH:x"],
    }


def test_certification_passes_when_all_gates_hold() -> None:
    cert = kq_pipeline._fresh_certification(_cert_metrics(), _paired(0.05), {"ok": True, "violations": []})
    assert cert["certified"] is True
    assert cert["verdict"] == "FRESH_HELDOUT_CERTIFIED"


@pytest.mark.parametrize(
    "metrics,paired,chain,failed",
    [
        (_cert_metrics(wape=0.31), _paired(0.05), {"ok": True}, ["wape_max_0.30"]),
        (_cert_metrics(agreement_pct=69.9), _paired(0.05), {"ok": True}, ["agreement_min_70"]),
        (_cert_metrics(coverage=0.8), _paired(0.05), {"ok": True}, ["coverage_min_0.85"]),
        (_cert_metrics(), _paired(0.005), {"ok": True}, ["paired_baseline_improvement_min_0.01"]),
        (_cert_metrics(), _paired(None), {"ok": True}, ["paired_baseline_improvement_min_0.01"]),
        (_cert_metrics(), _paired(0.05, match=False), {"ok": True}, ["paired_sample_match"]),
        (_cert_metrics(), _paired(0.05), {"ok": False}, ["time_chain_ok"]),
    ],
)
def test_certification_gate_failures_are_enumerated(metrics, paired, chain, failed) -> None:  # type: ignore[no-untyped-def]
    cert = kq_pipeline._fresh_certification(metrics, paired, chain)
    assert cert["certified"] is False
    assert cert["verdict"] == "FRESH_HELDOUT_GATES_FAILED"
    for gate in failed:
        assert gate in cert["failed_gates"]


# ------------------------------------------------------- paired comparison
def _cell(origin: int, horizon: int, yhat: float | None, actual: float | None) -> dict[str, object]:
    return {
        "origin_index": origin,
        "horizon": horizon,
        "target_period": f"2024-{horizon:02d}-01",
        "yhat": yhat,
        "actual_value": actual,
        "lower_bound": None if yhat is None else yhat * 0.5,
        "upper_bound": None if yhat is None else yhat * 1.5,
        "mase_denominator": 10.0,
    }


def _synthetic_cells(drop_last: bool = False, null_last_yhat: bool = False) -> list[dict[str, object]]:
    cells = [_cell(o, h, 10.0 + o + h, 12.0 + o) for o in range(3) for h in (1, 2)]
    if drop_last:
        cells = cells[:-1]
    if null_last_yhat:
        cells[-1]["yhat"] = None
    return cells


def test_paired_comparison_same_sample_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_cells(rows, adapter_id, params, origin_indices, frequency, seed, with_labels):  # type: ignore[no-untyped-def]
        calls.append(adapter_id)
        return _synthetic_cells()

    monkeypatch.setattr(kq_pipeline, "_champion_cells_payload", fake_cells)
    champion_cells = _synthetic_cells()
    result = kq_pipeline._paired_baseline_comparison(
        [], TRACK["key"], "champion.v1", {}, [1, 2, 3], "month", 1, champion_cells
    )
    assert result["paired_sample_match"] is True
    assert result["paired_gate_failures"] == []
    assert result["paired_n"] == 6
    assert set(result["baselines"]) == {"last_value", "rolling_mean", "drift", "seasonal_naive"}
    assert result["best_baseline_id"] is not None
    assert result["relative_improvement_vs_best_baseline"] is not None
    assert result["paired_csv_sha256"]
    assert "champion,champion.v1" in result["paired_csv"]
    # champion + 4 baselines evaluated
    assert calls == ["last_value", "rolling_mean", "drift", "seasonal_naive"]


def test_paired_comparison_sample_mismatch_fails_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_cells(rows, adapter_id, params, origin_indices, frequency, seed, with_labels):  # type: ignore[no-untyped-def]
        # one baseline produces a null prediction on the last cell (the old 48-vs-47 bug)
        return _synthetic_cells(null_last_yhat=(adapter_id == "drift"))

    monkeypatch.setattr(kq_pipeline, "_champion_cells_payload", fake_cells)
    result = kq_pipeline._paired_baseline_comparison(
        [], TRACK["key"], "champion.v1", {}, [1, 2, 3], "month", 1, _synthetic_cells()
    )
    assert result["paired_sample_match"] is False
    assert any("PAIRED_SAMPLE_MISMATCH:drift" in f for f in result["paired_gate_failures"])
    assert result["baselines"]["drift"]["certifiable"] is False
    # matched baselines still produce a certifiable improvement number
    assert result["best_baseline_id"] != "drift"


# ------------------------------------------------------- sentinel non-vacuity
def _rows(n: int, hidden_from: int | None = None) -> list[TrainingRow]:
    start = date(2005, 1, 1)
    out = []
    for i in range(n):
        value = float(10 + (i % 7))
        if hidden_from is not None and i >= hidden_from:
            value = 500.0 + i
        out.append(TrainingRow(period_start=period_add(start, "month", i), period_end=None, value=value))
    return out


def test_test_label_sentinel_zero_perturbation_is_not_applicable() -> None:
    rows = _rows(204)  # split covers ALL rows -> empty hidden region
    split = make_split([r.period_start for r in rows], 168, 36, 0)
    result = sentinels.test_label_sentinel(rows=rows, split=split, frequency="month")
    assert result.passed is False
    assert result.status == "NOT_APPLICABLE"
    assert "0" in result.detail


def test_test_label_sentinel_real_perturbation_passes() -> None:
    rows = _rows(240, hidden_from=204)
    split = make_split([r.period_start for r in rows[:204]], 168, 36, 0)
    result = sentinels.test_label_sentinel(rows=rows, split=split, frequency="month")
    assert result.passed is True
    assert result.status == "PASS"
    assert "36 hidden labels" in result.detail


def test_qwen_context_sentinel_canary_when_no_unique_hidden_values() -> None:
    # hidden values are the SAME small ints as train+val -> 0 natural
    # hidden-only values; the canary check must still verify the context.
    rows = _rows(240)  # no shifted hidden segment: hidden values overlap tv
    split = make_split([r.period_start for r in rows[:204]], 168, 36, 0)
    result = sentinels.qwen_context_sentinel(
        track_key=TRACK["key"], rows=rows, split=split, frequency="month", snapshot_sha256="sha"
    )
    assert result.status == "PASS"
    assert "canary" in result.detail


def test_qwen_context_sentinel_no_hidden_at_all_is_not_applicable() -> None:
    rows = _rows(204)
    split = make_split([r.period_start for r in rows], 168, 36, 0)
    result = sentinels.qwen_context_sentinel(
        track_key=TRACK["key"], rows=rows, split=split, frequency="month", snapshot_sha256="sha"
    )
    assert result.passed is False
    assert result.status == "NOT_APPLICABLE"


def test_sentinel_result_status_defaults() -> None:
    assert sentinels.SentinelResult("x", True, "d", None).status == "PASS"
    assert sentinels.SentinelResult("x", False, "d", None).status == "FAIL"
    assert sentinels.SentinelResult("x", False, "d", None, status="NOT_APPLICABLE").status == "NOT_APPLICABLE"


# ------------------------------------------------------- track C definition
def test_track_c_geometry() -> None:
    track = kq_pipeline.TRACKS[kq_pipeline.TRACK_C_KEY]
    assert track["eval_identity"] == "FRESH_HELDOUT_LOCKED"
    assert track["train_n"] + track["val_n"] == track["tuning_n"]
    assert track["hidden_n"] == 52
    assert track["tuning_n"] + track["hidden_n"] == track["total_n"]
    assert track["source_override"]["table"] == "epidemic.normalized_observations"
