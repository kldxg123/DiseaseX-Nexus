"""Parameter-tuning + controlled self-learning demo on top of the KG dual loop.

This package implements the closed loop

    data profile -> KG recommendation -> parameter contracts -> temporal split
    -> bounded tuning -> best-trial selection -> H1-H4 forecasts -> actual
    feedback -> performance ledger -> controlled retrain

entirely in Shadow mode: no promotion, no active-pointer change and no
trusted-table mutation. All tuning code is pure (stdlib only plus the
execution/dual-loop contracts); database access lives exclusively inside
``pipeline`` functions, so every module imports cleanly without a database.

Safety invariants enforced here:
  * tuning only ever sees the train+validation slice, never test rows;
  * missing observations stay None and are never zero-filled or imputed;
  * every parameter has an explicit provenance source; PAPER_EXTRACTED is
    never claimed because the KG holds no paper evidence for these parameters;
  * all randomness flows through a caller-provided ``random.Random`` instance;
  * re-running with the same seed and inputs reproduces byte-identical
    content hashes (wall-clock timings are the only non-hashed field).
"""

from __future__ import annotations

PT_IMPL_VERSION = "kg_pt.v1"
PT_SEED = 20260811

DEMO_KEY = "AUT:legionella:legionnaires_reported_cases:month"
DEMO_COUNTRY = "AUT"
DEMO_DISEASE = "legionella"
DEMO_INDICATOR = "legionnaires_reported_cases"
DEMO_FREQUENCY = "month"
# KG rank-1 recommendation for the demo series (verified read-only, SPEC section 0).
DEMO_RECOMMENDATION_ID = "c219de41-ea2e-58d9-86cd-339b03213401"
DEMO_ADAPTER_ID = "negbin_dglm.v1"

TRAIN_N = 168
VAL_N = 36
TEST_N = 36

# Rolling origins for tuning are relative to the validation-end index;
# test origins are relative to the series-end index.
TUNING_OFFSETS = [48, 44, 40, 36, 32, 28, 24, 20, 16, 12, 8, 4]
TEST_OFFSETS = [36, 32, 28, 24, 20, 16, 12, 8, 4]

MAX_TRIALS = 24
MAX_SECONDS = 600
MIN_VALID_FOLDS = 8
COVERAGE_FLOOR = 0.5
# Relative common-WAPE improvement over the best baseline required before the
# KG-recommended (tuned) model may become champion.
CHAMPION_MIN_IMPROVEMENT = 0.01
# Matured feedback rows required before a controlled retrain may trigger.
RETRAIN_MIN_MATURED = 12

HORIZONS = [1, 2, 3, 4]

MISSING_SEMANTICS = "MISSING_VALUE_KEPT_NULL_NEVER_IMPUTED"
NO_PAPER_EVIDENCE = "NO_PAPER_EVIDENCE_IN_KG"
REPLAY_KIND = "DEMO_HISTORICAL_REPLAY"
NO_REMAINING_HELDOUT = "NO_REMAINING_HELDOUT"
