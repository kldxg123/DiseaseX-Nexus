#!/usr/bin/env python3
"""23_API_OPENAPI_PROOF: authenticated API pass + unauthenticated 401 + OpenAPI presence.
Credentials from stdin (two lines), never persisted.
Usage: api_proof.py REPORT_DIR WORK_ID
"""
import json, sys, urllib.request, urllib.error, urllib.parse, http.cookiejar

BASE = "http://127.0.0.1:8020"
REPORT_DIR = sys.argv[1]
WORK_ID = sys.argv[2]
user, pw = (l.strip() for l in sys.stdin.read().splitlines()[:2])

SUBGRAPH = f"/api/v1/kg/subgraphs?subject_kind=WORK&subject_id={WORK_ID}"
ENDPOINTS_GET = [
    "/api/v1/kg/import-runs",
    "/api/v1/kg/import-runs/bef74d0c-f60e-58d8-af98-d646a6401958",
    "/api/v1/kg/review-items",
    "/api/v1/kg/graph-versions",
    "/api/v1/kg/graph-versions/6ac06cc5-606a-585f-a91b-a455824f1159",
    SUBGRAPH,
    "/api/v1/kg/r3c/recommendations?limit=3",
    "/api/v1/kg/openapi",
    f"/api/v1/kg/papers/{WORK_ID}/evidence-spans",
]

def plain(path):
    r = urllib.request.Request(BASE + path)
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code

unauth = {ep: plain(ep) for ep in ENDPOINTS_GET}

cj = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
r = urllib.request.Request(BASE + "/login", data=urllib.parse.urlencode({"username": user, "password": pw}).encode(), method="POST")
opener.open(r, timeout=20)
del user, pw

auth, bodies = {}, {}
for ep in ENDPOINTS_GET:
    try:
        with opener.open(BASE + ep, timeout=20) as resp:
            auth[ep] = resp.status
            bodies[ep] = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        auth[ep] = e.code

# dynamic id drills
rec_items = bodies["/api/v1/kg/r3c/recommendations?limit=3"]["items"]
rid = rec_items[0]["recommendation_id"]
pid = rec_items[0]["profile_id"]
for ep in (f"/api/v1/kg/r3c/recommendations/{rid}", f"/api/v1/kg/data-profiles/{pid}"):
    try:
        with opener.open(BASE + ep, timeout=20) as resp:
            auth[ep] = resp.status
    except urllib.error.HTTPError as e:
        auth[ep] = e.code
    unauth[ep] = plain(ep)

openapi = bodies.get("/api/v1/kg/openapi", {})
span_body = bodies.get(f"/api/v1/kg/papers/{WORK_ID}/evidence-spans", {})
out = {
    "authenticated": auth,
    "unauthenticated": unauth,
    "authenticated_all_200": all(v == 200 for v in auth.values()),
    "unauthenticated_all_401": all(v == 401 for v in unauth.values()),
    "openapi_paths_count": len(openapi.get("paths", {})) if isinstance(openapi, dict) else None,
    "openapi_has_r3c_paths": any("r3c" in p for p in (openapi.get("paths", {}) or {})),
    "evidence_spans_count_for_probe_work": len(span_body.get("evidence_spans", span_body.get("spans", span_body.get("items", [])))) if isinstance(span_body, dict) else None,
    "healthz": plain("/healthz"),
    "readyz": plain("/readyz"),
}
json.dump(out, open(f"{REPORT_DIR}/23_API_OPENAPI_PROOF.json", "w"), ensure_ascii=False, indent=1)
print(json.dumps({k: out[k] for k in ("authenticated_all_200", "unauthenticated_all_401", "openapi_paths_count", "openapi_has_r3c_paths", "evidence_spans_count_for_probe_work", "healthz", "readyz")}))
