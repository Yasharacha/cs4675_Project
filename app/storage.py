from __future__ import annotations

import sqlite3
from threading import Lock
from pathlib import Path
from typing import Protocol

from .models import UrlMapping


class UrlRepository(Protocol):
    def save(self, mapping: UrlMapping) -> UrlMapping: ...

    def get(self, code: str) -> UrlMapping | None: ...

    def get_by_url(self, long_url: str) -> UrlMapping | None: ...

    def list_all(self) -> list[UrlMapping]: ...


class InMemoryUrlRepository:
    def __init__(self) -> None:
        self._mappings: dict[str, UrlMapping] = {}
        self._lock = Lock()

    def save(self, mapping: UrlMapping) -> UrlMapping:
        with self._lock:
            self._mappings[mapping.code] = mapping
            return mapping

    def get(self, code: str) -> UrlMapping | None:
        with self._lock:
            return self._mappings.get(code)

    def get_by_url(self, long_url: str) -> UrlMapping | None:
        with self._lock:
            for mapping in self._mappings.values():
                if mapping.long_url == long_url:
                    return mapping
            return None

    def list_all(self) -> list[UrlMapping]:
        with self._lock:
            return list(self._mappings.values())


class SQLiteUrlRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.database_path, detect_types=sqlite3.PARSE_DECLTYPES
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        db_path = Path(self.database_path)
        if db_path.parent != Path("."):
            db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS url_mappings (
                    code TEXT PRIMARY KEY,
                    long_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    click_count INTEGER NOT NULL DEFAULT 0,
                    last_accessed_at TEXT
                )
                """
            )
            connection.commit()

    def save(self, mapping: UrlMapping) -> UrlMapping:
        with self._lock, self._connect() as connection:
            # Try to insert first, if it exists, update it
            connection.execute(
                """
                INSERT OR REPLACE INTO url_mappings
                (code, long_url, created_at, expires_at, click_count, last_accessed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    mapping.code,
                    mapping.long_url,
                    mapping.created_at.isoformat(),
                    mapping.expires_at.isoformat() if mapping.expires_at else None,
                    mapping.click_count,
                    (
                        mapping.last_accessed_at.isoformat()
                        if mapping.last_accessed_at
                        else None
                    ),
                ),
            )
            connection.commit()
            return mapping

    def get(self, code: str) -> UrlMapping | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT code, long_url, created_at, expires_at, click_count, last_accessed_at
                FROM url_mappings
                WHERE code = ?
                """,
                (code,),
            ).fetchone()

        if row is None or row["long_url"] == "__pending__":
            return None

        return UrlMapping(
            code=row["code"],
            long_url=row["long_url"],
            created_at=self._parse_datetime(row["created_at"]),
            expires_at=(
                self._parse_datetime(row["expires_at"]) if row["expires_at"] else None
            ),
            click_count=row["click_count"],
            last_accessed_at=(
                self._parse_datetime(row["last_accessed_at"])
                if row["last_accessed_at"]
                else None
            ),
        )

    def get_by_url(self, long_url: str) -> UrlMapping | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT code, long_url, created_at, expires_at, click_count, last_accessed_at
                FROM url_mappings
                WHERE long_url = ?
                LIMIT 1
                """,
                (long_url,),
            ).fetchone()

        if row is None:
            return None

        return UrlMapping(
            code=row["code"],
            long_url=row["long_url"],
            created_at=self._parse_datetime(row["created_at"]),
            expires_at=(
                self._parse_datetime(row["expires_at"]) if row["expires_at"] else None
            ),
            click_count=row["click_count"],
            last_accessed_at=(
                self._parse_datetime(row["last_accessed_at"])
                if row["last_accessed_at"]
                else None
            ),
        )

    def list_all(self) -> list[UrlMapping]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT code, long_url, created_at, expires_at, click_count, last_accessed_at
                FROM url_mappings
                ORDER BY created_at
                """
            ).fetchall()

        return [
            UrlMapping(
                code=row["code"],
                long_url=row["long_url"],
                created_at=self._parse_datetime(row["created_at"]),
                expires_at=(
                    self._parse_datetime(row["expires_at"])
                    if row["expires_at"]
                    else None
                ),
                click_count=row["click_count"],
                last_accessed_at=(
                    self._parse_datetime(row["last_accessed_at"])
                    if row["last_accessed_at"]
                    else None
                ),
            )
            for row in rows
        ]

    @staticmethod
    def _parse_datetime(value: str):
        from datetime import datetime

        return datetime.fromisoformat(value)
