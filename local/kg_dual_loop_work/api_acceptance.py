"""API acceptance for the KG dual-loop endpoints against http://127.0.0.1:8130.

Covers: unauthenticated rejection, authenticated reads, shadow-execution POST
idempotency (8-way concurrent), analyst feedback write+read, reconciliation
entry, CSV export. Never prints credentials.
"""
import concurrent.futures as cf
import http.cookiejar
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8130"
OUT = Path(__file__).parent / "api_acceptance.json"

GET_ENDPOINTS = [
    "/api/v1/kg/forecast-eligible-series",
]
RUN_SUBRESOURCES = ["", "/backtest", "/forecast", "/feedback", "/export.csv"]

results = {"unauthenticated": {}, "authenticated": {}, "idempotency": {}, "errors": []}


def make_opener(with_login: bool):
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    if with_login:
        lines = (Path(__file__).parent.parent / "secrets" / "dashboard_login.txt").read_text(encoding="utf-8").splitlines()
        user, pw = lines[0].strip(), lines[1].strip()
        req = urllib.request.Request(BASE + "/login", data=urllib.parse.urlencode({"username": user, "password": pw}).encode(), method="POST")
        try:
            opener.open(req, timeout=15)
        except urllib.error.HTTPError as e:
            if e.code not in (200, 302, 303):
                raise
    return opener


def call(opener, method, path, body=None, idem=None):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if idem:
        headers["Idempotency-Key"] = idem
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        resp = opener.open(req, timeout=60)
        raw = resp.read()
        if path.endswith(".csv"):
            return resp.status, {"csv_bytes": len(raw), "csv_lines": len(raw.decode().splitlines())}
        return resp.status, json.loads(raw.decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


anon = make_opener(False)
auth = make_opener(True)

# 1. unauthenticated rejection
for ep in GET_ENDPOINTS:
    s, _ = call(anon, "GET", ep)
    results["unauthenticated"][ep] = s
s, _ = call(anon, "POST", "/api/v1/kg/shadow-executions", {"recommendation_id": "c219de41-ea2e-58d9-86cd-339b03213401"})
results["unauthenticated"]["POST /api/v1/kg/shadow-executions"] = s
s, _ = call(anon, "POST", "/api/v1/kg/reconciliation/run", {})
results["unauthenticated"]["POST /api/v1/kg/reconciliation/run"] = s

# 2. authenticated flow
s, series = call(auth, "GET", "/api/v1/kg/forecast-eligible-series")
results["authenticated"]["forecast-eligible-series"] = s
items = series.get("items", [])
a_item = next(i for i in items if i["country"] == "AUT" and i["disease"] == "legionella")
b_item = next(i for i in items if i["country"] == "PER" and i["disease"] == "influenza" and i["indicator"] == "influenza_ili_cases")
results["a_series"] = a_item
results["b_series"] = b_item

for label, rid in [("A", a_item["recommendation_id"]), ("B", b_item["recommendation_id"])]:
    s, rec = call(auth, "GET", f"/api/v1/kg/recommendations/{rid}")
    results["authenticated"][f"recommendation_{label}"] = s
    results[f"rec_{label}_runs"] = rec.get("shadow_runs")

# 3. 8-way concurrent identical POST -> one semantic run
def do_post(i):
    op = make_opener(True)
    return call(op, "POST", "/api/v1/kg/shadow-executions",
                {"recommendation_id": a_item["recommendation_id"]},
                idem="acceptance-8way-a4")

with cf.ThreadPoolExecutor(max_workers=8) as pool:
    posts = list(pool.map(do_post, range(8)))
run_ids = {p[1].get("run_id") for p in posts}
statuses = [p[0] for p in posts]
results["idempotency"] = {
    "statuses": statuses,
    "distinct_run_ids": list(run_ids),
    "one_semantic_run": len(run_ids) == 1,
    "replay_flags": [p[1].get("status") or ("replay" if p[1].get("idempotent_replay") else None) for p in posts],
}
run_id_a = posts[0][1].get("run_id") or (results["rec_A_runs"][0]["run_id"])

s, lst = call(auth, "GET", "/api/v1/kg/shadow-executions?country=AUT")
results["authenticated"]["shadow-executions-list"] = s
run_id_a = lst["items"][0]["run_id"]
run_id_b = None
s, lstb = call(auth, "GET", "/api/v1/kg/shadow-executions?country=PER")
if lstb.get("items"):
    run_id_b = lstb["items"][0]["run_id"]

for label, rid in [("A", run_id_a), ("B", run_id_b)]:
    if not rid:
        continue
    for sub in RUN_SUBRESOURCES:
        s, body = call(auth, "GET", f"/api/v1/kg/shadow-executions/{rid}{sub}")
        results["authenticated"][f"run_{label}{sub or '/detail'}"] = s
        if sub == "/backtest":
            results[f"backtest_{label}_folds"] = len(body.get("folds", []))
            results[f"backtest_{label}_metrics_rows"] = len(body.get("metrics", []))
            results[f"backtest_{label}_champion"] = body.get("champion", {}).get("champion_adapter_id")
        if sub == "/forecast":
            results[f"forecast_{label}_values"] = len(body.get("values", []))
            results[f"forecast_{label}_history"] = len(body.get("history", []))
        if sub == "/feedback":
            results[f"feedback_{label}_summary"] = body.get("summary")
            cycles = body.get("learning_cycles", [])
            if cycles:
                results[f"learning_cycle_{label}"] = cycles[0]["learning_cycle_id"]

# learning cycle detail
for label in ("A", "B"):
    cid = results.get(f"learning_cycle_{label}")
    if cid:
        s, body = call(auth, "GET", f"/api/v1/kg/learning-cycles/{cid}")
        results["authenticated"][f"learning-cycle_{label}"] = s
        results[f"learning_cycle_{label}_decision"] = body.get("champion_decision")

# 4. analyst feedback write + read back
s, fb = call(auth, "POST", f"/api/v1/kg/shadow-executions/{run_id_a}/analyst-feedback",
             {"actor": "acceptance-bot", "decision_code": "USEFUL", "rating": 4,
              "comment": "acceptance check"}, idem="acceptance-analyst-a2-1")
results["authenticated"]["analyst-feedback-post"] = s
results["analyst_feedback_created"] = fb
s, fb2 = call(auth, "GET", f"/api/v1/kg/shadow-executions/{run_id_a}/feedback")
results["analyst_feedback_visible"] = any(a.get("actor") == "acceptance-bot" for a in fb2.get("analyst_feedback", []))
# invalid decision code rejected
s, _ = call(auth, "POST", f"/api/v1/kg/shadow-executions/{run_id_a}/analyst-feedback",
            {"actor": "acceptance-bot", "decision_code": "HACK"}, idem="acceptance-analyst-a2-bad")
results["analyst_feedback_invalid_code_status"] = s

# 5. reconciliation entry
s, rec_run = call(auth, "POST", "/api/v1/kg/reconciliation/run", {}, idem="acceptance-reconcile-3")
results["authenticated"]["reconciliation-run"] = s
results["reconciliation_result"] = rec_run

OUT.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
print(json.dumps(results["unauthenticated"], indent=2))
print("auth statuses:", results["authenticated"])
print("idempotency:", results["idempotency"])
print("A champion:", results.get("backtest_A_champion"), "B champion:", results.get("backtest_B_champion"))
print("A feedback:", results.get("feedback_A_summary"))
print("B feedback:", results.get("feedback_B_summary"))
print("analyst visible:", results["analyst_feedback_visible"], "invalid code status:", results["analyst_feedback_invalid_code_status"])
print("reconcile:", results["reconciliation_result"])
