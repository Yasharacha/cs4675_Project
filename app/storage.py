from __future__ import annotations

import sqlite3
from threading import Lock
from pathlib import Path

from .models import UrlMapping


class InMemoryUrlRepository:
    def __init__(self) -> None:
        self._mappings: dict[str, UrlMapping] = {}
        self._counter = 0
        self._lock = Lock()

    def next_id(self) -> int:
        with self._lock:
            self._counter += 1
            return self._counter

    def save(self, mapping: UrlMapping) -> UrlMapping:
        with self._lock:
            self._mappings[mapping.code] = mapping
            return mapping

    def get(self, code: str) -> UrlMapping | None:
        with self._lock:
            return self._mappings.get(code)

    def list_all(self) -> list[UrlMapping]:
        with self._lock:
            return list(self._mappings.values())


class SQLiteUrlRepository:
    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        self._lock = Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, detect_types=sqlite3.PARSE_DECLTYPES)
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
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE,
                    long_url TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT,
                    click_count INTEGER NOT NULL DEFAULT 0,
                    last_accessed_at TEXT
                )
                """
            )
            connection.commit()

    def next_id(self) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO url_mappings (code, long_url, created_at)
                VALUES (?, ?, ?)
                """,
                (None, "__pending__", "__pending__"),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def save(self, mapping: UrlMapping) -> UrlMapping:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE url_mappings
                SET code = ?,
                    long_url = ?,
                    created_at = ?,
                    expires_at = ?,
                    click_count = ?,
                    last_accessed_at = ?
                WHERE code = ? OR id = ?
                """,
                (
                    mapping.code,
                    mapping.long_url,
                    mapping.created_at.isoformat(),
                    mapping.expires_at.isoformat() if mapping.expires_at else None,
                    mapping.click_count,
                    mapping.last_accessed_at.isoformat() if mapping.last_accessed_at else None,
                    mapping.code,
                    self._decode_base62(mapping.code),
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
            expires_at=self._parse_datetime(row["expires_at"]) if row["expires_at"] else None,
            click_count=row["click_count"],
            last_accessed_at=self._parse_datetime(row["last_accessed_at"]) if row["last_accessed_at"] else None,
        )

    def list_all(self) -> list[UrlMapping]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT code, long_url, created_at, expires_at, click_count, last_accessed_at
                FROM url_mappings
                WHERE code IS NOT NULL AND long_url != '__pending__'
                ORDER BY id
                """
            ).fetchall()

        return [
            UrlMapping(
                code=row["code"],
                long_url=row["long_url"],
                created_at=self._parse_datetime(row["created_at"]),
                expires_at=self._parse_datetime(row["expires_at"]) if row["expires_at"] else None,
                click_count=row["click_count"],
                last_accessed_at=self._parse_datetime(row["last_accessed_at"]) if row["last_accessed_at"] else None,
            )
            for row in rows
        ]

    @staticmethod
    def _parse_datetime(value: str):
        from datetime import datetime

        return datetime.fromisoformat(value)

    @staticmethod
    def _decode_base62(code: str) -> int:
        alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
        value = 0
        for char in code:
            value = value * len(alphabet) + alphabet.index(char)
        return value
