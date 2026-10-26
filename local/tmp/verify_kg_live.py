"""Authenticated live verification of the forecast model KG on the A100 dashboard.

Reads the dashboard login from the local secrets file (never printed), pipes it
to a remote checker via stdin, and prints only non-secret results.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import paramiko

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import migrate_epidemic_to_a100 as migration  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LOGIN_FILE = ROOT / "secrets" / "dashboard_login.txt"

REMOTE_CHECKER = r'''
import http.cookiejar, json, sys, urllib.parse, urllib.request

creds = {}
for line in sys.stdin.read().splitlines():
    line = line.strip()
    if not line or "=" not in line:
        continue
    k, v = line.split("=", 1)
    creds[k.strip().lower()] = v.strip()
user = creds.get("username") or creds.get("user") or "analyst"
pw = creds.get("password") or creds.get("pass") or ""
if not pw:
    raise SystemExit("NO_PASSWORD_ON_STDIN")

BASE = "http://127.0.0.1:8020"
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

def open_url(path, method="GET", data=None):
    encoded = urllib.parse.urlencode(data or {}).encode() if data is not None else None
    req = urllib.request.Request(BASE + path, data=encoded, method=method)
    if encoded is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with opener.open(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")

result = {}
code, body = open_url("/login", "POST", {"username": user, "password": pw})
result["login"] = {"status": code, "cookies": len(jar)}
checks = [
    "/forecast-model-knowledge-graph",
    "/static/forecast-model-kg.css",
    "/api/v1/forecast-model-knowledge-graph",
    "/api/v1/forecast-model-knowledge-graph?full=true",
    "/api/v1/forecast-model-knowledge-graph/Legionellosis",
    "/api/v1/forecast-model-guidance?disease=COVID-19&country_or_region=FRA&target=cases_new&horizon=4",
    "/api/v1/forecast-model-guidance?disease=Influenza&country_or_region=PER&target=cases_new&horizon=4",
    "/api/v1/forecast-model-guidance?disease=Dengue&country_or_region=BRA&target=cases_new&horizon=4",
    "/api/v1/forecast-model-guidance?disease=Ebola&country_or_region=COD&target=cases_new&horizon=4",
    "/api/v1/forecast-model-guidance?disease=Puumala&country_or_region=FIN&target=cases_new&horizon=4",
    "/api/v1/forecast-model-guidance?disease=Legionellosis&country_or_region=AUT&target=cases_new&horizon=4",
    "/api/v1/forecast-model-guidance?disease=Nipah&country_or_region=BGD&target=cases_new&horizon=4",
]
for path in checks:
    code, body = open_url(path)
    item = {"status": code, "bytes": len(body)}
    if path.startswith("/api/") and code == 200:
        parsed = json.loads(body)
        if "guidance" in path:
            item["resolved"] = parsed["resolved_disease"]["id"]
            item["models"] = len(parsed["recommended_model_families"])
            item["warnings"] = len(parsed["warnings"])
            item["side_effects"] = parsed["side_effects"]
        elif "full=true" in path:
            item["nodes"] = len(parsed["nodes"]); item["rules"] = len(parsed["rules"])
            item["sha256"] = parsed["metadata"]["content_sha256"]
        elif "knowledge-graph/" in path:
            item["nodes"] = len(parsed["nodes"]); item["rules"] = len(parsed["rules"])
        else:
            item["keys"] = sorted(parsed.keys())[:8]
    result[path] = item
for path in ("/healthz", "/readyz", "/", "/predictions"):
    code, _ = open_url(path)
    result[path] = {"status": code}
print(json.dumps(result, ensure_ascii=False, indent=1, sort_keys=True))
'''


def parse_login(text: str) -> dict[str, str]:
    mapping = {"用户名": "username", "密码": "password", "username": 'REDACTED',
               "user": "username", "password": 'REDACTED', "pass": "password"}
    creds: dict[str, str] = {}
    positional: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        matched = False
        for sep in ("：", ":", "="):
            if sep in line:
                key, value = line.split(sep, 1)
                key = key.strip().lower()
                if key in mapping:
                    creds[mapping[key]] = value.strip()
                    matched = True
                break
        if not matched and not creds:
            positional.append(line)
    if "password" not in creds and len(positional) >= 2:
        creds.setdefault("username", positional[0])
        creds["password"] = positional[1]
    return creds


def main() -> int:
    login = parse_login(LOGIN_FILE.read_text(encoding="utf-8-sig", errors="replace"))
    if "password" not in login:
        print("LOCAL_LOGIN_FILE_UNPARSEABLE")
        return 2
    stdin_payload = f"username={login.get('username', 'analyst')}\npassword={login['password']}\n"

    creds = migration.credentials(migration.TARGET_INFO)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(creds.host, port=creds.port, username=creds.user, password=creds.password, timeout=30)
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tmp:
            tmp.write(REMOTE_CHECKER)
            tmp_path = tmp.name
        sftp = client.open_sftp()
        sftp.put(tmp_path, "/tmp/kg_live_check.py")
        sftp.close()
        cmd = "$HOME/autodl-tmp/conda/envs/epidemic-core/bin/python /tmp/kg_live_check.py; rc=$?; rm -f /tmp/kg_live_check.py; exit $rc"
        stdin, stdout, stderr = client.exec_command(cmd, timeout=180)
        stdin.write(stdin_payload)
        stdin.close()
        out = stdout.read().decode()
        err = stderr.read().decode()
        print(out)
        if err.strip():
            print("STDERR:", err[-500:], file=sys.stderr)
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
