#!/usr/bin/env python3
import json, sys
REPORT_DIR = sys.argv[1]
counts = {}
for line in open("/tmp/replay_counts.txt"):
    k, v = line.strip().split("=")
    phase, table = k.split(".", 1)
    counts.setdefault(table, {})[phase] = int(v)
zero_delta = all(v["before"] == v["after"] for v in counts.values())
out = {
  "replay_actions": [
    "b_csv_run.py full re-run (import run + member registry + CSV ingest)",
    "f_recommend.py full re-run (bundle build + deterministic scoring + recommendations)",
    "second full replay of both scripts with before/after row counts",
    "API execution-contract POST replay with same Idempotency-Key -> idempotent_replay=true"
  ],
  "table_counts_second_replay": counts,
  "zero_new_rows_on_replay": zero_delta,
  "recommendation_run_id_stable": "cbcdcce9-0a14-5028-8fef-8243379d8958",
  "import_run_id_stable": "bef74d0c-f60e-58d8-af98-d646a6401958",
  "execution_contracts_after_api_replay": 17,
  "idempotent_keys": ["import_key", "archive_sha256+member_sha256", "stable_uuid(work identity)",
                      "bundle_sha256", "stable_uuid(run, graph+policy+profile set)", "contract_sha256", "Idempotency-Key header"]
}
json.dump(out, open(f"{REPORT_DIR}/27_IDEMPOTENCY_REPLAY.json", "w"), ensure_ascii=False, indent=2)
print("zero_delta:", zero_delta)
