from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session

from shared.contracts.models import OutboxStatus
from shared.db import OutboxEventORM


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class OutboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def fetch_batch_for_processing(
        self, batch_size: int, processing_timeout_seconds: float = 30.0
    ) -> list[OutboxEventORM]:
        now = utc_now()
        lease_expiration = now + timedelta(seconds=processing_timeout_seconds)
        statement: Select[tuple[OutboxEventORM]] = (
            select(OutboxEventORM)
            .where(
                or_(
                    (
                        (OutboxEventORM.status == OutboxStatus.PENDING.value)
                        & or_(OutboxEventORM.next_retry_at.is_(None), OutboxEventORM.next_retry_at <= now)
                    ),
                    (
                        (OutboxEventORM.status == OutboxStatus.PROCESSING.value)
                        & or_(OutboxEventORM.next_retry_at.is_(None), OutboxEventORM.next_retry_at <= now)
                    ),
                )
            )
            .order_by(OutboxEventORM.created_at.asc(), OutboxEventORM.id.asc())
            .limit(batch_size)
        )
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect != "sqlite":
            statement = statement.with_for_update(skip_locked=True)
        events = list(self.session.scalars(statement))
        for event in events:
            event.status = OutboxStatus.PROCESSING.value
            event.next_retry_at = lease_expiration
        return events

    def mark_processed(self, event: OutboxEventORM) -> None:
        event.status = OutboxStatus.PROCESSED.value
        event.next_retry_at = None

    def mark_retry(self, event: OutboxEventORM, next_retry_at: datetime) -> None:
        event.attempts += 1
        if event.attempts >= 7:
            event.status = OutboxStatus.DEAD.value
            event.next_retry_at = None
            return
        event.status = OutboxStatus.PENDING.value
        event.next_retry_at = next_retry_at
