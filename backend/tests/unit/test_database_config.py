import pytest

from boundary.persistence.database import (
    DatabaseConfigurationError,
    DatabaseSettings,
)


VALID_URL = "postgresql+asyncpg://boundary:secret@postgres:5432/boundary"


def test_database_settings_require_explicit_environment_value() -> None:
    with pytest.raises(
        DatabaseConfigurationError,
        match="DATABASE_URL is required",
    ):
        DatabaseSettings.from_environment({})


def test_database_settings_accept_only_asyncpg_postgresql() -> None:
    with pytest.raises(
        DatabaseConfigurationError,
        match="postgresql\\+asyncpg",
    ):
        DatabaseSettings(
            url="sqlite+aiosqlite:///boundary.db",
        )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://:secret@postgres:5432/boundary",
        "postgresql+asyncpg://boundary@postgres:5432/boundary",
        "postgresql+asyncpg://boundary:secret@/boundary",
        "postgresql+asyncpg://boundary:secret@postgres:5432",
    ],
)
def test_database_settings_reject_incomplete_urls(url: str) -> None:
    with pytest.raises(DatabaseConfigurationError):
        DatabaseSettings(url=url)


def test_database_settings_preserve_the_explicit_url() -> None:
    settings = DatabaseSettings.from_environment(
        {"DATABASE_URL": VALID_URL}
    )

    assert settings.url == VALID_URL

