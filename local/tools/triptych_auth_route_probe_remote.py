from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


PROJECT = Path(os.environ.get("PROJECT", "USER_HOME/autodl-tmp/epidemic-openclaw")).resolve()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_safe(path: Path, limit: int = 200000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except Exception:
        return ""


def http(path: str, headers: dict[str, str] | None = None) -> dict:
    url = "http://127.0.0.1:8020" + path
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "triptych-auth-route-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read(200000)
            return {
                "path": path,
                "status": getattr(resp, "status", 200),
                "content_type": resp.headers.get("Content-Type"),
                "bytes_sampled": len(body),
                "sha256": sha256(body),
                "json_keys": sorted(json.loads(body).keys())[:20] if body[:1] in (b"{", b"[") else None,
                "body_prefix": body[:300].decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(200000)
        return {
            "path": path,
            "status": exc.code,
            "content_type": exc.headers.get("Content-Type") if exc.headers else None,
            "bytes_sampled": len(body),
            "sha256": sha256(body),
            "body_prefix": body[:300].decode("utf-8", errors="replace"),
        }
    except Exception as exc:
        return {"path": path, "status": 0, "error_type": type(exc).__name__, "error": str(exc)[:200]}


def main() -> None:
    files = [
        PROJECT / "src" / "epidemic_core" / "dashboard" / "app.py",
        PROJECT / "src" / "epidemic_core" / "api" / "app.py",
        PROJECT / "src" / "epidemic_core" / "api" / "auth.py",
        PROJECT / "src" / "epidemic_core" / "api" / "settings.py",
    ]
    route_hits = []
    auth_terms = {}
    for path in files:
        text = read_safe(path)
        rel = str(path.relative_to(PROJECT)) if path.exists() else str(path)
        route_hits.extend(
            {"file": rel, "method": m.group(1).upper(), "route": m.group(2)}
            for m in re.finditer(r"@(?:app|router)\.(get|post|put|delete|patch)\([\"']([^\"']+)", text)
        )
        auth_terms[rel] = {
            "exists": path.exists(),
            "sha256": sha256(path.read_bytes()) if path.exists() else None,
            "mentions": sorted(set(re.findall(r"\b(?:AUTH|TOKEN|LOGIN|PASSWORD|SESSION|COOKIE|BASIC|BEARER|API_KEY|SECRET)[A-Z0-9_]*\b", text, re.I)))[:80],
        }
    env_files = []
    for base in [PROJECT, PROJECT / ".secrets", PROJECT / "config", PROJECT / "scripts"]:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and any(part in p.name.lower() for part in ["env", "auth", "token", "secret", "credential"]):
                try:
                    mode = oct(p.stat().st_mode & 0o777)
                    data = p.read_bytes()
                    keys = []
                    for line in data.decode("utf-8", errors="replace").splitlines():
                        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
                        if m:
                            keys.append(m.group(1))
                    env_files.append({"path": str(p.relative_to(PROJECT)), "mode": mode, "sha256": sha256(data), "keys": sorted(keys)})
                except Exception as exc:
                    env_files.append({"path": str(p), "error": type(exc).__name__})
    probes = [
        http("/healthz"),
        http("/readyz"),
        http("/api/v2/catalog/options"),
        http("/api/v2/history/metadata"),
        http("/api/v2/history/series"),
        http("/"),
        http("/three-disease"),
        http("/trusted-data"),
    ]
    git = subprocess.run(["git", "status", "--porcelain=v2", "-z", "--untracked-files=all"], cwd=str(PROJECT), capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(PROJECT), capture_output=True, text=True)
    out = {
        "project": str(PROJECT),
        "git_head": head.stdout.strip(),
        "dirty_count": len([x for x in git.stdout.split(b"\0") if x]),
        "routes": route_hits,
        "auth_source_mentions": auth_terms,
        "env_files_no_values": env_files,
        "http_probes": probes,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
