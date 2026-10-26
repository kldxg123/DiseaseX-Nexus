from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("p1_10r", ROOT / "tools" / "a100_p1_10r.py")
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

paths = [module.WORK_ROOT / item for item in module.CHANGE_FILES]
paths += [module.REPORT_ROOT / item for item in module.REPORT_FILES]
paths.append(ROOT / "启动A100疫情平台.ps1")

rules = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "cloud_token": re.compile(r"(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9]{24,})"),
    "credential_url": re.compile(r"(?:postgresql|https?)://[^\s:/]+:[^\s/@]+@", re.I),
    "tls_bypass": re.compile(
        r"curl\s+-k(?:\s|$)|wget\s+--no-check-certificate|GIT_SSL_NO_VERIFY\s*=\s*(?:1|true)|pip\s+[^\n]*--trusted-host",
        re.I,
    ),
    "proxy_literal": re.compile(r"(?:http|https|all)_proxy\s*=\s*['\"]?(?:https?|socks)://", re.I),
}

findings: list[dict[str, object]] = []
large: list[dict[str, object]] = []
missing: list[str] = []
for path in paths:
    if not path.is_file():
        missing.append(path.name)
        continue
    data = path.read_bytes()
    relative = path.relative_to(ROOT).as_posix()
    if len(data) > 2 * 1024 * 1024:
        large.append({"path": relative, "bytes": len(data)})
    if b"\0" in data:
        continue
    text = data.decode("utf-8", "replace")
    for name, pattern in rules.items():
        for match in pattern.finditer(text):
            findings.append(
                {
                    "path": relative,
                    "line": text.count("\n", 0, match.start()) + 1,
                    "rule": name,
                    "match_sha256": hashlib.sha256(match.group().encode()).hexdigest(),
                }
            )

json_errors = []
for path in module.REPORT_ROOT.glob("*.json"):
    try:
        json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        json_errors.append({"path": path.name, "error_type": type(exc).__name__})

result = {
    "pass": not findings and not large and not missing and not json_errors,
    "scanned_files": len(paths),
    "findings": findings,
    "large_files": large,
    "missing_files": missing,
    "json_errors": json_errors,
}
(ROOT / "remote_edit" / "p1_10r_gate" / "local_delivery_scan.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
)
print(json.dumps({"pass": result["pass"], "scanned_files": result["scanned_files"]}))
raise SystemExit(0 if result["pass"] else 1)
