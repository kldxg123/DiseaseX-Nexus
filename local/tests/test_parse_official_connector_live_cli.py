from __future__ import annotations

import json

import pytest

from crawler_agent.connectors import CdcNoroStatConnector, ConnectorDocument
from scripts import parse_official_connector

LIVE_URL = "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"


def test_parse_official_connector_fixture_alias_still_works(capsys) -> None:
    result = parse_official_connector.main(
        [
            "--connector",
            "cdc_norostat",
            "--fixture",
            "tests/fixtures/cdc_norostat_live_mock.html",
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["connector"] == "cdc_norostat"
    assert payload["mode"] == "fixture"
    assert payload["parsed"] == 3
    _assert_safe(captured.out)


def test_parse_official_connector_live_denies_missing_guard_conditions(capsys) -> None:
    result = parse_official_connector.main(
        [
            "--connector",
            "cdc_norostat",
            "--allow-network",
            "--live-url",
            LIVE_URL,
            "--robots-allowed",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 2
    assert payload["guard_allowed"] is False
    assert payload["parsed"] == 0
    _assert_safe(captured.out)


def test_parse_official_connector_live_success_with_monkeypatch(monkeypatch, capsys) -> None:
    def fake_fetch_live(self, **kwargs):
        assert kwargs["allow_network"] is True
        assert kwargs["url"] == LIVE_URL
        return [
            ConnectorDocument(
                connector_name="cdc_norostat",
                source_name="CDC NoroSTAT",
                url=LIVE_URL,
                title="CDC NoroSTAT norovirus outbreaks - Fixture - 2026-W01",
                body_text="Fixture live connector summary.",
                content_type="text/html",
                metadata={"parse_mode": "html_table", "live_fetch": True, "source_key": "cdc_norostat"},
            )
        ]

    monkeypatch.setattr(CdcNoroStatConnector, "fetch_live", fake_fetch_live)

    result = parse_official_connector.main(
        [
            "--connector",
            "cdc_norostat",
            "--allow-network",
            "--live-url",
            LIVE_URL,
            "--terms-reviewed",
            "--manual-reviewed",
            "--robots-allowed",
            "--max-requests",
            "1",
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert result == 0
    assert payload["mode"] == "live"
    assert payload["parsed"] == 1
    assert payload["guard_allowed"] is True
    _assert_safe(captured.out)


def test_parse_official_connector_live_without_url_keeps_no_fetch_behavior(capsys) -> None:
    with pytest.raises(SystemExit, match="Live fetch not implemented/enabled in this MVP."):
        parse_official_connector.main(
            [
                "--connector",
                "cdc_norostat",
                "--base-url",
                "https://example.invalid/cdc-norostat-placeholder",
                "--allow-network",
                "--terms-reviewed",
                "--manual-reviewed",
                "--robots-allowed",
            ]
        )
    _assert_safe(capsys.readouterr().out)


def _assert_safe(output: str) -> None:
    rendered = output.lower()
    for marker in ("<html", "raw_text", "metadata_json", "token", "secret", "password", "private key"):
        assert marker not in rendered
