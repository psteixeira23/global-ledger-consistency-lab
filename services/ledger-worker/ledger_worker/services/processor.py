from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Final, Protocol
from uuid import uuid4

from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import Select, select
from sqlalchemy.orm import Session, sessionmaker

from ledger_worker.core.errors import WorkerError
from ledger_worker.repositories.domain_repository import DomainRepository
from ledger_worker.repositories.outbox_repository import OutboxRepository
from ledger_worker.services.mode_strategies import (
    EventualModeStrategy,
    HybridModeStrategy,
    StrongModeStrategy,
    WorkerModeStrategy,
)
from ledger_worker.telemetry.metrics import INVARIANT_VIOLATION, OUTBOX_RETRY, PAYMENTS_PROCESSED
from shared.contracts.messages import WorkerMessage
from shared.contracts.models import (
    ConsistencyMode,
    ErrorCode,
    LedgerDirection,
    OutboxStatus,
    PaymentStatus,
)
from shared.db import AccountORM, LedgerEntryORM, OutboxEventORM


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class EventPayload:
    payment_id: str
    source_account_id: str
    destination_account_id: str
    amount_cents: int
    traceparent: str | None


class FailureInjectorPort(Protocol):
    def maybe_apply_db_delay(self, event_id: str, attempt: int) -> None: ...

    def should_raise_worker_exception(self, event_id: str, attempt: int) -> bool: ...

    def should_fail_redis_simulation(self, event_id: str, attempt: int) -> bool: ...


