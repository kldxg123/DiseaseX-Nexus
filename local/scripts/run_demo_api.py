from __future__ import annotations

import os

import uvicorn


def main() -> None:
    os.environ.setdefault("DATABASE_URL", "sqlite:///data/dashboard_demo.sqlite")
    uvicorn.run("apps.api.main:app", host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
