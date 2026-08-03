from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest_asyncio
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from boundary.persistence.database import DatabaseSettings
from boundary.persistence.tables import (
    analyses,
    campaigns,
    evidence_records,
    evidence_sets,
    fault_activations,
    idempotency_records,
    run_capabilities,
    runs,
    tool_calls,
)


REPOSITORY_ROOT = Path(__file__).parents[3]
ALEMBIC_CONFIG_PATH = REPOSITORY_ROOT / "migrations" / "alembic.ini"
APPLICATION_TABLES = {
    "campaigns",
    "runs",
    "idempotency_records",
    "evidence_records",
    "run_capabilities",
    "tool_calls",
    "fault_activations",
    "evidence_sets",
    "analyses",
}


@dataclass(frozen=True, slots=True)
class MigrationFacts:
    tables_before_upgrade: frozenset[str]
    tables_after_upgrade: frozenset[str]
    database_revision: str
    head_revision: str


async def _application_tables(engine: AsyncEngine) -> frozenset[str]:
    statement = sa.text(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name <> 'alembic_version'
        """
    )
    async with engine.connect() as connection:
        rows = await connection.execute(statement)
    return frozenset(rows.scalars())


@pytest_asyncio.fixture(
    scope="session",
    loop_scope="session",
)
async def migration_facts() -> MigrationFacts:
    settings = DatabaseSettings.from_environment()
    engine = create_async_engine(settings.url, pool_pre_ping=True)
    alembic_config = Config(str(ALEMBIC_CONFIG_PATH))

    await asyncio.to_thread(command.downgrade, alembic_config, "base")
    tables_before_upgrade = await _application_tables(engine)

    await asyncio.to_thread(command.upgrade, alembic_config, "head")
    await asyncio.to_thread(command.check, alembic_config)
    tables_after_upgrade = await _application_tables(engine)

    async with engine.connect() as connection:
        database_revision = (
            await connection.scalar(
                sa.text("SELECT version_num FROM alembic_version")
            )
        )
    script = ScriptDirectory.from_config(alembic_config)
    head_revision = script.get_current_head()

    assert database_revision is not None
    assert head_revision is not None
    await engine.dispose()

    return MigrationFacts(
        tables_before_upgrade=tables_before_upgrade,
        tables_after_upgrade=tables_after_upgrade,
        database_revision=database_revision,
        head_revision=head_revision,
    )


@pytest_asyncio.fixture(
    scope="session",
    loop_scope="session",
)
async def database_engine(
    migration_facts: MigrationFacts,
) -> AsyncEngine:
    del migration_facts
    settings = DatabaseSettings.from_environment()
    engine = create_async_engine(settings.url, pool_pre_ping=True)
    yield engine
    await engine.dispose()


async def _clear_application_rows(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(analyses.delete())
        await connection.execute(evidence_sets.delete())
        await connection.execute(fault_activations.delete())
        await connection.execute(tool_calls.delete())
        await connection.execute(run_capabilities.delete())
        await connection.execute(evidence_records.delete())
        await connection.execute(idempotency_records.delete())
        await connection.execute(runs.delete())
        await connection.execute(campaigns.delete())


@pytest_asyncio.fixture(
    autouse=True,
    loop_scope="session",
)
async def clean_application_rows(
    database_engine: AsyncEngine,
) -> None:
    await _clear_application_rows(database_engine)
    yield
    await _clear_application_rows(database_engine)
