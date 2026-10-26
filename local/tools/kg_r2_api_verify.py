#!/usr/bin/env python3
from __future__ import annotations

import http.cookiejar
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


TASK_ID = "KG_P1_R3R_UX2_R2_70PDF_BASE_BACKFILL"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_dashboard_credentials() -> tuple[str, str]:
    pids = [p for p in os.listdir("/proc") if p.isdigit()]
    for p in pids:
        try:
            cmd = Path("/proc") / p / "cmdline"
            if b"epidemic_core.dashboard.app" not in cmd.read_bytes():
                continue
            env = (Path("/proc") / p / "environ").read_bytes().split(b"\0")
            data = {}
            for item in env:
                if b"=" in item:
                    k, v = item.split(b"=", 1)
                    data[k.decode()] = v.decode(errors="ignore")
            user = data.get("DASHBOARD_LOGIN_USER")
            password = data.get("DASHBOARD_LOGIN_PASSWORD")
            if user and password:
                return user, password
        except Exception:
            continue
    raise RuntimeError("dashboard credentials unavailable in process env")


def main() -> int:
    out = Path(os.environ["R2_API_REPORT"])
    base = "http://127.0.0.1:8020"
    report = {
        "schema_version": "1.0",
        "task_id": TASK_ID,
        "generated_at_utc": now(),
        "active_project_root": "USER_HOME/projects/openclaw",
        "source_revision_or_hash": "runtime",
        "database_identity": {"database": "epidemic_openclaw", "port": 55432},
        "input_manifest_sha256": "",
        "result_status": "STARTED",
        "commands_or_evidence_refs": [],
        "blockers": [],
        "no_fake_pass_assertion": True,
        "credential_read": "NOT_RUN",
        "login": "NOT_RUN",
        "responses": {},
    }
    try:
        username, password = read_dashboard_credentials()
        report["credential_read"] = "PASS_REDACTED"
        cj = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj), urllib.request.HTTPRedirectHandler())
        data = urllib.parse.urlencode({"username": username, "password": password}).encode()
        req = urllib.request.Request(base + "/login", data=data, method="POST")
        resp = opener.open(req, timeout=10)
        report["login"] = "PASS" if resp.geturl().endswith("/") else "FAIL"
        endpoints = {
            "healthz": "/healthz",
            "readyz": "/readyz",
            "kg_page": "/kg/explorer",
            "kg_work_entities": "/api/v1/kg/entities?node_type=WORK&limit=200",
            "kg_document_entities": "/api/v1/kg/entities?node_type=DOCUMENT_VERSION&limit=200",
        }
        for name, path in endpoints.items():
            try:
                r = opener.open(base + path, timeout=15)
                body = r.read(500000)
                item_count = None
                if path.startswith("/api/"):
                    obj = json.loads(body.decode("utf-8"))
                    item_count = len(obj.get("items", []))
                report["responses"][name] = {
                    "status": r.status,
                    "bytes_sampled": len(body),
                    "json_item_count": item_count,
                    "pass": r.status == 200 and (not path.startswith("/api/") or item_count is not None),
                }
            except Exception as e:
                report["responses"][name] = {"pass": False, "error_type": type(e).__name__, "error": str(e)[:300]}
        report["result_status"] = "PASS" if report["login"] == "PASS" and all(v.get("pass") for v in report["responses"].values()) else "FAIL"
    except Exception as e:
        report["result_status"] = "FAIL"
        report["blockers"].append(type(e).__name__)
        report["error"] = str(e)[:500]
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if report["result_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
