from __future__ import annotations

import json

from scripts.check_deepseek_secret_hygiene import run_audit


def test_secret_hygiene_passes_with_placeholders(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "\n".join([".env", "*.env", ".env.local", ".env.*.local", "!.env.example"]),
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text(
        "\n".join(
            [
                "DEEPSEEK_API_KEY=",
                "BING_SEARCH_API_KEY=",
                "BRAVE_SEARCH_API_KEY=",
                "SERPAPI_API_KEY=",
                "TAVILY_API_KEY=",
                "EXA_API_KEY=",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fixture.txt").write_text("placeholder only", encoding="utf-8")

    summary = run_audit("reports/deepseek_web_search")

    assert summary["status"] == "passed"
    assert summary["high_count"] == 0
    report = json.loads((tmp_path / "reports/deepseek_web_search/SECRET_HYGIENE_AUDIT.json").read_text())
    assert report["status"] == "passed"


def test_secret_hygiene_detects_secret_like_public_file_without_echoing_value(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".gitignore").write_text(
        "\n".join([".env", "*.env", ".env.local", ".env.*.local", "!.env.example"]),
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    fake_prefix = "sk-"
    fake_suffix = "abcdefghijklmnopqrstuvwxyz123456"
    fake_value = fake_prefix + fake_suffix
    (tmp_path / "README.md").write_text(f"bad example {fake_value}", encoding="utf-8")

    summary = run_audit("reports/deepseek_web_search")
    rendered = json.dumps(summary, ensure_ascii=False)

    assert summary["status"] == "failed"
    assert summary["high_count"] == 1
    assert fake_value not in rendered
