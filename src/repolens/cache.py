from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .database import Database

_Now = Callable[[], datetime]


class ResponseCache(Protocol):
    @property
    def data_version(self) -> str: ...

    async def get(self, cache_key: str) -> Any | None: ...

    async def set(self, cache_key: str, value: Any, ttl_seconds: int) -> None: ...


def build_cache_key(
    method: str,
    path: str,
    params: Mapping[str, str | int] | None,
    data_version: str,
) -> str:
    canonical = json.dumps(
        {
            "method": method.upper(),
            "path": path,
            "params": dict(sorted((params or {}).items())),
            "data_version": data_version,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class CacheRepository:
    def __init__(
        self,
        database: Database,
        *,
        data_version: str,
        now: _Now | None = None,
    ) -> None:
        self._database = database
        self._data_version = data_version
        self._now = now or (lambda: datetime.now(UTC))

    @property
    def data_version(self) -> str:
        return self._data_version

    async def get(self, cache_key: str) -> Any | None:
        async with self._database.connect() as connection:
            cursor = await connection.execute(
                """
                SELECT response_json, expires_at, data_version
                FROM response_cache
                WHERE cache_key = ?
                """,
                (cache_key,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        try:
            expires_at = datetime.fromisoformat(row["expires_at"])
            if expires_at.tzinfo is None or expires_at.utcoffset() is None:
                raise ValueError("cache timestamp has no timezone")
            if row["data_version"] != self._data_version or expires_at <= self._now():
                await self.delete(cache_key)
                return None
            return json.loads(row["response_json"])
        except (json.JSONDecodeError, TypeError, ValueError):
            await self.delete(cache_key)
            return None

    async def set(self, cache_key: str, value: Any, ttl_seconds: int) -> None:
        if ttl_seconds <= 0:
            return
        created_at = self._now()
        expires_at = created_at + timedelta(seconds=ttl_seconds)
        response_json = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        async with self._database.write_lock, self._database.connect() as connection:
            await connection.execute(
                """
                INSERT INTO response_cache (
                    cache_key, response_json, created_at, expires_at, data_version
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    response_json = excluded.response_json,
                    created_at = excluded.created_at,
                    expires_at = excluded.expires_at,
                    data_version = excluded.data_version
                """,
                (
                    cache_key,
                    response_json,
                    created_at.isoformat(),
                    expires_at.isoformat(),
                    self._data_version,
                ),
            )
            await connection.commit()

    async def delete(self, cache_key: str) -> None:
        async with self._database.write_lock, self._database.connect() as connection:
            await connection.execute("DELETE FROM response_cache WHERE cache_key = ?", (cache_key,))
            await connection.commit()

    async def clear_expired(self) -> int:
        async with self._database.write_lock, self._database.connect() as connection:
            cursor = await connection.execute(
                "DELETE FROM response_cache WHERE expires_at <= ? OR data_version != ?",
                (self._now().isoformat(), self._data_version),
            )
            await connection.commit()
            return max(cursor.rowcount, 0)
