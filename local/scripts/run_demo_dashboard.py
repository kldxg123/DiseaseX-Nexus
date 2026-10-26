from __future__ import annotations

import os
import sys

from streamlit.web import cli as streamlit_cli


def main() -> int:
    os.environ.setdefault("API_BASE_URL", "http://127.0.0.1:8000")
    sys.argv = [
        "streamlit",
        "run",
        "apps/dashboard/streamlit_app.py",
        "--server.address",
        "127.0.0.1",
        "--server.port",
        "8501",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]
    return streamlit_cli.main()


if __name__ == "__main__":
    raise SystemExit(main())
