"""Qwen-assisted autotuning + controlled self-learning loop (Shadow only).

This package implements the closed loop

    frozen snapshot -> dataset profile -> Qwen tuning plan (validated,
    sandboxed) -> bounded contract search -> validation gates -> one-shot
    hidden-eval -> Shadow forecast publication -> matured feedback ledger

on top of the kg_pt execution discipline: every module is pure stdlib and
imports cleanly without a database; DB access lives exclusively inside
``pipeline`` functions.

Safety invariants enforced here:
  * the hidden evaluation window is locked before any Qwen call or tuning
    and is evaluated exactly once (Track B = FRESH_HELDOUT_LOCKED);
  * tuning only ever sees the tuning window, never hidden-eval labels;
  * missing observations stay None and are never zero-filled or imputed;
  * every parameter has an explicit provenance source; PAPER_EXTRACTED is
    never claimed because the KG holds no paper evidence for these parameters;
  * all randomness flows through a caller-provided ``random.Random`` instance;
  * re-running with the same seed and inputs reproduces byte-identical
    content hashes (wall-clock timings are the only non-hashed field).
"""

from __future__ import annotations

KQ_IMPL_VERSION = "kg_qwen.v1"
KQ_SEED = 20260811

TARGET_WAPE = 0.30
TARGET_AGREEMENT_PCT = 70.0
COVERAGE_MIN = 0.85
BASELINE_IMPROVEMENT_MIN = 0.01

MAX_ROUNDS = 3
MAX_TRIALS_PER_ROUND = 50
MAX_TOTAL_MINUTES = 45
TRIAL_TIMEOUT_SECONDS = 120
MIN_VALID_FOLDS = 8
HORIZONS = [1, 2, 3, 4]

TRACK_A_KEY = "AUT:legionella:legionnaires_reported_cases:month"
TRACK_B_KEY = "FIN:hantavirus:puumala_virus_reports:week"
TRACK_C_KEY = "CHL:influenza:influenza_positive_detections:week"
TRACK_B_TUNING_WEEKS = 393
TRACK_B_HIDDEN_WEEKS = 52

QWEN_MODEL_ID = "Qwen/Qwen3-4B-Instruct-2507"
QWEN_MODEL_DIR = "USER_HOME/models/Qwen3-4B-Instruct-2507"
QWEN_WORKER_PYTHON = "USER_HOME/Ebola/venv/bin/python"
QWEN_RUNTIME_LABEL = "transformers-5.8.1+torch-2.12.0-cpu"

REPLAY_KIND = "DEMO_HISTORICAL_REPLAY"
