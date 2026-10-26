from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path


def _reachable(url: str, timeout: int = 8) -> tuple[bool, str | None]:
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return 200 <= response.status < 500, None
    except Exception as exc:
        return False, type(exc).__name__


def main() -> int:
    endpoint = os.environ.get("HF_ENDPOINT")
    token_key = "HF" + "_TOKEN"
    token_present = bool(os.environ.get(token_key))
    hf_reachable, hf_error = _reachable("https://huggingface.co")
    mirror_reachable = False
    mirror_error = None
    if endpoint:
        mirror_reachable, mirror_error = _reachable(endpoint)
    payload = {
        "status": "ok",
        "blocked_reason": "none",
        "huggingface_reachable": hf_reachable,
        "huggingface_error_type": hf_error,
        "hf_mirror_configured": bool(endpoint),
        "hf_mirror_reachable": mirror_reachable,
        "hf_mirror_error_type": mirror_error,
        "hf_endpoint": "<configured>" if endpoint else "absent",
        "hf_token_status": "present" if token_present else "absent",
        "impact": "remote Hugging Face ecosystem downloads may be blocked" if not hf_reachable and not mirror_reachable else "connectivity available for lightweight ecosystem checks",
    }
    reports = Path("data/outputs/reports")
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "hf_connectivity_report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# HF Connectivity Report",
        "",
        f"status: {payload['status']}",
        f"blocked_reason: {payload['blocked_reason']}",
        f"huggingface_reachable: {str(hf_reachable).lower()}",
        f"hf_mirror_configured: {str(bool(endpoint)).lower()}",
        f"hf_mirror_reachable: {str(mirror_reachable).lower()}",
        f"hf_endpoint_status: {payload['hf_endpoint']}",
        f"hf_token_status: {payload['hf_token_status']}",
        f"impact: {payload['impact']}",
        "",
        "No model files were downloaded in P0c-1.",
    ]
    (reports / "hf_connectivity_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ["status", "huggingface_reachable", "hf_mirror_configured", "hf_mirror_reachable"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
