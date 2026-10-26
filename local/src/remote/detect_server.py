from __future__ import annotations

import argparse
import json

from src.remote.sanitizer import sanitize_text
from src.remote.server_config import discover_server_configs


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect remote server configuration without exposing secrets.")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    try:
        configs = discover_server_configs(args.root)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "found": False,
                    "error": sanitize_text(f"{type(exc).__name__}: {exc}"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    if not configs:
        print(json.dumps({"found": False, "message": "No server config found."}, ensure_ascii=False))
        return 1
    print(json.dumps({"found": True, "configs": [cfg.sanitize() for cfg in configs]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
