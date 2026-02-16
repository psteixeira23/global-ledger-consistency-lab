from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from payments_api.api.dependencies import db_session, get_settings
from payments_api.core.config import Settings
from payments_api.core.errors import DomainError
from payments_api.telemetry.metrics import PAYMENTS_RECEIVED, REQUEST_LATENCY_MS
from payments_api.use_cases.create_payment import CreatePaymentUseCase
from shared.contracts.models import ApiErrorResponse, CreatePaymentRequest, PaymentResponse

router = APIRouter(prefix="/v1", tags=["payments"])


@router.post(
    "/payments",
    response_model=PaymentResponse,
    responses={409: {"model": ApiErrorResponse}, 422: {"model": ApiErrorResponse}, 503: {"model": ApiErrorResponse}},
)
def create_payment(
    request_body: CreatePaymentRequest,
    request: Request,
    session: Session = Depends(db_session),
    settings: Settings = Depends(get_settings),
) -> PaymentResponse | JSONResponse:
    PAYMENTS_RECEIVED.inc()
    started = time.perf_counter()
    use_case = CreatePaymentUseCase(session=session, mode=settings.consistency_mode)
    try:
        return use_case.execute(request_body, request.headers.get("traceparent"))
    except DomainError as exc:
        payload = ApiErrorResponse(error_code=exc.error_code, message=exc.message).model_dump()
        return JSONResponse(status_code=exc.http_status, content=payload)
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        REQUEST_LATENCY_MS.observe(elapsed_ms)
