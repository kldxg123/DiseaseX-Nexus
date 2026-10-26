from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from common.models import Base


def test_alembic_upgrade_head_creates_initial_schema(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert {
        "alembic_version",
        "forecast_outputs",
        "forecast_runs",
        "epidemic_events",
        "model_features",
        "model_experience_bank",
        "normalized_observations",
        "predictions",
        "human_feedback",
        "deepseek_web_search_queries",
        "deepseek_web_search_results",
        "deepseek_web_search_runs",
        "disease_country_information_records",
        "raw_documents",
        "raw_observations",
        "review_decisions",
        "source_registry",
        "sources",
    }.issubset(tables)
    assert "prediction_feedback" not in tables


def test_alembic_upgrade_head_matches_orm_metadata(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'migration-metadata.db'}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            version = connection.execute(text("select version_num from alembic_version")).scalar_one()
            context = MigrationContext.configure(connection)
            diffs = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert version == "0009_add_deepseek_web_search"
    assert diffs == []
