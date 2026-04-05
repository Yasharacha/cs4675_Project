from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(slots=True)
class UrlMapping:
    code: str
    long_url: str
    created_at: datetime
    expires_at: datetime | None
    click_count: int = 0
    last_accessed_at: datetime | None = None

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= datetime.now(UTC)
