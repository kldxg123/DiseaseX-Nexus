"""Add KG held-out governance registry and reclassify multiply-opened window.

Revision ID: 0043_kg_heldout_governance
Revises: 0042_kg_qwen_autotuning

Problem being repaired (audit finding):

* the FIN/hantavirus 52-week window (snapshot a3add227...) was evaluated by
  THREE different agent runs because exactly-once consumption was only
  enforced per (run_id, identity) and eval_id was content-addressed WITH the
  run_id inside — nothing stopped a new run from re-opening the same window;
* those rows can therefore never again be claimed "fresh held-out".

This migration:

1. adds ``epidemic_kg.kg_qwen_heldout_registry`` — ONE row per global
   evaluation identity (track_key + snapshot_sha256 + canonical eval window
   JSON + evaluation identity + split_version), deliberately WITHOUT run_id,
   with an atomic status machine
   (REGISTERED -> LOCKED_UNOPENED -> CONSUMED | INVALIDATED | REPLAY_ONLY)
   enforced at the privilege level of a guarded UPDATE;
2. links kg_qwen_evaluation_sets to the registry (registry_id, nullable FK)
   and adds the time-chain columns first_qwen_started_at /
   tuning_finished_at to kg_qwen_agent_runs;
3. reclassifies every historical FRESH_HELDOUT_LOCKED evaluation row as
   RETROSPECTIVE_REPLAY_NOT_FRESH — original WAPE/agreement/coverage/MAE/
   RMSE/sMAPE/MASE/run_id are preserved verbatim, only the identity label,
   window flags and an audit note inside metrics change;
4. seeds registry rows for the two windows that already exist (AUT replay
   benchmark, FIN multiply-opened window) with status REPLAY_ONLY: they may
   still be used for retrospective replay demos but can never be consumed as
   fresh held-out again.

Downgrade drops the new table/columns only; the reclassification is an
audit-preserving correction and is intentionally NOT reversed.
"""

import hashlib
import json
import uuid

from alembic import op

revision = "0043_kg_heldout_governance"
down_revision = "0042_kg_qwen_autotuning"
branch_labels = None
depends_on = None

SCHEMA = "epidemic_kg"

# Same namespace / canonicalization as epidemic_core.kg_r3c.execution
# (stable_uuid + sha256_json) — duplicated here so the migration stays
# self-contained and produces identical registry ids to the runtime code.
_NS = uuid.UUID("c87f8a95-7d2c-4f3e-9b1a-2e5d6a8b0c1f")
_WINDOW_KEYS = ("identity", "flags", "start_period", "end_period", "n_periods", "frequency")


