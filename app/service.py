from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from .models import UrlMapping
from .storage import InMemoryUrlRepository

BASE62_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


class InvalidUrlError(ValueError):
    pass


class ExpiredUrlError(ValueError):
    pass


class UnknownCodeError(KeyError):
    pass


class UrlShortenerService:
    def __init__(self, repository: InMemoryUrlRepository) -> None:
        self.repository = repository

    def create_short_url(self, long_url: str, expires_in_days: int | None = None) -> UrlMapping:
        normalized_url = self._validate_url(long_url)
        code = self._encode_base62(self.repository.next_id())
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=expires_in_days) if expires_in_days is not None else None
        mapping = UrlMapping(
            code=code,
            long_url=normalized_url,
            created_at=now,
            expires_at=expires_at,
        )
        return self.repository.save(mapping)

    def resolve(self, code: str) -> UrlMapping:
        mapping = self.repository.get(code)
        if mapping is None:
            raise UnknownCodeError(code)
        if mapping.is_expired:
            raise ExpiredUrlError(code)

        mapping.click_count += 1
        mapping.last_accessed_at = datetime.now(UTC)
        self.repository.save(mapping)
        return mapping

    def lookup(self, code: str) -> UrlMapping:
        mapping = self.repository.get(code)
        if mapping is None:
            raise UnknownCodeError(code)
        return mapping

    def list_urls(self) -> list[UrlMapping]:
        return self.repository.list_all()

    def serialize(self, mapping: UrlMapping) -> dict[str, object]:
        payload = asdict(mapping)
        payload["created_at"] = mapping.created_at.isoformat() + "Z"
        payload["expires_at"] = mapping.expires_at.isoformat() + "Z" if mapping.expires_at else None
        payload["last_accessed_at"] = (
            mapping.last_accessed_at.isoformat() + "Z" if mapping.last_accessed_at else None
        )
        payload["is_expired"] = mapping.is_expired
        return payload

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise InvalidUrlError("URL must include an http or https scheme and a valid host.")
        return url

    def _encode_base62(self, value: int) -> str:
        if value <= 0:
            raise ValueError("value must be positive")

        encoded: list[str] = []
        current = value
        while current:
            current, remainder = divmod(current, len(BASE62_ALPHABET))
            encoded.append(BASE62_ALPHABET[remainder])
        return "".join(reversed(encoded))
