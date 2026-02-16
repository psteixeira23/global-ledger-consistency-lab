from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

import pytest
from sqlalchemy import delete

from ledger_worker.db.session import get_engine, get_session_factory
from shared.db import AccountORM, Base, LedgerEntryORM, OutboxEventORM, PaymentORM


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{tmp_path / 'ledger_worker.db'}"


@pytest.fixture(autouse=True)
def clean_database(db_url: str) -> Iterator[None]:
    os.environ["DATABASE_URL"] = db_url
    os.environ["CONSISTENCY_MODE"] = "hybrid"
    os.environ["FAIL_PROFILE"] = "none"
    os.environ["EXPERIMENT_SEED"] = "42"
    os.environ["DISABLE_OTEL_EXPORTER"] = "1"
    try:
        get_engine().dispose()
    except Exception:
        pass
    get_engine.cache_clear()
    get_session_factory.cache_clear()
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    session_factory = get_session_factory()
    session = session_factory()
    try:
        with session.begin():
            session.execute(delete(LedgerEntryORM))
            session.execute(delete(OutboxEventORM))
            session.execute(delete(PaymentORM))
            session.execute(delete(AccountORM))
            session.add_all(
                [
                    AccountORM(id="acc-001", available_balance_cents=1_000, reserved_balance_cents=0, version=0),
                    AccountORM(id="acc-002", available_balance_cents=1_000, reserved_balance_cents=0, version=0),
                ]
            )
    finally:
        session.close()
    yield
    try:
        get_engine().dispose()
    except Exception:
        pass
    get_engine.cache_clear()
    get_session_factory.cache_clear()
