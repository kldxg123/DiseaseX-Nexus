from __future__ import annotations

import json

from epidemic_core.kg_qwen.pipeline import autotuning_evaluation
from epidemic_core.kg_r3c.dashboard_app import write_conn

RUN_ID = "d9bc833c-d0fd-5290-b567-f8dd3042fa7a"


def main() -> None:
    with write_conn() as conn:
        conn.execute("begin transaction isolation level repeatable read read only")
        result = autotuning_evaluation(conn, RUN_ID)
        conn.rollback()
    paired = (result or {}).get("metrics", {}).get("paired", {})
    print(
        json.dumps(
            {
                "champion_labeled_n": paired.get("champion_labeled_n"),
                "paired_n": paired.get("paired_n"),
                "paired_sample_match": paired.get("paired_sample_match"),
                "gate_failures": paired.get("gate_failures"),
                "baselines": {
                    key: {
                        field: value.get(field)
                        for field in ("sample_matched", "labeled_n", "paired_n", "wape")
                    }
                    for key, value in (paired.get("baselines") or {}).items()
                },
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
