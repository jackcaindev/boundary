"""Validated PostgreSQL engine configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


class DatabaseConfigurationError(ValueError):
    """The database configuration is absent or unsupported."""


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """The explicit database configuration accepted by Boundary."""

    url: str

    def __post_init__(self) -> None:
        try:
            parsed = make_url(self.url)
        except Exception as error:
            raise DatabaseConfigurationError(
                "DATABASE_URL must be a valid SQLAlchemy URL"
            ) from error

        if parsed.drivername != "postgresql+asyncpg":
            raise DatabaseConfigurationError(
                "DATABASE_URL must use postgresql+asyncpg"
            )
        if not parsed.username:
            raise DatabaseConfigurationError(
                "DATABASE_URL must include a username"
            )
        if parsed.password is None:
            raise DatabaseConfigurationError(
                "DATABASE_URL must include a password"
            )
        if not parsed.host:
            raise DatabaseConfigurationError(
                "DATABASE_URL must include a host"
            )
        if not parsed.database:
            raise DatabaseConfigurationError(
                "DATABASE_URL must include a database name"
            )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> DatabaseSettings:
        """Load the one required setting without implicit defaults."""
        values = os.environ if environment is None else environment
        try:
            url = values["DATABASE_URL"]
        except KeyError:
            raise DatabaseConfigurationError(
                "DATABASE_URL is required"
            ) from None
        return cls(url=url)


def create_database_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Create the async engine; schema creation remains Alembic-only."""
    return create_async_engine(
        settings.url,
        pool_pre_ping=True,
    )

