from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from payments_api.api.dependencies import db_session
from payments_api.repositories.ledger_repository import LedgerRepository
from payments_api.repositories.outbox_repository import OutboxRepository
from payments_api.repositories.payments_repository import PaymentsRepository
from shared.contracts.models import PaymentStatus

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/stats")
def stats(session: Session = Depends(db_session)) -> dict[str, int]:
    payments = PaymentsRepository(session)
    outbox = OutboxRepository(session)
    ledger = LedgerRepository(session)
    return {
        "completed": payments.count_by_status(PaymentStatus.COMPLETED.value),
        "rejected": payments.count_by_status(PaymentStatus.REJECTED.value),
        "outbox_pending": outbox.pending_count(),
        "outbox_dead": outbox.dead_count(),
        "ledger_imbalance": ledger.imbalance_sum(),
        "negative_balance_detected": int(ledger.has_negative_balances()),
    }
