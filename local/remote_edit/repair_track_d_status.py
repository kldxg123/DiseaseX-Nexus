"""Correct Track D final status without reopening its consumed held-out set."""

from __future__ import annotations

from epidemic_core.kg_r3c.dashboard_app import write_conn

RUN_ID = "8c6324d5-d0bb-525f-b39a-9c0500a7d8a0"


def main() -> None:
    with write_conn() as conn:
        row = conn.execute(
            """
            select r.target_status, e.metrics
            from epidemic_kg.kg_qwen_agent_runs r
            join epidemic_kg.kg_qwen_evaluation_sets e on e.run_id=r.run_id
            where r.run_id=%s
            for update of r
            """,
            (RUN_ID,),
        ).fetchone()
        assert row is not None
        certification = (row["metrics"] or {}).get("certification") or {}
        assert certification.get("certified") is False
        assert certification.get("verdict") == "FRESH_HELDOUT_GATES_FAILED"
        conn.execute(
            """
            update epidemic_kg.kg_qwen_agent_runs
            set target_status='FRESH_HELDOUT_GATES_FAILED'
            where run_id=%s and target_status='TARGET_MET'
            """,
            (RUN_ID,),
        )
        conn.commit()


if __name__ == "__main__":
    main()
