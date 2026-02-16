from __future__ import annotations

from prometheus_client import Counter, start_http_server

PAYMENTS_PROCESSED = Counter("payments_processed_total", "Payments processed by worker")
OPTIMISTIC_LOCK_CONFLICT = Counter(
    "optimistic_lock_conflict_total", "Optimistic lock conflicts detected by worker"
)
LEDGER_IMBALANCE = Counter("ledger_imbalance_total", "Detected debit-credit imbalance")
OUTBOX_RETRY = Counter("outbox_retry_total", "Outbox retries due to transient failures")
NEGATIVE_BALANCE_DETECTED = Counter(
    "negative_balance_detected_total", "Detected negative available/reserved balance"
)
INVARIANT_VIOLATION = Counter("invariant_violation_total", "Detected invariant violations")


def start_metrics_server(port: int) -> None:
    start_http_server(port)
