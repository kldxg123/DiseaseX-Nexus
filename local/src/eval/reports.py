from __future__ import annotations

from pathlib import Path


def write_markdown_report(path: str | Path, title: str, lines: list[str]) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# " + title + "\n\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return p
