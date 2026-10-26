"""Local smoke driver for the kg_pt demo API via SSH tunnel (no secrets printed)."""
from __future__ import annotations

import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18130"

creds = Path(r"F:/作业/aibola/secrets/dashboard_login.txt").read_text(encoding="utf-8").splitlines()
USER, PW = creds[0].strip(), creds[1].strip()

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def post_form(path, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        r = opener.open(req, timeout=30)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def post_json(path, payload, idem):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Idempotency-Key": idem},
    )
    try:
        r = opener.open(req, timeout=900)
        return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def get(path):
    try:
        r = opener.open(BASE + path, timeout=120)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]


status, _, _ = post_form("/login", {"username": USER, "password": PW})
print("login:", status)
assert status in (200, 303), "login failed"

idem = sys.argv[1] if len(sys.argv) > 1 else "pt-demo-run:v1"
status, headers, body = post_json("/api/v1/kg/pt/demo/run", {"demo_key": "AUT:legionella:legionnaires_reported_cases:month"}, idem)
d = json.loads(body)
print("run http:", status, "idempotent-replay:", headers.get("Idempotent-Replay"))
print("result.status:", d.get("status"))
if d.get("status") in ("COMPLETED", "IDEMPOTENT_REPLAY"):
    for k in ("shadow_run_id", "tuning_run_id", "tuning_run_id_cycle2", "best_trial_index", "n_trials", "n_trials_cycle2"):
        print(f"  {k}: {d.get(k)}")
    print("  best_params:", json.dumps(d.get("best_params"))[:200])
    print("  champion:", json.dumps(d.get("champion"), ensure_ascii=False)[:500])
    print("  champion_cycle2:", json.dumps(d.get("champion_cycle2"), ensure_ascii=False)[:400])
    print("  feedback:", json.dumps(d.get("feedback"))[:300])
    print("  retrain:", json.dumps(d.get("retrain"))[:300])
    print("  learning_cycle_ids:", d.get("learning_cycle_ids"))
else:
    print(json.dumps(d, ensure_ascii=False)[:900])

s, data = get("/api/v1/kg/pt/demo/overview")
print("overview ->", s)
if s == 200:
    print("  snapshot n_obs:", (data.get("snapshot") or {}).get("n_obs"))
    print("  split:", json.dumps(data.get("split"))[:250])
    print("  test_comparison:", json.dumps(data.get("test_comparison"), ensure_ascii=False)[:400])
    print("  feedback_summary:", json.dumps(data.get("feedback_summary"))[:250])
    print("  learning_cycles:", len(data.get("learning_cycles") or []))
    print("  contracts:", len(data.get("parameter_contracts") or []))
    trs = data.get("tuning_runs") or []
    if trs:
        tid = trs[0].get("tuning_run_id")
        s2, d2 = get(f"/api/v1/kg/pt/demo/trials?tuning_run_id={tid}")
        trials = (d2 or {}).get("trials") or []
        print("  trials ->", s2, "count:", len(trials))
        if trials:
            best = [t for t in trials if t.get("gate_pass")]
            print("  gate_pass trials:", len(best))
        rid = trs[0].get("shadow_run_id")
        if rid:
            s3, d3 = get(f"/api/v1/kg/pt/demo/forecast?run_id={rid}")
            print("  forecast ->", s3, "values:", len((d3 or {}).get("values") or []), "history:", len((d3 or {}).get("history") or []))
