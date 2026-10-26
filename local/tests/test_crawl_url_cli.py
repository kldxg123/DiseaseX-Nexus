from __future__ import annotations

from scripts import crawl_url


def test_crawl_url_cli_dry_run_does_not_create_database(tmp_path, capsys) -> None:
    database_path = tmp_path / "crawler-cli.db"

    result = crawl_url.main(
        [
            "--database-url",
            f"sqlite:///{database_path}",
            "--source-id",
            "1",
            "--url",
            "https://example.invalid/page",
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert not database_path.exists()
    assert "dry_run" in captured.out


def test_crawl_url_cli_refuses_network_without_explicit_flag(tmp_path) -> None:
    database_path = tmp_path / "crawler-cli.db"

    try:
        crawl_url.main(
            [
                "--database-url",
                f"sqlite:///{database_path}",
                "--source-id",
                "1",
                "--url",
                "https://example.invalid/page",
            ]
        )
    except SystemExit as exc:
        assert str(exc) == "Refusing to fetch without --allow-network."
    else:
        raise AssertionError("Expected SystemExit")
