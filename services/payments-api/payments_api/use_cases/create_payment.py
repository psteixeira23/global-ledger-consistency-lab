from __future__ import annotations

import json
from typing import Final
from uuid import uuid4

from opentelemetry import trace
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from payments_api.core.errors import DomainError
from payments_api.repositories.accounts_repository import AccountsRepository
from payments_api.repositories.idempotency_repository import IdempotencyRepository
from payments_api.repositories.outbox_repository import OutboxRepository
from payments_api.repositories.payments_repository import PaymentsRepository
from payments_api.telemetry.metrics import IDEMPOTENCY_REPLAY, PAYMENTS_PROCESSED
from payments_api.use_cases.mode_strategies import (
    EventualModeStrategy,
    HybridModeStrategy,
    PaymentModeStrategy,
    StrongModeStrategy,
)
from shared.contracts.models import (
    ConsistencyMode,
    CreatePaymentRequest,
    ErrorCode,
    LedgerDirection,
    OutboxEventType,
    OutboxStatus,
    PaymentResponse,
    PaymentStatus,
)
from shared.contracts.messages import DomainMessage
from shared.db import AccountORM, LedgerEntryORM, OutboxEventORM, PaymentORM


class CreatePaymentUseCase:
    def __init__(self, session: Session, mode: ConsistencyMode) -> None:
        self.session = session
        self.mode = mode
        self.accounts = AccountsRepository(session)
        self.idempotency = IdempotencyRepository(session)
        self.payments = PaymentsRepository(session)
        self.outbox = OutboxRepository(session)
        self.tracer = trace.get_tracer("payments_api.use_cases.create_payment")
        self._strategies: Final[dict[ConsistencyMode, PaymentModeStrategy]] = {
            ConsistencyMode.STRONG: StrongModeStrategy(self),
            ConsistencyMode.HYBRID: HybridModeStrategy(self),
            ConsistencyMode.EVENTUAL: EventualModeStrategy(self),
        }

    def execute(self, request: CreatePaymentRequest, traceparent: str | None) -> PaymentResponse:
        self._validate_request(request)
        request_hash = request.compute_request_hash()
        if self.session.in_transaction():
            self.session.rollback()
        with self.tracer.start_as_current_span("payments.db.transaction"):
            response, created = self._run_transaction(request, request_hash, traceparent)
        if created:
            PAYMENTS_PROCESSED.inc()
        return response

    def _validate_request(self, request: CreatePaymentRequest) -> None:
        if request.source_account_id == request.destination_account_id:
            raise DomainError(
                error_code=ErrorCode.INVALID_PAYMENT,
                message=DomainMessage.SOURCE_DESTINATION_MUST_DIFFER.value,
                http_status=422,
            )

    def _get_or_validate_idempotency(self, key: str, request_hash: str) -> PaymentResponse | None:
        existing = self.idempotency.get(key)
        if existing is None:
            return None
        if existing.request_hash != request_hash:
            raise DomainError(
                error_code=ErrorCode.IDEMPOTENCY_CONFLICT,
                message=DomainMessage.IDEMPOTENCY_CONFLICT.value,
                http_status=409,
            )
        if not existing.response_payload_json:
            raise DomainError(
                error_code=ErrorCode.IDEMPOTENCY_UNAVAILABLE,
                message=DomainMessage.IDEMPOTENCY_IN_PROGRESS.value,
                http_status=503,
            )
        IDEMPOTENCY_REPLAY.inc()
        return PaymentResponse.model_validate_json(existing.response_payload_json)

    def _run_transaction(
        self, request: CreatePaymentRequest, request_hash: str, traceparent: str | None
    ) -> tuple[PaymentResponse, bool]:
        try:
            with self.session.begin():
                replay = self._get_or_validate_idempotency(request.idempotency_key, request_hash)
                if replay is not None:
                    return replay, False
                response = self._execute_mode(request, request_hash, traceparent)
                self.idempotency.save(
                    key=request.idempotency_key,
                    request_hash=request_hash,
                    response_payload_json=response.model_dump_json(),
                )
                return response, True
        except IntegrityError as exc:
            self.session.rollback()
            replay = self._get_or_validate_idempotency(request.idempotency_key, request_hash)
            if replay is not None:
                return replay, False
            raise DomainError(
                error_code=ErrorCode.IDEMPOTENCY_UNAVAILABLE,
                message=DomainMessage.IDEMPOTENCY_RACE.value,
                http_status=503,
            ) from exc
        except SQLAlchemyError as exc:
            raise DomainError(
                error_code=ErrorCode.DEPENDENCY_UNAVAILABLE,
                message=DomainMessage.DATABASE_UNAVAILABLE.value,
                http_status=503,
            ) from exc

    def _execute_mode(
        self, request: CreatePaymentRequest, request_hash: str, traceparent: str | None
    ) -> PaymentResponse:
        strategy = self._strategies[self.mode]
        return strategy.execute(request, request_hash, traceparent)

    def _lock_accounts(self, source_id: str, destination_id: str) -> tuple[AccountORM, AccountORM]:
        first_id, second_id = sorted([source_id, destination_id])
        first = self.accounts.get_for_update(first_id)
        second = self.accounts.get_for_update(second_id)
        if first is None or second is None:
            raise DomainError(
                error_code=ErrorCode.INVALID_PAYMENT,
                message=DomainMessage.ACCOUNT_NOT_FOUND.value,
                http_status=422,
            )
        by_id = {first.id: first, second.id: second}
        return by_id[source_id], by_id[destination_id]

    def _validate_funds(self, source: AccountORM, amount_cents: int) -> None:
        if source.available_balance_cents < amount_cents:
            raise DomainError(
                error_code=ErrorCode.INSUFFICIENT_FUNDS,
                message=DomainMessage.INSUFFICIENT_FUNDS.value,
                http_status=422,
            )

    def _create_payment(
        self, request: CreatePaymentRequest, request_hash: str, status: PaymentStatus
    ) -> str:
        payment_id = f"pay-{uuid4().hex}"
        payment = PaymentORM(
            id=payment_id,
            idempotency_key=request.idempotency_key,
            request_hash=request_hash,
            source_account_id=request.source_account_id,
            destination_account_id=request.destination_account_id,
            amount_cents=request.amount_cents,
            method=request.method.value,
            status=status.value,
        )
        self.payments.save(payment)
        return payment_id

    def _add_ledger_entries(
        self, payment_id: str, source_id: str, destination_id: str, amount_cents: int
    ) -> None:
        debit_entry = LedgerEntryORM(
            id=f"led-{uuid4().hex}",
            payment_id=payment_id,
            account_id=source_id,
            direction=LedgerDirection.DEBIT.value,
            amount_cents=amount_cents,
        )
        credit_entry = LedgerEntryORM(
            id=f"led-{uuid4().hex}",
            payment_id=payment_id,
            account_id=destination_id,
            direction=LedgerDirection.CREDIT.value,
            amount_cents=amount_cents,
        )
        self.session.add(debit_entry)
        self.session.add(credit_entry)

    def _add_outbox(
        self,
        payment_id: str,
        event_type: OutboxEventType,
        request: CreatePaymentRequest,
        traceparent: str | None,
    ) -> None:
        payload = {
            "payment_id": payment_id,
            "source_account_id": request.source_account_id,
            "destination_account_id": request.destination_account_id,
            "amount_cents": request.amount_cents,
            "traceparent": traceparent,
        }
        event = OutboxEventORM(
            id=f"evt-{uuid4().hex}",
            aggregate_type="payment",
            aggregate_id=payment_id,
            event_type=event_type.value,
            payload_json=json.dumps(payload, sort_keys=True),
            status=OutboxStatus.PENDING.value,
            attempts=0,
            next_retry_at=None,
        )
        self.outbox.save(event)
