# Throwaway local smoke: run execute_pt_demo against a fake in-memory conn.
# Not a deliverable; exercises orchestration logic without a database.
import math
import sys
from datetime import date, datetime

sys.path.insert(0, "sandbox")

from epidemic_core.forecast_model_kg.dual_loop import period_add
from epidemic_core.kg_r3c.execution import TrainingRow
from epidemic_core.kg_pt import pipeline as pl

N = 240
ROWS = []
for i in range(N):
    p = period_add(date(2005, 1, 1), "month", i)
    v = float(int(round(40 + 0.08 * i + 12 * math.sin(2 * math.pi * (i % 12) / 12) + (i * 7) % 5)))
    ROWS.append(TrainingRow(period_start=p, period_end=p, value=v, observation_id=f"syn-{i}", revision=1))


class FakeResult:
    def __init__(self, one=None, all_=None):
        self._one = one
        self._all = all_ if all_ is not None else ([] if one is None else [one])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.writes = []  # (sql, params)
        self.tuning_runs = {}  # idempotency_key -> dict
        self.awaiting = []  # dicts fed to _mature_test_feedback select

    def execute(self, sql, params=()):
        s = " ".join(sql.split()).lower()
        if s.startswith("select tuning_run_id") and "kg_pt_tuning_runs" in s:
            if "where idempotency_key" in s:
                row = self.tuning_runs.get(params[0])
            else:  # content lookup: params = (snapshot_sha, split_sha, adapter, cycle)
                row = next(
                    (
                        v
                        for v in self.tuning_runs.values()
                        if v["snapshot_sha256"] == params[0]
                        and v["split_sha256"] == params[1]
                        and (not v.get("adapter_id") or v.get("adapter_id") == params[2])
                        and v.get("cycle_index") == params[3]
                        and ("and status = 'completed'" not in s or v["status"] == "COMPLETED")
                    ),
                    None,
                )
            return FakeResult(one=row)
        if "from epidemic.dashboard_history_series_v2" in s:
            all_ = [
                {
                    "observation_id": r.observation_id,
                    "period_start": r.period_start,
                    "period_end": r.period_end,
                    "value_numeric": r.value,
                    "revision": r.revision,
                }
                for r in self.rows
            ]
            return FakeResult(all_=all_)
        if "select snapshot_id from" in s and "kg_pt_demo_snapshots" in s:
            return FakeResult(one=None)
        if "from epidemic_kg.kg_pt_demo_snapshot_rows" in s:
            all_ = [
                {
                    "observation_id": r.observation_id,
                    "period_start": r.period_start,
                    "period_end": r.period_end,
                    "value_numeric": r.value,
                    "revision": r.revision,
                }
                for r in self.rows
            ]
            return FakeResult(all_=all_)
        if s.startswith("select feedback_id") and "kg_forecast_objective_feedback" in s:
            return FakeResult(all_=list(self.awaiting))
        if s.startswith("insert into epidemic_kg.kg_pt_tuning_runs"):
            key = params[12]
            self.tuning_runs.setdefault(
                key,
                {
                    "tuning_run_id": params[0],
                    "status": "RUNNING",
                    "shadow_run_id": params[1],
                    "snapshot_sha256": params[3],
                    "split_sha256": params[4],
                    "adapter_id": params[5],
                    "cycle_index": params[6],
                    "best_trial_id": None,
                },
            )
        elif s.startswith("update epidemic_kg.kg_pt_tuning_runs"):
            for k, v in self.tuning_runs.items():
                if v["tuning_run_id"] == params[2]:
                    v["status"] = params[0]
                    v["best_trial_id"] = params[1]
        elif s.startswith("insert into epidemic_kg.kg_forecast_objective_feedback") and params[5] == "AWAITING_ACTUAL":
            tp = params[4]
            tp = date.fromisoformat(tp) if isinstance(tp, str) else tp
            self.awaiting.append({"feedback_id": params[0], "forecast_value_id": params[2], "target_period": tp})
        elif s.startswith("update epidemic_kg.kg_forecast_objective_feedback"):
            self.awaiting = [a for a in self.awaiting if a["feedback_id"] != params[1]]
        self.writes.append((s, params))
        return FakeResult()


conn = FakeConn(ROWS)
r1 = pl.execute_pt_demo(conn, idempotency_key="smoke:v1")
print("status:", r1["status"])
print("keys:", sorted(r1.keys()))
print("trials:", r1["n_trials"], r1["n_trials_cycle2"], "best:", r1["best_trial_index"])
print("champion:", r1["champion"]["champion_adapter_id"], r1["champion"]["champion_source"], r1["champion"]["champion_reason"])
print("champion2:", r1["champion_cycle2"]["champion_adapter_id"], r1["champion_cycle2"]["champion_reason"])
print("feedback:", r1["feedback"])
print("retrain:", r1["retrain"])
print("evals:", sorted(r1["test_evaluations"]))
print("cycles:", r1["learning_cycle_ids"])
print("future:", r1["future_forecast_count"])

# second run -> IDEMPOTENT_REPLAY with same ids
r2 = pl.execute_pt_demo(conn, idempotency_key="smoke:v1")
print("replay:", r2["status"], r2["tuning_run_id"] == r1["tuning_run_id"], r2["tuning_run_id_cycle2"] == r1["tuning_run_id_cycle2"])
assert r2["status"] == "IDEMPOTENT_REPLAY"
assert r2["best_trial_id"] == r1["best_trial_id"]
# DIFFERENT key, identical content -> converge on existing ids, no new tuning runs
n_runs_before = len(conn.tuning_runs)
r3 = pl.execute_pt_demo(conn, idempotency_key="smoke:v2-different-key")
print("content-converge:", r3["status"], r3["tuning_run_id"] == r1["tuning_run_id"], r3["shadow_run_id"] == r1["shadow_run_id"])
assert r3["status"] == "IDEMPOTENT_REPLAY"
assert r3["tuning_run_id"] == r1["tuning_run_id"]
assert r3["tuning_run_id_cycle2"] == r1["tuning_run_id_cycle2"]
assert r3["shadow_run_id"] == r1["shadow_run_id"]
assert len(conn.tuning_runs) == n_runs_before  # nothing new written
print("OK")
print("timestamp", datetime.utcnow().isoformat())
