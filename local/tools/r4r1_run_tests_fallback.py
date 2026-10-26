#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    test_path = Path("USER_HOME/projects/openclaw/tests/test_r4r1_shadow_and_evidence.py")
    spec = importlib.util.spec_from_file_location("r4r1_tests", test_path)
    if spec is None or spec.loader is None:
        print(json.dumps({"status": "FAIL", "reason": "SPEC_LOAD_FAILED"}, ensure_ascii=False))
        return 1
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    executed = []
    failed = []
    for name in sorted(n for n in dir(mod) if n.startswith("test_")):
        fn = getattr(mod, name)
        try:
            fn()
            executed.append(name)
        except Exception as exc:  # noqa: BLE001 - test runner records failure
            failed.append({"test": name, "error_type": type(exc).__name__, "message": str(exc)})
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "pytest_available": False,
        "fallback_runner": "direct_test_function_invocation",
        "executed": executed,
        "failed": failed,
        "status": "PASS" if not failed else "FAIL",
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
