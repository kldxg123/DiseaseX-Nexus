from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_deepseek_migration_upgrade_head_creates_tables_and_query_indexes(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'deepseek-migration.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        indexes = {item["name"] for item in inspector.get_indexes("disease_country_information_records")}
        with engine.connect() as connection:
            version = connection.execute(text("select version_num from alembic_version")).scalar_one()
    finally:
        engine.dispose()

    assert version == "0009_add_deepseek_web_search"
    assert {
        "deepseek_web_search_runs",
        "deepseek_web_search_queries",
        "deepseek_web_search_results",
        "disease_country_information_records",
    }.issubset(tables)
    assert "ix_disease_country_records_disease_country" in indexes
