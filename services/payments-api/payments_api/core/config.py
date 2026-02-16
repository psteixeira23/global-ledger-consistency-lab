from __future__ import annotations

import os
from dataclasses import dataclass

from shared.contracts.models import ConsistencyMode


def _build_postgres_url() -> str:
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    database = os.getenv("POSTGRES_DB", "ledgerlab")
    user = os.getenv("POSTGRES_USER", "ledger")
    password = os.getenv("POSTGRES_PASSWORD", "ledger")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"


@dataclass(frozen=True)
class Settings:
    database_url: str
    consistency_mode: ConsistencyMode


def load_settings() -> Settings:
    raw_mode = os.getenv("CONSISTENCY_MODE", ConsistencyMode.HYBRID.value)
    mode = ConsistencyMode(raw_mode)
    database_url = os.getenv("DATABASE_URL", _build_postgres_url())
    return Settings(database_url=database_url, consistency_mode=mode)
