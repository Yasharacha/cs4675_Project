from __future__ import annotations

import hashlib
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

from .models import UrlMapping
from .storage import UrlRepository

BASE62_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class InvalidUrlError(ValueError):
    pass


class ExpiredUrlError(ValueError):
    pass


class UnknownCodeError(KeyError):
    pass


class UrlShortenerService:
    def __init__(self, repository: UrlRepository) -> None:
        self.repository = repository

    def create_short_url(
        self, long_url: str, expires_in_days: int | None = None
    ) -> UrlMapping:
        normalized_url = self._validate_url(long_url)
        existing = self.repository.get_by_url(normalized_url)
        if existing is not None and not existing.is_expired:
            return existing

        code = self._generate_short_code(normalized_url)

        now = datetime.now(UTC)
        expires_at = (
            now + timedelta(days=expires_in_days)
            if expires_in_days is not None
            else None
        )
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
        payload["expires_at"] = (
            mapping.expires_at.isoformat() + "Z" if mapping.expires_at else None
        )
        payload["last_accessed_at"] = (
            mapping.last_accessed_at.isoformat() + "Z"
            if mapping.last_accessed_at
            else None
        )
        payload["is_expired"] = mapping.is_expired
        return payload

    def _validate_url(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise InvalidUrlError(
                "URL must include an http or https scheme and a valid host."
            )
        return url

    def _generate_short_code(self, url: str) -> str:
        url_hash = hashlib.sha256(url.encode()).digest()
        hash_int = int.from_bytes(url_hash[:6], byteorder="big")

        for code_length in range(6, 12):
            code = self._encode_base62(hash_int, code_length)

            existing = self.repository.get(code)
            if existing is None:
                return code
            if existing.long_url == url:
                return code

            hash_int = (hash_int * 31 + code_length) % (62**12)

        code = self._encode_base62(hash_int % (62**11), 11)
        return code

    def _encode_base62(self, value: int, min_length: int = 1) -> str:
        if value == 0:
            return "0" * min_length

        digits = []
        while value > 0:
            digits.append(BASE62_ALPHABET[value % 62])
            value //= 62

        code = "".join(reversed(digits))
        return code.rjust(min_length, "0")
