"""Smoke driver for the kg_pt demo API on vm201 (localhost only, no secrets printed)."""
from __future__ import annotations

import json
import sys
import urllib.request
import urllib.error
import http.cookiejar
from pathlib import Path

BASE = "http://127.0.0.1:8020"

creds = {}
for line in Path("USER_HOME/projects/openclaw/runtime/secrets/vm201_runtime.env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        creds[k.strip()] = v.strip()

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def post_form(path, data):
    body = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in data.items()).encode()
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

import urllib.parse  # noqa: E402

status, _, _ = post_form("/login", {"username": creds["DASHBOARD_LOGIN_USER"], "password": creds["DASHBOARD_LOGIN_PASSWORD"]})
print("login:", status)
assert status in (200, 303), "login failed"

idem = sys.argv[1] if len(sys.argv) > 1 else "pt-demo-smoke:v1"
status, headers, body = post_json("/api/v1/kg/pt/demo/run", {"demo_key": "AUT:legionella:legionnaires_reported_cases:month"}, idem)
d = json.loads(body)
print("run status:", status, "idempotent-replay:", headers.get("Idempotent-Replay"))
print("result.status:", d.get("status"))
if d.get("status") in ("COMPLETED", "IDEMPOTENT_REPLAY"):
    for k in ("shadow_run_id", "tuning_run_id", "tuning_run_id_cycle2", "best_trial_index", "n_trials", "n_trials_cycle2"):
        print(f"  {k}: {d.get(k)}")
    ch = d.get("champion") or {}
    print("  champion:", json.dumps(ch, ensure_ascii=False)[:400])
    fb = d.get("feedback") or {}
    print("  feedback:", json.dumps(fb)[:300])
    rt = d.get("retrain") or {}
    print("  retrain:", json.dumps(rt)[:300])
else:
    print(json.dumps(d, ensure_ascii=False)[:800])

for path in (
    "/api/v1/kg/pt/demo/overview",
):
    s, data = get(path)
    print(path, "->", s)
    if s == 200:
        print("  snapshot n_obs:", (data.get("snapshot") or {}).get("n_obs"))
        tc = data.get("test_comparison") or {}
        print("  test_comparison:", json.dumps(tc, ensure_ascii=False)[:300])
        fs = data.get("feedback_summary") or {}
        print("  feedback_summary:", json.dumps(fs)[:200])
        print("  learning_cycles:", len(data.get("learning_cycles") or []))
        print("  trials endpoint next")
        trs = data.get("tuning_runs") or []
        if trs:
            tid = trs[0].get("tuning_run_id")
            s2, d2 = get(f"/api/v1/kg/pt/demo/trials?tuning_run_id={tid}")
            print("  trials ->", s2, "count:", len((d2 or {}).get("trials") or []))
        rid = (data.get("tuning_runs") or [{}])[0].get("shadow_run_id")
        if rid:
            s3, d3 = get(f"/api/v1/kg/pt/demo/forecast?run_id={rid}")
            print("  forecast ->", s3, "values:", len((d3 or {}).get("values") or []), "history:", len((d3 or {}).get("history") or []))
