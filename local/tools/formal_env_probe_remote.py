from __future__ import annotations

import json
import os


def main() -> None:
    names = [
        "TRUSTED_INGEST_DATABASE_URL",
        "MIGRATION_DATABASE_URL",
        "POSTGRES_ADMIN_USER",
        "POSTGRES_ADMIN_PASSWORD",
        "DATABASE_URL",
    ]
    print(json.dumps({name: bool(os.environ.get(name)) for name in names}, sort_keys=True))


if __name__ == "__main__":
    main()
