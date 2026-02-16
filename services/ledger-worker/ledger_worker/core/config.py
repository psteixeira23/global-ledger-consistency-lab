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
    fail_profile: str
    experiment_seed: int
    poll_interval_seconds: float
    reconciliation_interval_seconds: float
    processing_timeout_seconds: float


def load_settings() -> Settings:
    mode = ConsistencyMode(os.getenv("CONSISTENCY_MODE", ConsistencyMode.HYBRID.value))
    database_url = os.getenv("DATABASE_URL", _build_postgres_url())
    fail_profile = os.getenv("FAIL_PROFILE", "none")
    experiment_seed = int(os.getenv("EXPERIMENT_SEED", "42"))
    poll_interval_seconds = float(os.getenv("OUTBOX_POLL_INTERVAL_SECONDS", "0.2"))
    reconciliation_interval_seconds = float(os.getenv("RECONCILIATION_INTERVAL_SECONDS", "5"))
    processing_timeout_seconds = float(os.getenv("OUTBOX_PROCESSING_TIMEOUT_SECONDS", "30"))
    return Settings(
        database_url=database_url,
        consistency_mode=mode,
        fail_profile=fail_profile,
        experiment_seed=experiment_seed,
        poll_interval_seconds=poll_interval_seconds,
        reconciliation_interval_seconds=reconciliation_interval_seconds,
        processing_timeout_seconds=processing_timeout_seconds,
    )
