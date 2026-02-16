from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from ledger_worker.core.errors import WorkerError
from shared.contracts.messages import WorkerMessage
from shared.contracts.models import ErrorCode, OutboxEventType
from shared.db import OutboxEventORM

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ledger_worker.services.processor import EventPayload, WorkerProcessor


class WorkerModeStrategy(Protocol):
    def process(
        self, processor: WorkerProcessor, session: Session, event: OutboxEventORM, payload: EventPayload
    ) -> None: ...


@dataclass(frozen=True)
class StrongModeStrategy:
    def process(
        self, processor: WorkerProcessor, session: Session, event: OutboxEventORM, payload: EventPayload
    ) -> None:
        del payload
        processor.outbox(session).mark_processed(event)


@dataclass(frozen=True)
class HybridModeStrategy:
    def process(
        self, processor: WorkerProcessor, session: Session, event: OutboxEventORM, payload: EventPayload
    ) -> None:
        if event.event_type != OutboxEventType.PAYMENT_RESERVED.value:
            raise WorkerError(
                ErrorCode.INVARIANT_VIOLATION,
                f"{WorkerMessage.UNEXPECTED_EVENT.value} {event.event_type}",
            )
        processor._handle_hybrid_event(session, event, payload)


@dataclass(frozen=True)
class EventualModeStrategy:
    def process(
        self, processor: WorkerProcessor, session: Session, event: OutboxEventORM, payload: EventPayload
    ) -> None:
        if event.event_type != OutboxEventType.PAYMENT_REQUESTED.value:
            raise WorkerError(
                ErrorCode.INVARIANT_VIOLATION,
                f"{WorkerMessage.UNEXPECTED_EVENT.value} {event.event_type}",
            )
        processor._handle_eventual_event(session, event, payload)
