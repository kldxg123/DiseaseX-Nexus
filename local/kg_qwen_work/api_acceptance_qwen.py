"""API acceptance for the KG Qwen autotuning endpoints (local tunnel :18130).

Covers: unauthenticated 401 on all 8 endpoints, authenticated reads for both
tracks, POST /run without Idempotency-Key (400), unknown track (422),
8-way concurrent POST with the same key producing exactly one semantic run
(replay returns the same run_id), POST /reconcile idempotency, and payload
shape sanity for the dashboard block. Never prints credentials.
"""
import concurrent.futures as cf
import http.cookiejar
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:18130"
OUT = Path(__file__).parent / "api_acceptance_qwen.json"
TRACK_A = "AUT:legionella:legionnaires_reported_cases:month"
TRACK_B = "FIN:hantavirus:puumala_virus_reports:week"

results = {"unauthenticated": {}, "authenticated": {}, "idempotency": {}, "errors": []}


def make_opener(with_login: bool):
    cj = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    if with_login:
        lines = (Path(r"F:/作业/aibola/secrets/dashboard_login.txt")).read_text(encoding="utf-8").splitlines()
        user, pw = lines[0].strip(), lines[1].strip()
        req = urllib.request.Request(
            BASE + "/login",
            data=urllib.parse.urlencode({"username": user, "password": pw}).encode(),
            method="POST",
        )
        try:
            opener.open(req, timeout=15)
        except urllib.error.HTTPError as e:
            if e.code not in (200, 302, 303):
                raise
    return opener


def call(opener, method, path, body=None, idem=None, timeout=120):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if idem:
        headers["Idempotency-Key"] = idem
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        resp = opener.open(req, timeout=timeout)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


anon = make_opener(False)
auth = make_opener(True)

GETS = [
    f"/api/v1/kg/autotuning/overview?track_key={urllib.parse.quote(TRACK_A)}",
    "/api/v1/kg/autotuning/plans?run_id=00000000-0000-0000-0000-000000000000",
    "/api/v1/kg/autotuning/rounds?run_id=00000000-0000-0000-0000-000000000000",
    "/api/v1/kg/autotuning/trials?run_id=00000000-0000-0000-0000-000000000000",
    "/api/v1/kg/autotuning/evaluation?run_id=00000000-0000-0000-0000-000000000000",
    "/api/v1/kg/autotuning/feedback?run_id=00000000-0000-0000-0000-000000000000",
]
for ep in GETS:
    s, _ = call(anon, "GET", ep)
    results["unauthenticated"][ep.split("?")[0]] = s
s, _ = call(anon, "POST", "/api/v1/kg/autotuning/run", {"track_key": TRACK_A}, idem="kq-api-accept-anon")
results["unauthenticated"]["POST /run"] = s
s, _ = call(anon, "POST", "/api/v1/kg/autotuning/reconcile", {"run_id": "00000000-0000-0000-0000-000000000000"}, idem="kq-api-accept-anon")
results["unauthenticated"]["POST /reconcile"] = s

# authenticated reads
s, ov_a = call(auth, "GET", GETS[0])
results["authenticated"]["overview_trackA"] = s
s, ov_b = call(auth, "GET", f"/api/v1/kg/autotuning/overview?track_key={urllib.parse.quote(TRACK_B)}")
results["authenticated"]["overview_trackB"] = s

run_id = (ov_a.get("run") or {}).get("run_id")
results["authenticated"]["trackA_run_id"] = run_id
results["authenticated"]["trackA_target_status"] = (ov_a.get("run") or {}).get("target_status")
results["authenticated"]["trackA_eval_identity"] = (ov_a.get("evaluation") or {}).get("identity")
results["authenticated"]["trackA_eval_count"] = (ov_a.get("evaluation") or {}).get("eval_count")
results["authenticated"]["trackA_champion"] = ov_a.get("champion")
results["authenticated"]["trackA_rounds"] = len(ov_a.get("rounds") or [])
results["authenticated"]["trackA_trials"] = len(ov_a.get("trials") or [])
results["authenticated"]["trackA_plans"] = [
    {"round_index": p.get("round_index"), "plan_source": p.get("plan_source"),
     "validation_status": p.get("validation_status")}
    for p in (ov_a.get("plans") or [])
]
run_id_b = (ov_b.get("run") or {}).get("run_id")
results["authenticated"]["trackB_run_id"] = run_id_b
results["authenticated"]["trackB_eval_identity"] = (ov_b.get("evaluation") or {}).get("identity")
results["authenticated"]["trackB_eval_count"] = (ov_b.get("evaluation") or {}).get("eval_count")
results["authenticated"]["trackB_wape"] = (ov_b.get("evaluation") or {}).get("wape")
results["authenticated"]["trackB_agreement_pct"] = (ov_b.get("evaluation") or {}).get("agreement_pct")
results["authenticated"]["trackB_coverage"] = (ov_b.get("evaluation") or {}).get("coverage")

for name, rid in (("A", run_id), ("B", run_id_b)):
    if not rid:
        continue
    for sub in ("plans", "rounds", "trials", "evaluation", "feedback"):
        s, payload = call(auth, "GET", f"/api/v1/kg/autotuning/{sub}?run_id={rid}")
        results["authenticated"][f"track{name}_{sub}"] = s

# POST validation
s, _ = call(auth, "POST", "/api/v1/kg/autotuning/run", {"track_key": TRACK_A})
results["idempotency"]["missing_key_status"] = s
s, _ = call(auth, "POST", "/api/v1/kg/autotuning/run", {"track_key": "XXX:unknown:thing:month"}, idem="kq-api-accept-badtrack")
results["idempotency"]["unknown_track_status"] = s

# 8-way concurrent same-key POST -> exactly one semantic run (replay of the existing run)
def post_run(i):
    op = make_opener(True)
    s, payload = call(op, "POST", "/api/v1/kg/autotuning/run", {"track_key": TRACK_A}, idem="kq-trackA-20260811", timeout=300)
    return s, payload.get("run_id"), payload.get("status")

with cf.ThreadPoolExecutor(max_workers=8) as ex:
    outcomes = list(ex.map(post_run, range(8)))
results["idempotency"]["concurrent_statuses"] = [o[0] for o in outcomes]
results["idempotency"]["concurrent_run_ids"] = sorted({str(o[1]) for o in outcomes})
results["idempotency"]["concurrent_semantic_runs"] = len({str(o[1]) for o in outcomes})

# reconcile idempotent replay
if run_id:
    s1, r1 = call(auth, "POST", "/api/v1/kg/autotuning/reconcile", {"run_id": run_id}, idem="kq-reconcile-accept-1", timeout=300)
    s2, r2 = call(auth, "POST", "/api/v1/kg/autotuning/reconcile", {"run_id": run_id}, idem="kq-reconcile-accept-1", timeout=300)
    results["idempotency"]["reconcile_first"] = s1
    results["idempotency"]["reconcile_replay"] = (s2, r2.get("idempotent_replay", r2.get("status")))

ok = True
ok &= all(v == 401 for v in results["unauthenticated"].values())
ok &= results["authenticated"].get("overview_trackA") == 200
ok &= results["idempotency"].get("missing_key_status") == 400
ok &= results["idempotency"].get("unknown_track_status") == 422
ok &= results["idempotency"].get("concurrent_semantic_runs") == 1
results["verdict"] = "PASS" if ok else "FAIL"
OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(json.dumps(results["authenticated"], ensure_ascii=False, default=str)[:1500])
print(json.dumps(results["idempotency"], ensure_ascii=False, default=str))
print("VERDICT", results["verdict"])
sys.exit(0 if ok else 1)
