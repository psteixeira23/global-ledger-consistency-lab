from __future__ import annotations

from fastapi import FastAPI
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

PAYMENTS_RECEIVED = Counter("payments_received_total", "Payments received by API")
PAYMENTS_PROCESSED = Counter("payments_processed_total", "Payments successfully processed")
IDEMPOTENCY_REPLAY = Counter("idempotency_replay_total", "Idempotency replay events")
OPTIMISTIC_LOCK_CONFLICT = Counter(
    "optimistic_lock_conflict_total", "Optimistic lock conflicts detected"
)
REQUEST_LATENCY_MS = Histogram(
    "payments_request_latency_ms",
    "Latency of payment endpoint in milliseconds",
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2000),
)


def mount_metrics_endpoint(app: FastAPI) -> None:
    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
