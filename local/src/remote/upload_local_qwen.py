from __future__ import annotations

import argparse
import json

from src.models.qwen25_model_uploader import upload_local_qwen


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload selected local Qwen2.5-7B-Instruct to remote.")
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    report = upload_local_qwen(args.root)
    print(json.dumps({"upload_status": report["upload_status"], "blocked_reason": report["blocked_reason"]}, ensure_ascii=False))
    return 0 if report["upload_status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
