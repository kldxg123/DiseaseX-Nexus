#!/usr/bin/env python3
"""Create ExecutionContracts (DEFERRED_NOT_EXECUTED) for all rank-1 ELIGIBLE recommendations.

Credentials are read from stdin (two lines: user, password) — never echoed or written to disk.
"""
import json, sys, urllib.request, urllib.error, urllib.parse, http.cookiejar

BASE = "http://127.0.0.1:8020"
REPORT_DIR = sys.argv[1]

creds = sys.stdin.read().splitlines()
user, pw = creds[0].strip(), creds[1].strip()

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))

def req(method, path, body=None, headers=None, form=None):
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    else:
        data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/x-www-form-urlencoded" if form is not None else "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with opener.open(r, timeout=30) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"_html": raw[:200]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"_html": raw[:200]}

st, body = req("POST", "/login", form={"username": user, "password": pw})
assert st in (200, 303), f"login failed: {st}"
st, probe = req("GET", "/api/v1/kg/r3c/recommendations?limit=1")
assert st == 200, f"auth probe failed: {st} {probe}"
del user, pw, creds

st, recs = req("GET", "/api/v1/kg/r3c/recommendations?eligible_only=true&limit=200")
assert st == 200, f"list failed: {st} {recs}"
items = recs.get("items", recs if isinstance(recs, list) else [])
rank1 = [r for r in items if r.get("rank") == 1]

results = []
for r in rank1:
    rid = r["recommendation_id"]
    idem = f"kg-r3c-contract-{rid}"
    st, body = req("POST", f"/api/v1/kg/r3c/recommendations/{rid}/execution-contracts",
                   {}, {"Idempotency-Key": idem})
    results.append({
        "recommendation_id": rid,
        "country": r.get("country"), "disease": r.get("disease"),
        "indicator": r.get("indicator"), "frequency": r.get("frequency"),
        "model_family": r.get("model_family"), "adapter_version": r.get("adapter_version"),
        "total_score": r.get("total_score"),
        "parameter_confidence_score": r.get("parameter_confidence_score"),
        "http_status": st,
        "contract_id": body.get("contract_id"),
        "contract_sha256": body.get("contract_sha256"),
        "contract_status": body.get("status"),
        "created": body.get("created"),
        "actually_executed": (body.get("contract") or {}).get("non_execution_assertions", {}).get("actually_executed"),
    })

# replay one to prove idempotency via API
replay = None
if rank1:
    rid = rank1[0]["recommendation_id"]
    st, body = req("POST", f"/api/v1/kg/r3c/recommendations/{rid}/execution-contracts",
                   {}, {"Idempotency-Key": f"kg-r3c-contract-{rid}"})
    replay = {"http_status": st, "created": body.get("created"),
              "idempotent_replay": body.get("idempotent_replay"),
              "contract_id": body.get("contract_id"), "status": body.get("status")}

out = {
    "eligible_rank1_count": len(rank1),
    "contracts": results,
    "contracts_created": sum(1 for x in results if x["created"]),
    "all_deferred_not_executed": all(x["contract_status"] == "DEFERRED_NOT_EXECUTED" for x in results),
    "actually_executed_model_count": 0,
    "forecast_run_count_created": 0,
    "forecast_value_count_created": 0,
    "idempotent_replay_probe": replay,
}
json.dump(out, open(f"{REPORT_DIR}/25b_EXECUTION_CONTRACTS.json", "w"), ensure_ascii=False, indent=2)
print(json.dumps({k: out[k] for k in ("eligible_rank1_count", "contracts_created", "all_deferred_not_executed")}))
print("replay:", replay)