def _sha256_json(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _registry_id(payload: dict) -> str:
    return str(uuid.uuid5(_NS, "kq-heldout-registry:" + _sha256_json(payload)))


def upgrade() -> None:
    # --- 1. global held-out registry (one row per data+window across ALL runs) ---
    op.execute(
        f"""
        create table if not exists {SCHEMA}.kg_qwen_heldout_registry (
          registry_id uuid primary key,
          global_sha256 text not null unique,
          global_identity jsonb not null,
          track_key text not null,
          identity text not null,
          snapshot_sha256 text not null,
          eval_window jsonb not null,
          split_version text not null,
          evaluator_version text not null,
          status text not null
            check (status in ('REGISTERED','LOCKED_UNOPENED','CONSUMED','INVALIDATED','REPLAY_ONLY')),
          registered_at timestamptz not null default now(),
          locked_at timestamptz,
          first_qwen_started_at timestamptz,
          tuning_finished_at timestamptz,
          consumed_at timestamptz,
          consumer_run_id uuid,
          created_at timestamptz not null default now()
        )
        """
    )
    op.execute(
        f"create index if not exists kg_qwen_heldout_registry_track_idx "
        f"on {SCHEMA}.kg_qwen_heldout_registry(track_key, snapshot_sha256)"
    )
    # --- 2. links + time-chain columns (additive) ---
    op.execute(
        f"alter table {SCHEMA}.kg_qwen_evaluation_sets "
        f"add column if not exists registry_id uuid references {SCHEMA}.kg_qwen_heldout_registry(registry_id)"
    )
    op.execute(f"alter table {SCHEMA}.kg_qwen_agent_runs add column if not exists first_qwen_started_at timestamptz")
    op.execute(f"alter table {SCHEMA}.kg_qwen_agent_runs add column if not exists tuning_finished_at timestamptz")

    # --- 3. seed registry rows for windows that already exist (REPLAY_ONLY),
    #        computed from the PRE-reclassification window payload ---
    conn = op.get_bind()
    windows = conn.exec_driver_sql(
        f"""
        select e.track, e.snapshot_sha256, e.eval_window, min(e.locked_at) as first_locked_at,
               (select r.split_sha256 from {SCHEMA}.kg_qwen_agent_runs r
                 where r.track_key = e.track and r.snapshot_sha256 = e.snapshot_sha256
                 order by r.created_at limit 1) as split_version
        from {SCHEMA}.kg_qwen_evaluation_sets e
        group by e.track, e.snapshot_sha256, e.eval_window
        """
    ).fetchall()
    for track, snapshot_sha256, window, first_locked_at, split_version in windows:
        canonical_window = {k: window.get(k) for k in _WINDOW_KEYS}
        payload = {
            "track_key": track,
            "snapshot_sha256": snapshot_sha256,
            "eval_window": canonical_window,
            "identity": canonical_window.get("identity"),
            "split_version": split_version or "UNKNOWN_PRE_GOVERNANCE",
        }
        registry_id = _registry_id(payload)
        conn.exec_driver_sql(
            f"""
            insert into {SCHEMA}.kg_qwen_heldout_registry
                (registry_id, global_sha256, global_identity, track_key, identity, snapshot_sha256,
                 eval_window, split_version, evaluator_version, status, registered_at, locked_at)
            values (%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s,%s,'REPLAY_ONLY',%s,%s)
            on conflict (registry_id) do nothing
            """,
            (
                registry_id,
                _sha256_json(payload),
                json.dumps(
                    {
                        "seeded_from": "0043_kg_heldout_governance",
                        "reason": "WINDOW_ALREADY_OPENED_BEFORE_GLOBAL_REGISTRY",
                        "payload": payload,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                track,
                canonical_window.get("identity"),
                snapshot_sha256,
                json.dumps(canonical_window, ensure_ascii=False, default=str),
                payload["split_version"],
                "kg_qwen_heldout_evaluator.v2",
                first_locked_at,
                first_locked_at,
            ),
        )
        # link existing evaluation rows to the seeded registry row
        conn.exec_driver_sql(
            f"""
            update {SCHEMA}.kg_qwen_evaluation_sets
            set registry_id = %s
            where track = %s and snapshot_sha256 = %s and registry_id is null
            """,
            (registry_id, track, snapshot_sha256),
        )

    # --- 4. reclassify the multiply-opened window (metrics preserved verbatim) ---
    op.execute(
        f"""
        update {SCHEMA}.kg_qwen_evaluation_sets
        set identity = 'RETROSPECTIVE_REPLAY_NOT_FRESH',
            eval_window = jsonb_set(eval_window, '{{flags}}',
                '["RETROSPECTIVE_REPLAY_NOT_FRESH","NOT_FRESH_HELDOUT"]'::jsonb),
            metrics = metrics || '{{"flags":["RETROSPECTIVE_REPLAY_NOT_FRESH","NOT_FRESH_HELDOUT"],
                "reclassified_from":"FRESH_HELDOUT_LOCKED",
                "reclassified_reason":"SAME_SNAPSHOT_WINDOW_EVALUATED_BY_MULTIPLE_RUNS",
                "reclassified_by":"0043_kg_heldout_governance"}}'::jsonb
        where identity = 'FRESH_HELDOUT_LOCKED'
        """
    )

    # --- 5. grants (same pattern as 0042; registry lifecycle updates are narrow) ---
    op.execute(f"grant select on {SCHEMA}.kg_qwen_heldout_registry to epidemic_dashboard_ro")
    op.execute(f"grant select, insert on {SCHEMA}.kg_qwen_heldout_registry to epidemic_app")
    op.execute(
        f"grant update (status, locked_at, first_qwen_started_at, tuning_finished_at, consumed_at, "
        f"consumer_run_id) on {SCHEMA}.kg_qwen_heldout_registry to epidemic_app"
    )
    op.execute(f"grant update (registry_id) on {SCHEMA}.kg_qwen_evaluation_sets to epidemic_app")
    op.execute(
        f"grant update (first_qwen_started_at, tuning_finished_at) on {SCHEMA}.kg_qwen_agent_runs to epidemic_app"
    )


def downgrade() -> None:
    # schema-only reversal; the RETROSPECTIVE_REPLAY_NOT_FRESH reclassification
    # is an audit correction and is deliberately left in place.
    op.execute(f"alter table {SCHEMA}.kg_qwen_agent_runs drop column if exists tuning_finished_at")
    op.execute(f"alter table {SCHEMA}.kg_qwen_agent_runs drop column if exists first_qwen_started_at")
    op.execute(f"alter table {SCHEMA}.kg_qwen_evaluation_sets drop column if exists registry_id")
    op.execute(f"drop table if exists {SCHEMA}.kg_qwen_heldout_registry")
