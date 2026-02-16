from __future__ import annotations

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session

from shared.contracts.models import LedgerDirection
from shared.db import AccountORM, LedgerEntryORM, PaymentORM


class DomainRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_account_for_update(self, account_id: str) -> AccountORM | None:
        statement: Select[tuple[AccountORM]] = select(AccountORM).where(AccountORM.id == account_id)
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect != "sqlite":
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def get_payment_for_update(self, payment_id: str) -> PaymentORM | None:
        statement: Select[tuple[PaymentORM]] = select(PaymentORM).where(PaymentORM.id == payment_id)
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect != "sqlite":
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def add_ledger_entry(self, entry: LedgerEntryORM) -> None:
        self.session.add(entry)

    def ledger_imbalance(self) -> int:
        expression = case(
            (LedgerEntryORM.direction == LedgerDirection.DEBIT.value, LedgerEntryORM.amount_cents),
            else_=-LedgerEntryORM.amount_cents,
        )
        statement = select(func.coalesce(func.sum(expression), 0))
        value = self.session.scalar(statement)
        return int(value or 0)

    def negative_balance_count(self) -> int:
        statement = select(func.count()).select_from(AccountORM).where(
            (AccountORM.available_balance_cents < 0) | (AccountORM.reserved_balance_cents < 0)
        )
        value = self.session.scalar(statement)
        return int(value or 0)
