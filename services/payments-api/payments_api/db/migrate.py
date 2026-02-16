from __future__ import annotations

import os

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from payments_api.db.session import get_engine, get_session_factory
from shared.db import AccountORM, Base, IdempotencyKeyORM, LedgerEntryORM, OutboxEventORM, PaymentORM

SEED_ACCOUNTS = [
    ("acc-001", 1_000_000),
    ("acc-002", 1_000_000),
    ("acc-003", 1_000_000),
    ("acc-004", 1_000_000),
]


def create_schema(recreate: bool = False) -> None:
    engine = get_engine()
    if recreate:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def seed_accounts() -> None:
    session_factory = get_session_factory()
    session: Session = session_factory()
    try:
        with session.begin():
            for account_id, balance in SEED_ACCOUNTS:
                existing = session.scalar(select(AccountORM).where(AccountORM.id == account_id))
                if existing is None:
                    session.add(
                        AccountORM(
                            id=account_id,
                            available_balance_cents=balance,
                            reserved_balance_cents=0,
                            version=0,
                        )
                    )
                else:
                    existing.available_balance_cents = balance
                    existing.reserved_balance_cents = 0
                    existing.version = 0
    finally:
        session.close()


def reset_transactional_state() -> None:
    session_factory = get_session_factory()
    session: Session = session_factory()
    try:
        with session.begin():
            session.execute(delete(IdempotencyKeyORM))
            session.execute(delete(OutboxEventORM))
            session.execute(delete(LedgerEntryORM))
            session.execute(delete(PaymentORM))
    finally:
        session.close()


def migrate() -> None:
    recreate = os.getenv("MIGRATE_RECREATE_SCHEMA", "1") == "1"
    create_schema(recreate=recreate)
    if not recreate:
        reset_transactional_state()
    seed_accounts()


if __name__ == "__main__":
    migrate()
