from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from ledger_worker.repositories.domain_repository import DomainRepository
from ledger_worker.telemetry.metrics import LEDGER_IMBALANCE, NEGATIVE_BALANCE_DETECTED


class ReconciliationService:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self.session_factory = session_factory

    def run_once(self) -> dict[str, int]:
        session = self.session_factory()
        try:
            with session.begin():
                repository = DomainRepository(session)
                imbalance = repository.ledger_imbalance()
                negative_count = repository.negative_balance_count()
            if imbalance != 0:
                LEDGER_IMBALANCE.inc()
            if negative_count > 0:
                NEGATIVE_BALANCE_DETECTED.inc()
            return {"imbalance": imbalance, "negative_count": negative_count}
        finally:
            session.close()
