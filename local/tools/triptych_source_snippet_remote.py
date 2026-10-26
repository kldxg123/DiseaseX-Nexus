from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT = Path(os.environ.get("PROJECT", "USER_HOME/autodl-tmp/epidemic-openclaw")).resolve()


def snippet(path: Path, needles: list[str], context: int = 35) -> list[dict]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = []
    for i, line in enumerate(lines, 1):
        if any(n in line for n in needles):
            start = max(1, i - context)
            end = min(len(lines), i + context)
            block = []
            for no in range(start, end + 1):
                text = lines[no - 1]
                if any(secret_word in text.lower() for secret_word in ["password", "token", "secret", "cookie"]):
                    # Keep code structure but never literal right hand values.
                    text = text.split("=", 1)[0] + "= <redacted-or-expression>" if "=" in text else text
                block.append(f"{no}: {text}")
            hits.append({"needle_line": i, "block": block})
    return hits[:8]


def main() -> None:
    targets = {
        "dashboard_app": PROJECT / "src" / "epidemic_core" / "dashboard" / "app.py",
        "api_auth": PROJECT / "src" / "epidemic_core" / "api" / "auth.py",
    }
    out = {}
    for key, path in targets.items():
        out[key] = snippet(
            path,
            [
                "def login",
                "AuthStore",
                "SessionStore",
                "AUTHENTICATION_REQUIRED",
                "expected",
                "verify",
                "Depends",
                "require",
                "readyz",
                "required_revision",
            ],
        )
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
