from __future__ import annotations

import json
from hashlib import sha256
from enum import Enum

from pydantic import BaseModel, Field


class ConsistencyMode(str, Enum):
    STRONG = "strong"
    HYBRID = "hybrid"
    EVENTUAL = "eventual"


class ErrorCode(str, Enum):
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    INVALID_PAYMENT = "INVALID_PAYMENT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    IDEMPOTENCY_UNAVAILABLE = "IDEMPOTENCY_UNAVAILABLE"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INVARIANT_VIOLATION = "INVARIANT_VIOLATION"


class IncidentSeverity(str, Enum):
    INFO = "info"
    P2 = "p2"
    P1 = "p1"


class PaymentStatus(str, Enum):
    RECEIVED = "received"
    RESERVED = "reserved"
    COMPLETED = "completed"
    REJECTED = "rejected"


class PaymentMethod(str, Enum):
    PIX = "pix"
    TED = "ted"


class LedgerDirection(str, Enum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class OutboxStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PROCESSED = "processed"
    DEAD = "dead"


class OutboxEventType(str, Enum):
    PAYMENT_RESERVED = "PAYMENT_RESERVED"
    PAYMENT_REQUESTED = "PAYMENT_REQUESTED"


class CreatePaymentRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)
    source_account_id: str = Field(min_length=3, max_length=64)
    destination_account_id: str = Field(min_length=3, max_length=64)
    amount_cents: int = Field(gt=0, le=50_000_000)
    method: PaymentMethod = PaymentMethod.PIX

    def compute_request_hash(self) -> str:
        payload = self.model_dump(mode="json", by_alias=True)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return sha256(encoded).hexdigest()


class PaymentCreatedEvent(BaseModel):
    payment_id: str
    idempotency_key: str
    source_account_id: str
    destination_account_id: str
    amount_cents: int
    traceparent: str | None = None


class PaymentResponse(BaseModel):
    payment_id: str
    status: PaymentStatus


class ApiErrorResponse(BaseModel):
    error_code: ErrorCode
    message: str
