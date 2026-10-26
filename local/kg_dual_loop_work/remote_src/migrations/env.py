from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from epidemic_core.db.base import Base
from epidemic_core.db.models import tables as _tables  # noqa: F401

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata
MANAGED = set(target_metadata.tables)


def include_object(obj, name, type_, reflected, compare_to):
    if type_ == "table":
        key = f"{obj.schema}.{name}" if obj.schema else name
        return key in MANAGED
    table = getattr(obj, "table", None)
    return table is None or f"{table.schema}.{table.name}" in MANAGED


def url() -> str:
    value = os.environ.get("MIGRATION_DATABASE_URL")
    if not value:
        raise RuntimeError("MIGRATION_DATABASE_URL is required")
    return value


def configure(connection=None) -> None:
    context.configure(
        connection=connection,
        url=None if connection is not None else url(),
        target_metadata=target_metadata,
        include_schemas=True,
        include_object=include_object,
        compare_type=True,
        version_table_schema="epidemic",
        transaction_per_migration=True,
        render_as_batch=False,
    )


def run_migrations_offline() -> None:
    configure()
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = url()
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool, hide_parameters=True)
    with engine.connect() as connection:
        configure(connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
