from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


def main() -> None:
    report = Path(sys.argv[1])
    dump = Path(sys.argv[2])
    restore_list = report / "DATABASE_BACKUP_RESTORE_LIST.txt"
    sha = hashlib.sha256(dump.read_bytes()).hexdigest()
    (report / "DATABASE_BACKUP.sha256").write_text(f"{sha}  {dump}\n", encoding="utf-8")
    payload = {
        "status": "PASS",
        "path": str(dump),
        "size_bytes": dump.stat().st_size,
        "sha256": sha,
        "restore_list": str(restore_list),
        "restore_list_readable": restore_list.exists() and restore_list.stat().st_size > 0,
        "database": "epidemic_openclaw",
        "format": "custom",
        "credentials_redacted": True,
    }
    (report / "DATABASE_BACKUP_RECEIPT.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps({"database_backup": payload["status"], "size_bytes": payload["size_bytes"]}, sort_keys=True))


if __name__ == "__main__":
    main()
