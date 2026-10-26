from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = PROJECT_ROOT / "packages"
for candidate in (PROJECT_ROOT, PACKAGES_DIR):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from crawler_agent.access_policy import load_access_policies, validate_access_policy
from crawler_agent.live_fetch_guard import LiveFetchGuard


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate source access policies locally.")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--source-key", default=None)
    parser.add_argument("--url", default=None)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--terms-reviewed", action="store_true")
    parser.add_argument("--manual-reviewed", action="store_true")
    parser.add_argument("--robots-allowed", action="store_true")
    parser.add_argument("--request-count", type=int, default=1)
    args = parser.parse_args(argv)

    errors: list[str] = []
    decision_payload = None
    try:
        policies = load_access_policies(args.policy)
        for policy in policies.values():
            errors.extend(validate_access_policy(policy))
    except Exception as exc:
        result = {
            "status": "failed",
            "policies_checked": 0,
            "errors": [_safe_text(str(exc))],
            "decision": None,
        }
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 1

    if args.source_key:
        guard = LiveFetchGuard(policies)
        decision = guard.check(
            args.source_key,
            allow_network=args.allow_network,
            requested_url=args.url,
            robots_allowed=True if args.robots_allowed else None,
            terms_reviewed=args.terms_reviewed,
            manual_reviewed=args.manual_reviewed,
            request_count=args.request_count,
        )
        decision_payload = _decision_payload(decision)

    result = {
        "status": "ok" if not errors else "failed",
        "policies_checked": len(policies),
        "errors": [_safe_text(error) for error in errors],
        "decision": decision_payload,
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if not errors else 1


def _decision_payload(decision) -> dict:
    return {
        "allowed": decision.allowed,
        "reason": _safe_text(decision.reason),
        "warnings": [_safe_text(warning) for warning in decision.warnings],
        "policy_summary": decision.policy_summary,
    }


def _safe_text(value: str) -> str:
    text = str(value)
    for marker in ("secret", "token", "password", "private key", "ssh_key_path", "credential"):
        if marker in text.lower():
            return "[redacted]"
    return text


if __name__ == "__main__":
    raise SystemExit(main())
