from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

_MIGRATIONS = (
    """
    CREATE TABLE IF NOT EXISTS response_cache (
        cache_key TEXT PRIMARY KEY,
        response_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        data_version TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_response_cache_expires_at
        ON response_cache(expires_at);

    CREATE TABLE IF NOT EXISTS analysis_tasks (
        task_id TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        progress INTEGER NOT NULL,
        current_stage TEXT,
        error_json TEXT,
        request_json TEXT NOT NULL,
        report_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_analysis_tasks_status_updated
        ON analysis_tasks(status, updated_at);
    """,
    """
    CREATE TABLE IF NOT EXISTS report_confirmations (
        task_id TEXT PRIMARY KEY,
        confirmation_json TEXT NOT NULL,
        confirmed_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES analysis_tasks(task_id) ON DELETE CASCADE
    );
    """,
)


class Database:
    def __init__(self, database_url: str) -> None:
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise ValueError("当前仅支持 sqlite:/// 格式的数据库地址")
        raw_path = database_url.removeprefix(prefix)
        if not raw_path:
            raise ValueError("SQLite 数据库路径不能为空")
        self.path = raw_path
        self._write_lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self.path != ":memory:":
            Path(self.path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        async with self._write_lock, self.connect() as connection:
            cursor = await connection.execute("PRAGMA user_version")
            row = await cursor.fetchone()
            current_version = int(row[0]) if row else 0
            for version, migration in enumerate(_MIGRATIONS, start=1):
                if version <= current_version:
                    continue
                await connection.executescript(migration)
                await connection.execute(f"PRAGMA user_version = {version}")
            await connection.commit()

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        connection = await aiosqlite.connect(self.path, timeout=5)
        connection.row_factory = aiosqlite.Row
        await connection.execute("PRAGMA foreign_keys = ON")
        await connection.execute("PRAGMA busy_timeout = 5000")
        if self.path != ":memory:":
            await connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            await connection.close()

    @property
    def write_lock(self) -> asyncio.Lock:
        return self._write_lock
