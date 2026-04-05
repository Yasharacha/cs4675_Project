from __future__ import annotations

from threading import Lock

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
