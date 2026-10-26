from __future__ import annotations

import json

import pytest

from scripts import crawl_url, discover_sources, parse_official_connector


POLICY_PATH = "configs/source_access_policy.example.json"


def test_crawl_url_allow_network_still_does_not_live_fetch(tmp_path, capsys) -> None:
    database_path = tmp_path / "should-not-exist.db"

    with pytest.raises(SystemExit, match="Live fetch not implemented/enabled in this MVP."):
        crawl_url.main(
            [
                "--database-url",
                f"sqlite:///{database_path}",
                "--source-id",
                "1",
                "--source-key",
                "cdc_norostat",
                "--url",
                "https://example.invalid/test",
                "--source-policy",
                POLICY_PATH,
                "--allow-network",
                "--terms-reviewed",
                "--manual-reviewed",
                "--robots-allowed",
            ]
        )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["decision"]["allowed"] is True
    assert not database_path.exists()


def test_discover_sources_allow_network_is_guarded_and_denied(capsys) -> None:
    with pytest.raises(SystemExit, match="Live GDELT discovery denied by source access policy."):
        discover_sources.main(
            [
                "--country",
                "U.S.",
                "--disease",
                "norovirus",
                "--source-policy",
                POLICY_PATH,
                "--allow-network",
                "--terms-reviewed",
                "--manual-reviewed",
                "--robots-allowed",
            ]
        )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["decision"]["allowed"] is False
    assert payload["decision"]["reason"] == "requested URL domain is not allowed by policy"


def test_parse_official_connector_allow_network_still_does_not_live_fetch(capsys) -> None:
    with pytest.raises(SystemExit, match="Live fetch not implemented/enabled in this MVP."):
        parse_official_connector.main(
            [
                "--connector",
                "cdc_norostat",
                "--base-url",
                "https://example.invalid/cdc-norostat-placeholder",
                "--source-policy",
                POLICY_PATH,
                "--allow-network",
                "--terms-reviewed",
                "--manual-reviewed",
                "--robots-allowed",
            ]
        )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["decision"]["allowed"] is True


def test_live_flag_cli_outputs_do_not_leak_sensitive_markers(capsys) -> None:
    with pytest.raises(SystemExit):
        crawl_url.main(
            [
                "--source-id",
                "1",
                "--source-key",
                "missing_source",
                "--url",
                "https://example.invalid/test?token=hidden",
                "--source-policy",
                POLICY_PATH,
                "--allow-network",
            ]
        )
    captured = capsys.readouterr()
    dumped = captured.out.lower()

    assert "hidden" not in dumped
    assert "token" not in dumped
    assert "password" not in dumped
    assert "secret" not in dumped
