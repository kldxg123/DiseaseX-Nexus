from __future__ import annotations

import re

from deepseek_web_search_agent.cli import main


SECRET_LIKE = re.compile(r"\bsk-[A-Za-z0-9][A-Za-z0-9_-]{18,}\b")


def test_deepseek_generated_reports_do_not_contain_secret_like_values_or_full_html(tmp_path) -> None:
    database_url = f"sqlite:///{(tmp_path / 'deepseek-safe.sqlite').as_posix()}"
    output_dir = tmp_path / "reports"

    assert (
        main(
            [
                "run-mock",
                "--disease",
                "measles",
                "--country",
                "United States",
                "--max-search-queries",
                "1",
                "--max-results-per-query",
                "2",
                "--max-pages-to-read",
                "0",
                "--max-total-http-requests",
                "2",
                "--output-dir",
                str(output_dir),
                "--database-url",
                database_url,
            ]
        )
        == 0
    )

    rendered = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in output_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in {".json", ".md"}
    )
    lowered = rendered.lower()
    assert not SECRET_LIKE.search(rendered)
    assert "<html" not in lowered
    assert "authorization" not in lowered
