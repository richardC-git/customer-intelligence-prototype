"""Fetched web page content, independent of how it was fetched or stored."""

import hashlib
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, computed_field, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]


class FetchStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"
    # Not fetched on purpose, e.g. login, paywall, CAPTCHA, or robots.txt disallow.
    BLOCKED = "blocked"


class FetchedPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    url: HttpUrl
    status: FetchStatus
    fetched_at: datetime
    http_status: int | None = None
    content_type: str | None = None
    title: str | None = None
    text: str | None = None
    error: str | None = None

    @computed_field
    @property
    def content_hash(self) -> str | None:
        """SHA-256 of the extracted text, used to detect when a page has changed."""
        if self.text is None:
            return None
        return hashlib.sha256(self.text.encode()).hexdigest()

    @model_validator(mode="after")
    def _status_matches_content(self) -> "FetchedPage":
        if self.fetched_at.tzinfo is None:
            raise ValueError("fetched_at must be timezone-aware.")
        if self.status is FetchStatus.OK and not self.text:
            raise ValueError("A successfully fetched page requires text.")
        if self.status is not FetchStatus.OK and not self.error:
            raise ValueError(f"A {self.status} page requires an error explaining why.")
        return self
