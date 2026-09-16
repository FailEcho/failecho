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


#: Columns added after a table first shipped. create_all() creates missing
#: tables and never touches existing ones, so each of these is checked and
#: added on startup. Additive only, nullable only -- a column with a default
#: or a rewrite belongs to a real migration tool, not to this list.
ADDED_COLUMNS = (
    ("observations", "mutates", "BOOLEAN"),
)


def _add_missing_columns(sync_connection) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(sync_connection)
    for table, column, ddl_type in ADDED_COLUMNS:
        if table not in inspector.get_table_names():
            continue  # create_all made it with the column already
        present = {c["name"] for c in inspector.get_columns(table)}
        if column not in present:
            sync_connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


async def init_db() -> None:
    """Create tables if they do not exist, then add any columns that arrived
    after a table first shipped. Enough for an MVP; Alembic later."""
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.run_sync(_add_missing_columns)


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
