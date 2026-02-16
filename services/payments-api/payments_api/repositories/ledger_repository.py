from __future__ import annotations

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from shared.contracts.models import LedgerDirection
from shared.db import AccountORM, LedgerEntryORM


class LedgerRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def imbalance_sum(self) -> int:
        expr = case(
            (LedgerEntryORM.direction == LedgerDirection.DEBIT.value, LedgerEntryORM.amount_cents),
            else_=-LedgerEntryORM.amount_cents,
        )
        statement = select(func.coalesce(func.sum(expr), 0))
        value = self.session.scalar(statement)
        return int(value or 0)

    def has_negative_balances(self) -> bool:
        statement = select(func.count()).select_from(AccountORM).where(
            (AccountORM.available_balance_cents < 0) | (AccountORM.reserved_balance_cents < 0)
        )
        value = self.session.scalar(statement)
        return int(value or 0) > 0
