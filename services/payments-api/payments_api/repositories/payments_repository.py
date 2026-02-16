from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.db import PaymentORM


class PaymentsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, payment: PaymentORM) -> None:
        self.session.add(payment)

    def count_by_status(self, status: str) -> int:
        statement = select(func.count()).select_from(PaymentORM).where(PaymentORM.status == status)
        count = self.session.scalar(statement)
        return int(count or 0)