class WorkerProcessor:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        mode: ConsistencyMode,
        failure_injector: FailureInjectorPort,
        processing_timeout_seconds: float = 30.0,
    ) -> None:
        self.session_factory = session_factory
        self.mode = mode
        self.failure_injector = failure_injector
        self.processing_timeout_seconds = processing_timeout_seconds
        self.tracer = trace.get_tracer("ledger_worker.processor")
        self._strategies: Final[dict[ConsistencyMode, WorkerModeStrategy]] = {
            ConsistencyMode.STRONG: StrongModeStrategy(),
            ConsistencyMode.HYBRID: HybridModeStrategy(),
            ConsistencyMode.EVENTUAL: EventualModeStrategy(),
        }

    def process_available_events(self, batch_size: int = 20) -> int:
        event_ids = self._acquire_event_ids(batch_size)
        for event_id in event_ids:
            self._process_event_by_id(event_id)
        return len(event_ids)

    def _acquire_event_ids(self, batch_size: int) -> list[str]:
        session = self.session_factory()
        try:
            with session.begin():
                events = OutboxRepository(session).fetch_batch_for_processing(
                    batch_size=batch_size,
                    processing_timeout_seconds=self.processing_timeout_seconds,
                )
                return [event.id for event in events]
        finally:
            session.close()

    def _process_event_by_id(self, event_id: str) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                event = self._load_event_for_update(session, event_id)
                if event is None:
                    return
                self._process_event(session, event)
        except WorkerError as exc:
            self._handle_permanent_failure(event_id, exc)
        except Exception:
            self._handle_transient_failure(event_id)
        finally:
            session.close()

    def _load_event_for_update(self, session: Session, event_id: str) -> OutboxEventORM | None:
        statement: Select[tuple[OutboxEventORM]] = select(OutboxEventORM).where(OutboxEventORM.id == event_id)
        dialect = session.bind.dialect.name if session.bind is not None else ""
        if dialect != "sqlite":
            statement = statement.with_for_update()
        return session.scalar(statement)

    def _process_event(self, session: Session, event: OutboxEventORM) -> None:
        payload = self._parse_payload(event.payload_json)
        parent = self._extract_context(payload)
        attempt = event.attempts + 1
        with self.tracer.start_as_current_span("worker.process_event", context=parent):
            self.failure_injector.maybe_apply_db_delay(event.id, attempt)
            if self.failure_injector.should_raise_worker_exception(event.id, attempt):
                raise RuntimeError(WorkerMessage.DETERMINISTIC_WORKER_FAILURE.value)
            if self.failure_injector.should_fail_redis_simulation(event.id, attempt):
                raise RuntimeError(WorkerMessage.DETERMINISTIC_REDIS_FAILURE.value)
            strategy = self._strategies[self.mode]
            strategy.process(self, session, event, payload)

    def _parse_payload(self, payload_json: str) -> EventPayload:
        payload: dict[str, object] = json.loads(payload_json)
        return EventPayload(
            payment_id=self._as_required_str(payload, "payment_id"),
            source_account_id=self._as_required_str(payload, "source_account_id"),
            destination_account_id=self._as_required_str(payload, "destination_account_id"),
            amount_cents=self._as_required_int(payload, "amount_cents"),
            traceparent=self._as_optional_str(payload, "traceparent"),
        )

    def _extract_context(self, payload: EventPayload) -> Context | None:
        if not payload.traceparent:
            return None
        carrier = {"traceparent": payload.traceparent}
        return TraceContextTextMapPropagator().extract(carrier=carrier)

    def _handle_hybrid_event(
        self, session: Session, event: OutboxEventORM, payload: EventPayload
    ) -> None:
        payment_id = payload.payment_id
        source_id = payload.source_account_id
        destination_id = payload.destination_account_id
        amount_cents = payload.amount_cents
        repository = DomainRepository(session)
        payment = repository.get_payment_for_update(payment_id)
        if payment is None:
            raise WorkerError(ErrorCode.INVARIANT_VIOLATION, WorkerMessage.PAYMENT_NOT_FOUND.value)
        if payment.status in [PaymentStatus.COMPLETED.value, PaymentStatus.REJECTED.value]:
            self.outbox(session).mark_processed(event)
            return
        source, destination = self._lock_accounts(repository, source_id, destination_id)
        if source.reserved_balance_cents < amount_cents:
            raise WorkerError(
                ErrorCode.INVARIANT_VIOLATION,
                WorkerMessage.RESERVED_FUNDS_BELOW_AMOUNT.value,
            )
        source.reserved_balance_cents -= amount_cents
        source.version += 1
        destination.available_balance_cents += amount_cents
        destination.version += 1
        payment.status = PaymentStatus.COMPLETED.value
        self._add_ledger_entries(repository, payment_id, source_id, destination_id, amount_cents)
        self.outbox(session).mark_processed(event)
        PAYMENTS_PROCESSED.inc()

    def _handle_eventual_event(
        self, session: Session, event: OutboxEventORM, payload: EventPayload
    ) -> None:
        payment_id = payload.payment_id
        source_id = payload.source_account_id
        destination_id = payload.destination_account_id
        amount_cents = payload.amount_cents
        repository = DomainRepository(session)
        payment = repository.get_payment_for_update(payment_id)
        if payment is None:
            raise WorkerError(ErrorCode.INVARIANT_VIOLATION, WorkerMessage.PAYMENT_NOT_FOUND.value)
        if payment.status in [PaymentStatus.COMPLETED.value, PaymentStatus.REJECTED.value]:
            self.outbox(session).mark_processed(event)
            return
        source, destination = self._lock_accounts(repository, source_id, destination_id)
        if source.available_balance_cents < amount_cents:
            payment.status = PaymentStatus.REJECTED.value
            self.outbox(session).mark_processed(event)
            PAYMENTS_PROCESSED.inc()
            return
        source.available_balance_cents -= amount_cents
        source.version += 1
        destination.available_balance_cents += amount_cents
        destination.version += 1
        payment.status = PaymentStatus.COMPLETED.value
        self._add_ledger_entries(repository, payment_id, source_id, destination_id, amount_cents)
        self.outbox(session).mark_processed(event)
        PAYMENTS_PROCESSED.inc()

    def _lock_accounts(
        self, repository: DomainRepository, source_id: str, destination_id: str
    ) -> tuple[AccountORM, AccountORM]:
        first_id, second_id = sorted([source_id, destination_id])
        first = repository.get_account_for_update(first_id)
        second = repository.get_account_for_update(second_id)
        if first is None or second is None:
            raise WorkerError(ErrorCode.INVARIANT_VIOLATION, WorkerMessage.ACCOUNT_NOT_FOUND.value)
        by_id = {first.id: first, second.id: second}
        return by_id[source_id], by_id[destination_id]

    def _add_ledger_entries(
        self, repository: DomainRepository, payment_id: str, source_id: str, destination_id: str, amount_cents: int
    ) -> None:
        repository.add_ledger_entry(
            LedgerEntryORM(
                id=f"led-{uuid4().hex}",
                payment_id=payment_id,
                account_id=source_id,
                direction=LedgerDirection.DEBIT.value,
                amount_cents=amount_cents,
            )
        )
        repository.add_ledger_entry(
            LedgerEntryORM(
                id=f"led-{uuid4().hex}",
                payment_id=payment_id,
                account_id=destination_id,
                direction=LedgerDirection.CREDIT.value,
                amount_cents=amount_cents,
            )
        )

    def _handle_permanent_failure(self, event_id: str, _exc: WorkerError) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                event = self._load_event_for_update(session, event_id)
                if event is None:
                    return
                event.status = OutboxStatus.DEAD.value
                event.next_retry_at = None
                INVARIANT_VIOLATION.inc()
        finally:
            session.close()

    def _handle_transient_failure(self, event_id: str) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                event = self._load_event_for_update(session, event_id)
                if event is None:
                    return
                retry_delay_seconds = 2 ** min(event.attempts + 1, 6)
                next_retry_at = utc_now() + timedelta(seconds=retry_delay_seconds)
                self.outbox(session).mark_retry(event, next_retry_at)
                if event.status != OutboxStatus.DEAD.value:
                    OUTBOX_RETRY.inc()
        finally:
            session.close()

    def outbox(self, session: Session) -> OutboxRepository:
        return OutboxRepository(session)

    def _as_required_str(self, payload: dict[str, object], field: str) -> str:
        value = payload.get(field)
        if isinstance(value, str) and value:
            return value
        raise WorkerError(
            ErrorCode.INVARIANT_VIOLATION,
            f"{WorkerMessage.INVALID_PAYLOAD_FIELD.value}: {field}",
        )

    def _as_required_int(self, payload: dict[str, object], field: str) -> int:
        value = payload.get(field)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        raise WorkerError(
            ErrorCode.INVARIANT_VIOLATION,
            f"{WorkerMessage.INVALID_PAYLOAD_FIELD.value}: {field}",
        )

    def _as_optional_str(self, payload: dict[str, object], field: str) -> str | None:
        value = payload.get(field)
        if value is None:
            return None
        if isinstance(value, str) and value:
            return value
        return None
