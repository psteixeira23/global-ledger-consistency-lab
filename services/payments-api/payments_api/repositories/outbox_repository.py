from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.contracts.models import OutboxStatus
from shared.db import OutboxEventORM


class OutboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, event: OutboxEventORM) -> None:
        self.session.add(event)

    def pending_count(self) -> int:
        statement = select(func.count()).select_from(OutboxEventORM).where(
            OutboxEventORM.status.in_([OutboxStatus.PENDING.value, OutboxStatus.PROCESSING.value])
        )
        count = self.session.scalar(statement)
        return int(count or 0)

    def dead_count(self) -> int:
        statement = select(func.count()).select_from(OutboxEventORM).where(
            OutboxEventORM.status == OutboxStatus.DEAD.value
        )
        count = self.session.scalar(statement)
        return int(count or 0)
