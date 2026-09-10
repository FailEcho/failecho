"""Async engine, session factory and schema bootstrap.

SQLite is the MVP store. One file, ~0 MB of RAM, no daemon. The async driver
(aiosqlite) keeps the FastAPI event loop free while the (serialised) SQLite
writes happen on its worker thread.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.db.models import Base


def _ensure_sqlite_dir(url: str) -> None:
    """Create the parent directory of a SQLite file if it does not exist."""
    marker = "sqlite+aiosqlite:///"
    if not url.startswith(marker):
        return
    path = url[len(marker) :]
    if path in ("", ":memory:"):
        return
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)


_ensure_sqlite_dir(settings.database_url)

engine = create_async_engine(
    settings.database_url,
    echo=settings.sql_echo,
    future=True,
    pool_pre_ping=True,
)

if engine.dialect.name == "sqlite":

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _connection_record):  # pragma: no cover
        """WAL + relaxed fsync: concurrent readers during writes, low I/O cost."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


SessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def init_db() -> None:
    """Create tables if they do not exist. Enough for an MVP; Alembic later."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


def hour_bucket_expr(column):
    """Dialect-portable "truncate this timestamp to its UTC hour".

    SQLite has no date_trunc, PostgreSQL has no strftime. One helper keeps the
    rest of the codebase free of dialect checks and the migration path short.
    """
    if engine.dialect.name == "sqlite":
        from sqlalchemy import func

        return func.strftime("%Y-%m-%d %H:00:00", column)
    from sqlalchemy import func

    return func.date_trunc("hour", column)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one session per request."""
    async with SessionLocal() as session:
        yield session
