from __future__ import annotations

import os

from fastapi import FastAPI

from payments_api.api.routes_health import router as health_router
from payments_api.api.routes_internal import router as internal_router
from payments_api.api.routes_payments import router as payments_router
from payments_api.telemetry.metrics import mount_metrics_endpoint
from payments_api.telemetry.otel import configure_otel, instrument_fastapi


def create_app() -> FastAPI:
    app = FastAPI(title="payments-api", version="0.1.0")
    app.include_router(health_router, tags=["health"])
    mount_metrics_endpoint(app)

    configure_otel(service_name=os.getenv("PAYMENTS_API_OTEL_SERVICE_NAME", "payments-api"))
    instrument_fastapi(app)

    app.include_router(payments_router)
    app.include_router(internal_router)
    return app


app = create_app()
