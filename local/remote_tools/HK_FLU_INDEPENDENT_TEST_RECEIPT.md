# Test receipt

## Passed

- Targeted unit suite: `tests/unit/test_forecast_model_kg.py` — 7 passed.
- Ruff on touched source and test modules — passed.
- strict Mypy on touched source modules — passed after adding explicit `cast` for JSON payloads.
- The report builder recomputed 1,104 paired H0-H1 rows and 4,968 paired H0-H8 rows directly from the saved row-level prediction CSV.
- Primary H0-H1 absolute WAPE gap is 0.002094, below the declared 0.01 tolerance.
- The H0-H8 failure remains present in the artifact and has a unit-test assertion preventing accidental promotion to parity.

## Environment-limited broader check

The full `tests/unit` collection was attempted but stopped before test execution because the current vm201 project runtime lacks the pre-existing `httpx` dependency used by unrelated retrieval/normalization tests. Seven modules failed collection for the same missing dependency. This is an environment issue outside the touched benchmark path; it was not reported as a pass and no dependency was installed or upgraded in this stage.

## Non-mutation

- Database writes: 0
- Forecast runs created: 0
- Promotion changes: 0
- Active Pointer changes: 0
- Original active worktree dirty paths were not overwritten.
