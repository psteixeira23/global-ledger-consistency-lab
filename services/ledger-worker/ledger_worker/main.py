from __future__ import annotations

import asyncio
import os
import time

from ledger_worker.core.config import Settings, load_settings
from ledger_worker.db.session import get_session_factory
from ledger_worker.services.failure_injector import FailureInjector
from ledger_worker.services.processor import WorkerProcessor
from ledger_worker.services.reconciliation import ReconciliationService
from ledger_worker.telemetry.metrics import start_metrics_server
from ledger_worker.telemetry.otel import configure_otel


def build_processor(settings: Settings) -> WorkerProcessor:
    session_factory = get_session_factory()
    injector = FailureInjector(settings.fail_profile, settings.experiment_seed)
    return WorkerProcessor(
        session_factory=session_factory,
        mode=settings.consistency_mode,
        failure_injector=injector,
        processing_timeout_seconds=settings.processing_timeout_seconds,
    )


def process_outbox_once(settings: Settings | None = None) -> int:
    active_settings = settings or load_settings()
    processor = build_processor(active_settings)
    return processor.process_available_events()


async def run_loop(settings: Settings | None = None) -> None:
    active_settings = settings or load_settings()
    processor = build_processor(active_settings)
    reconciliation = ReconciliationService(get_session_factory())
    last_reconciliation = time.monotonic()
    while True:
        processor.process_available_events()
        now = time.monotonic()
        if now - last_reconciliation >= active_settings.reconciliation_interval_seconds:
            reconciliation.run_once()
            last_reconciliation = now
        await asyncio.sleep(active_settings.poll_interval_seconds)


def main() -> None:
    settings = load_settings()
    port = int(os.getenv("LEDGER_WORKER_METRICS_PORT", "8001"))
    configure_otel(service_name=os.getenv("LEDGER_WORKER_OTEL_SERVICE_NAME", "ledger-worker"))
    start_metrics_server(port)
    asyncio.run(run_loop(settings))


if __name__ == "__main__":
    main()
