from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from payments_api.core.config import Settings, load_settings
from payments_api.db.session import get_session


def get_settings() -> Settings:
    return load_settings()


def db_session() -> Iterator[Session]:
    yield from get_session()
