from __future__ import annotations

import logging

from common.logging import MASK, get_logger, mask_sensitive, sanitize_log_record


def test_mask_sensitive_masks_value() -> None:
    assert mask_sensitive("hidden") == MASK
    assert mask_sensitive(None) is None


def test_sanitize_log_record_masks_nested_values() -> None:
    payload = {
        "public": "visible",
        "api_token": "hidden",
        "nested": {
            "database_password": 'REDACTED',
            "private_key": 'REDACTED',
            "safe": "visible",
        },
    }

    masked = sanitize_log_record(payload)

    assert masked["public"] == "visible"
    assert masked["api_token"] == MASK
    assert masked["nested"]["database_password"] == MASK
    assert masked["nested"]["private_key"] == MASK
    assert masked["nested"]["safe"] == "visible"
    assert "hidden" not in str(masked)


def test_get_logger_returns_logger() -> None:
    logger = get_logger("infectious_intel.test")

    assert isinstance(logger, logging.Logger)
    assert logger.handlers
