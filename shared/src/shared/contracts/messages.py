from __future__ import annotations

from enum import Enum


class DomainMessage(str, Enum):
    SOURCE_DESTINATION_MUST_DIFFER = "source and destination must differ"
    IDEMPOTENCY_CONFLICT = "idempotency key reused with different payload"
    IDEMPOTENCY_IN_PROGRESS = "idempotency key is being processed"
    IDEMPOTENCY_RACE = "idempotency persistence race"
    DATABASE_UNAVAILABLE = "database unavailable"
    ACCOUNT_NOT_FOUND = "account not found"
    INSUFFICIENT_FUNDS = "insufficient funds"


class WorkerMessage(str, Enum):
    INVALID_FAIL_PROFILE = "invalid FAIL_PROFILE"
    DETERMINISTIC_WORKER_FAILURE = "deterministic worker failure"
    DETERMINISTIC_REDIS_FAILURE = "deterministic redis failure simulation"
    PAYMENT_NOT_FOUND = "payment not found"
    RESERVED_FUNDS_BELOW_AMOUNT = "reserved funds below amount"
    ACCOUNT_NOT_FOUND = "account not found"
    UNEXPECTED_EVENT = "unexpected event"
    INVALID_PAYLOAD_FIELD = "invalid payload field"
