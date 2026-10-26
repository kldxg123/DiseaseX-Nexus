from __future__ import annotations

import pytest

from crawler_agent.connectors import CdcNoroStatConnector

LIVE_URL = "https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html"


def test_cdc_norostat_live_requires_terms_review() -> None:
    with pytest.raises(PermissionError, match="terms review has not been completed"):
        CdcNoroStatConnector().fetch_live(
            url=LIVE_URL,
            allow_network=True,
            manual_reviewed=True,
            robots_allowed=True,
        )


def test_cdc_norostat_live_requires_manual_review() -> None:
    with pytest.raises(PermissionError, match="manual review has not been completed"):
        CdcNoroStatConnector().fetch_live(
            url=LIVE_URL,
            allow_network=True,
            terms_reviewed=True,
            robots_allowed=True,
        )


def test_cdc_norostat_live_requires_robots_allowed() -> None:
    with pytest.raises(PermissionError, match="robots access check has not passed"):
        CdcNoroStatConnector().fetch_live(
            url=LIVE_URL,
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            robots_allowed=False,
        )


def test_cdc_norostat_live_rejects_request_count_over_policy_limit() -> None:
    with pytest.raises(PermissionError, match="request count exceeds policy limit"):
        CdcNoroStatConnector().fetch_live(
            url=LIVE_URL,
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            robots_allowed=True,
            request_count=4,
        )


def test_cdc_norostat_live_rejects_domain_outside_policy() -> None:
    with pytest.raises(PermissionError, match="requested URL domain is not allowed by policy"):
        CdcNoroStatConnector().fetch_live(
            url="https://outside.invalid/norostat.html",
            allow_network=True,
            terms_reviewed=True,
            manual_reviewed=True,
            robots_allowed=True,
        )
