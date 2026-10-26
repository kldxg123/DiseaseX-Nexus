"""DB/API/UI reconciliation for the Track C blind run + final browser capture."""

import json
import urllib.parse
import urllib.request
import urllib.error
import http.cookiejar
from pathlib import Path

BASE = "http://127.0.0.1:18130"
RUN_C = "d3e6acef-6e8d-5596-86d1-fe07e2587284"
TRACK_C = "CHL:influenza:influenza_positive_detections:week"

creds = Path(r"F:\作业\aibola\secrets\dashboard_login.txt").read_text(encoding="utf-8").splitlines()
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
login_req = urllib.request.Request(
    BASE + "/login", data=urllib.parse.urlencode({"username": creds[0].strip(), "password": creds[1].strip()}).encode(), method="POST"
)
opener.open(login_req, timeout=15)


def get(path):
    with opener.open(BASE + path, timeout=60) as r:
        return json.loads(r.read().decode())


ev = get(f"/api/v1/kg/autotuning/evaluation?run_id={RUN_C}")
ov = get(f"/api/v1/kg/autotuning/overview?track_key={urllib.parse.quote(TRACK_C)}")

expected = {
    "wape": 0.7170266150977908,
    "agreement_pct": 28.297338490220923,
    "coverage": 0.8958333333333334,
}
checks = []
for k, v in expected.items():
    got = ev.get(k)
    checks.append({"field": k, "db": v, "api": got, "match": got is not None and abs(float(got) - v) < 1e-9})

recon = {
    "api_evaluation_matches_db": all(c["match"] for c in checks),
    "field_checks": checks,
    "registry_status_api": (ev.get("registry") or {}).get("status"),
    "classification_api": ev.get("classification"),
    "certification_verdict_api": (ev.get("certification") or {}).get("verdict"),
    "time_chain_api": ev.get("time_chain"),
    "paired_n_api": (ev.get("paired") or {}).get("paired_n"),
    "paired_match_api": (ev.get("paired") or {}).get("paired_sample_match"),
    "overview_registry": [r.get("status") for r in (ov.get("heldout_registry") or [])],
    "overview_is_fresh": (ov.get("evaluation") or {}).get("is_fresh"),
    "overview_beats_baseline": (ov.get("evaluation") or {}).get("beats_baseline"),
}
Path(__file__).with_name("reconciliation_trackc.json").write_text(
    json.dumps(recon, ensure_ascii=False, indent=1), encoding="utf-8"
)
print(json.dumps(recon, ensure_ascii=False, indent=1)[:1200])
