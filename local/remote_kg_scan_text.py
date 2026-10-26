from __future__ import annotations

from pathlib import Path

ROOT = Path("USER_HOME/projects/openclaw")
PATHS = [
    ROOT / "src/epidemic_core/forecast_model_kg/dashboard_app.py",
    ROOT / "src/epidemic_core/dashboard/static/forecast-model-kg.js",
    ROOT / "src/epidemic_core/dashboard/app.py",
]


def main() -> None:
    for path in PATHS:
        text = path.read_text(encoding="utf-8", errors="replace")
        print(path.name, "question_marks", text.count("?"), "replacement_chars", text.count("\ufffd"))
        for lineno, line in enumerate(text.splitlines(), 1):
            if "????" in line or "\ufffd" in line:
                print("badline", path.name, lineno, line[:220])


if __name__ == "__main__":
    main()
