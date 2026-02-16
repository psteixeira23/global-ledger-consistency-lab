from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from shared.contracts.models import (
    CreatePaymentRequest,
    OutboxEventType,
    PaymentResponse,
    PaymentStatus,
)

if TYPE_CHECKING:
    from payments_api.use_cases.create_payment import CreatePaymentUseCase


class PaymentModeStrategy(Protocol):
    def execute(
        self,
        request: CreatePaymentRequest,
        request_hash: str,
        traceparent: str | None,
    ) -> PaymentResponse: ...


@dataclass(frozen=True)
class StrongModeStrategy:
    use_case: CreatePaymentUseCase

    def execute(
        self,
        request: CreatePaymentRequest,
        request_hash: str,
        traceparent: str | None,
    ) -> PaymentResponse:
        del traceparent
        source, destination = self.use_case._lock_accounts(
            request.source_account_id, request.destination_account_id
        )
        self.use_case._validate_funds(source, request.amount_cents)
        source.available_balance_cents -= request.amount_cents
        source.version += 1
        destination.available_balance_cents += request.amount_cents
        destination.version += 1
        payment_id = self.use_case._create_payment(request, request_hash, PaymentStatus.COMPLETED)
        self.use_case._add_ledger_entries(
            payment_id,
            request.source_account_id,
            request.destination_account_id,
            request.amount_cents,
        )
        return PaymentResponse(payment_id=payment_id, status=PaymentStatus.COMPLETED)


@dataclass(frozen=True)
class HybridModeStrategy:
    use_case: CreatePaymentUseCase

    def execute(
        self,
        request: CreatePaymentRequest,
        request_hash: str,
        traceparent: str | None,
    ) -> PaymentResponse:
        source, _ = self.use_case._lock_accounts(request.source_account_id, request.destination_account_id)
        self.use_case._validate_funds(source, request.amount_cents)
        source.available_balance_cents -= request.amount_cents
        source.reserved_balance_cents += request.amount_cents
        source.version += 1
        payment_id = self.use_case._create_payment(request, request_hash, PaymentStatus.RESERVED)
        self.use_case._add_outbox(
            payment_id=payment_id,
            event_type=OutboxEventType.PAYMENT_RESERVED,
            request=request,
            traceparent=traceparent,
        )
        return PaymentResponse(payment_id=payment_id, status=PaymentStatus.RESERVED)


@dataclass(frozen=True)
class EventualModeStrategy:
    use_case: CreatePaymentUseCase

    def execute(
        self,
        request: CreatePaymentRequest,
        request_hash: str,
        traceparent: str | None,
    ) -> PaymentResponse:
        payment_id = self.use_case._create_payment(request, request_hash, PaymentStatus.RECEIVED)
        self.use_case._add_outbox(
            payment_id=payment_id,
            event_type=OutboxEventType.PAYMENT_REQUESTED,
            request=request,
            traceparent=traceparent,
        )
        return PaymentResponse(payment_id=payment_id, status=PaymentStatus.RECEIVED)
