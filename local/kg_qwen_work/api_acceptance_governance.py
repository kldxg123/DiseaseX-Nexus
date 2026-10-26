"""Governance-repair API acceptance (through the local SSH tunnel 18130).

Checks: unauth 401, Track A/B/C overview governance blocks, Track B global
refusal (HELDOUT_ALREADY_CONSUMED, no new run), idempotent replay and 8-way
concurrency on an existing key, evaluation endpoint classification fields.
Writes api_acceptance_governance.json next to this script.
"""

import concurrent.futures as cf
import json
import urllib.request
import urllib.error
import http.cookiejar
from pathlib import Path

BASE = "http://127.0.0.1:18130"
OUT = Path(__file__).with_name("api_acceptance_governance.json")
TRACK_A = "AUT:legionella:legionnaires_reported_cases:month"
TRACK_B = "FIN:hantavirus:puumala_virus_reports:week"
TRACK_C = "CHL:influenza:influenza_positive_detections:week"

creds = Path(r"F:\作业\aibola\secrets\dashboard_login.txt").read_text(encoding="utf-8").splitlines()
USER, PW = creds[0].strip(), creds[1].strip()

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
anon = urllib.request.build_opener()

checks = []


def rec(name, ok, detail=""):
    checks.append({"name": name, "pass": bool(ok), "detail": str(detail)[:400]})
    print(("PASS " if ok else "FAIL "), name, "|", str(detail)[:160], flush=True)


def req(op, method, path, body=None, headers=None):
    r = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with op.open(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


# 1-2. unauthenticated
try:
    anon.open(BASE + "/api/v1/kg/autotuning/overview", timeout=30)
    rec("unauth_overview_401", False, "no exception")
except urllib.error.HTTPError as e:
    rec("unauth_overview_401", e.code == 401, f"status={e.code}")
s, d = req(anon, "POST", "/api/v1/kg/autotuning/run", {"track_key": TRACK_B}, {"Idempotency-Key": "unauth-probe"})
rec("unauth_run_401", s == 401, f"status={s}")

# login (form-encoded, same contract as the dashboard login form)
import urllib.parse

login_req = urllib.request.Request(
    BASE + "/login", data=urllib.parse.urlencode({"username": USER, "password": PW}).encode(), method="POST"
)
try:
    with opener.open(login_req, timeout=15) as resp:
        login_status = resp.status
except urllib.error.HTTPError as e:
    login_status = e.code
rec("login", login_status in (200, 302, 303), f"status={login_status}")

# 4. Track A overview: replay classification + registry REPLAY_ONLY
s, d = req(opener, "GET", "/api/v1/kg/autotuning/overview?track_key=" + TRACK_A)
regs = d.get("heldout_registry") or []
ev = d.get("evaluation") or {}
rec(
    "trackA_overview_replay",
    s == 200 and regs and regs[0].get("status") == "REPLAY_ONLY" and ev.get("identity") == "REUSED_HISTORICAL_BENCHMARK",
    f"status={s} registry={[r.get('status') for r in regs]} identity={ev.get('identity')}",
)

# 5. Track B overview: reclassified retrospective, never fresh again
s, d = req(opener, "GET", "/api/v1/kg/autotuning/overview?track_key=" + TRACK_B)
regs = d.get("heldout_registry") or []
ev = d.get("evaluation") or {}
rec(
    "trackB_reclassified",
    s == 200
    and ev.get("identity") == "RETROSPECTIVE_REPLAY_NOT_FRESH"
    and ev.get("is_fresh") is False
    and regs and regs[0].get("status") == "REPLAY_ONLY"
    and ev.get("classification_note_zh"),
    f"identity={ev.get('identity')} is_fresh={ev.get('is_fresh')} registry={[r.get('status') for r in regs]} "
    f"note={str(ev.get('classification_note_zh'))[:40]}",
)

# 5b. Track B evaluation endpoint carries governance fields
run_b = (d.get("run") or {}).get("run_id")
s, d2 = req(opener, "GET", f"/api/v1/kg/autotuning/evaluation?run_id={run_b}")
rec(
    "trackB_evaluation_governance",
    s == 200 and d2.get("registry", {}) is not None and d2.get("classification") == "REPLAY_ONLY_NOT_FRESH",
    f"status={s} classification={d2.get('classification')} registry_status={(d2.get('registry') or {}).get('status')}",
)

# 6. Track C overview: registry locked before tuning finishes
s, d = req(opener, "GET", "/api/v1/kg/autotuning/overview?track_key=" + TRACK_C)
regs = d.get("heldout_registry") or []
rec(
    "trackC_registry_locked",
    s in (200, 404) and (not regs or regs[0].get("status") in ("LOCKED_UNOPENED", "CONSUMED", "REGISTERED")),
    f"status={s} registry={[r.get('status') for r in regs]}",
)

# 7. Track B POST with a NEW key must be refused globally and create no run
s, d = req(
    opener, "POST", "/api/v1/kg/autotuning/run",
    {"track_key": TRACK_B}, {"Idempotency-Key": "kq-trackB-governance-probe-20260812"},
)
rec(
    "trackB_post_refused",
    s == 409 and d.get("status") == "HELDOUT_ALREADY_CONSUMED",
    f"status={s} body_status={d.get('status')} reason={str(d.get('reason'))[:80]}",
)

# 8. Track A POST with the EXISTING key replays (no new tuning)
s, d = req(
    opener, "POST", "/api/v1/kg/autotuning/run",
    {"track_key": TRACK_A}, {"Idempotency-Key": "kq-trackA-qwen3-20260812"},
)
rec(
    "trackA_idempotent_replay",
    s == 200 and (d.get("idempotent_replay") or d.get("status") == "IDEMPOTENT_REPLAY"),
    f"status={s} body={str(d)[:120]}",
)


# 9. 8-way concurrent same-key POSTs -> exactly one semantic run (all replay/one run)
def post_one(i):
    return req(
        opener, "POST", "/api/v1/kg/autotuning/run",
        {"track_key": TRACK_A}, {"Idempotency-Key": "kq-trackA-qwen3-20260812"},
    )


with cf.ThreadPoolExecutor(8) as ex:
    results = list(ex.map(post_one, range(8)))
statuses = [s for s, _ in results]
run_ids = {(d or {}).get("run_id") for _, d in results}
rec(
    "eight_way_idempotent",
    all(s == 200 for s in statuses) and len(run_ids - {None}) <= 1,
    f"statuses={statuses} run_ids={run_ids}",
)

OUT.write_text(
    json.dumps({"checks": checks, "all_pass": all(c["pass"] for c in checks)}, ensure_ascii=False, indent=1),
    encoding="utf-8",
)
print("ALL_PASS =", all(c["pass"] for c in checks))
