from __future__ import annotations

from pathlib import Path

MIGRATION = Path("migrations/versions/0027_p1_14c_v2_preview_path_grants.py").read_text()
FETCHER = Path("src/epidemic_core/ebola/fetcher.py").read_text()


def test_p1_14c_migration_grants_only_exact_gate_function_to_app() -> None:
    assert "GRANT EXECUTE ON FUNCTION epidemic_p1_12.review_ready_v2_violations(uuid) TO epidemic_app" in MIGRATION
    assert "REVOKE EXECUTE ON FUNCTION epidemic_p1_12.review_ready_v2_violations(uuid) FROM epidemic_app" in MIGRATION
    assert "GRANT ALL" not in MIGRATION
    assert "ON ALL TABLES" not in MIGRATION
    assert "ON ALL FUNCTIONS" not in MIGRATION
    assert "ALTER DEFAULT PRIVILEGES" not in MIGRATION
    assert "PUBLIC" not in MIGRATION
    assert "INSERT INTO" not in MIGRATION
    assert "UPDATE " not in MIGRATION
    assert "DELETE " not in MIGRATION


def test_fetcher_allows_official_xlsx_without_disabling_tls_or_magic_checks() -> None:
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert mime in FETCHER
    assert "ssl.create_default_context()" in FETCHER
    assert "GIT_SSL_NO_VERIFY" not in FETCHER
    assert "PK\\x03\\x04" in FETCHER
