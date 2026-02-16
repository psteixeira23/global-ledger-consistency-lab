from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import os
import sys

import pytest
from sqlalchemy import select

from ledger_worker import main as worker_main
from ledger_worker.core.config import Settings, load_settings
from ledger_worker.db.session import get_session_factory
from ledger_worker.main import process_outbox_once
from ledger_worker.services.failure_injector import FailureInjector
from ledger_worker.services.processor import WorkerProcessor
from ledger_worker.services.reconciliation import ReconciliationService
from ledger_worker.telemetry import metrics as worker_metrics
from ledger_worker.telemetry import otel as worker_otel
from shared.contracts.messages import WorkerMessage
from shared.contracts.models import (
    ConsistencyMode,
    LedgerDirection,
    OutboxEventType,
    OutboxStatus,
    PaymentStatus,
)
from shared.db import AccountORM, LedgerEntryORM, OutboxEventORM, PaymentORM


def _insert_payment_with_event(
    payment_status: str,
    event_type: str,
    amount_cents: int,
    suffix: str = "001",
    source_id: str = "acc-001",
    destination_id: str = "acc-002",
    traceparent: str | None = None,
) -> str:
    payment_id = f"pay-test-{suffix}"
    payload = {
        "payment_id": payment_id,
        "source_account_id": source_id,
        "destination_account_id": destination_id,
        "amount_cents": amount_cents,
        "traceparent": traceparent,
    }
    session = get_session_factory()()
    try:
        with session.begin():
            session.add(
                PaymentORM(
                    id=payment_id,
                    idempotency_key=f"idem-test-{suffix}",
                    request_hash=f"hash-{suffix}",
                    source_account_id=source_id,
                    destination_account_id=destination_id,
                    amount_cents=amount_cents,
                    method="pix",
                    status=payment_status,
                )
            )
            session.add(
                OutboxEventORM(
                    id=f"evt-test-{suffix}",
                    aggregate_type="payment",
                    aggregate_id=payment_id,
                    event_type=event_type,
                    payload_json=json.dumps(payload, sort_keys=True),
                    status=OutboxStatus.PENDING.value,
                    attempts=0,
                )
            )
    finally:
        session.close()
    return payment_id


def test_hybrid_finalization() -> None:
    os.environ["CONSISTENCY_MODE"] = "hybrid"
    session = get_session_factory()()
    try:
        with session.begin():
            source = session.scalar(select(AccountORM).where(AccountORM.id == "acc-001"))
            assert source is not None
            source.available_balance_cents = 700
            source.reserved_balance_cents = 300
    finally:
        session.close()
    payment_id = _insert_payment_with_event(
        PaymentStatus.RESERVED.value,
        OutboxEventType.PAYMENT_RESERVED.value,
        300,
    )
    processed = process_outbox_once(load_settings())
    assert processed == 1

    session = get_session_factory()()
    try:
        source = session.scalar(select(AccountORM).where(AccountORM.id == "acc-001"))
        destination = session.scalar(select(AccountORM).where(AccountORM.id == "acc-002"))
        payment = session.scalar(select(PaymentORM).where(PaymentORM.id == payment_id))
        event = session.scalar(select(OutboxEventORM))
        entries = list(session.scalars(select(LedgerEntryORM)))
        assert source is not None and destination is not None and payment is not None and event is not None
        assert source.reserved_balance_cents == 0
        assert destination.available_balance_cents == 1_300
        assert payment.status == PaymentStatus.COMPLETED.value
        assert event.status == OutboxStatus.PROCESSED.value
        assert len(entries) == 2
    finally:
        session.close()


def test_hybrid_event_payment_missing_becomes_dead() -> None:
    os.environ["CONSISTENCY_MODE"] = "hybrid"
    session = get_session_factory()()
    try:
        with session.begin():
            payload = {
                "payment_id": "pay-missing",
                "source_account_id": "acc-001",
                "destination_account_id": "acc-002",
                "amount_cents": 100,
                "traceparent": None,
            }
            session.add(
                OutboxEventORM(
                    id="evt-missing-payment",
                    aggregate_type="payment",
                    aggregate_id="pay-missing",
                    event_type=OutboxEventType.PAYMENT_RESERVED.value,
                    payload_json=json.dumps(payload),
                    status=OutboxStatus.PENDING.value,
                    attempts=0,
                )
            )
    finally:
        session.close()
    assert process_outbox_once(load_settings()) == 1
    session = get_session_factory()()
    try:
        event = session.scalar(select(OutboxEventORM).where(OutboxEventORM.id == "evt-missing-payment"))
        assert event is not None
        assert event.status == OutboxStatus.DEAD.value
    finally:
        session.close()


def test_eventual_mode_full_flow() -> None:
    os.environ["CONSISTENCY_MODE"] = "eventual"
    payment_id = _insert_payment_with_event(
        PaymentStatus.RECEIVED.value,
        OutboxEventType.PAYMENT_REQUESTED.value,
        200,
        traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00",
    )
    processed = process_outbox_once(load_settings())
    assert processed == 1

    session = get_session_factory()()
    try:
        source = session.scalar(select(AccountORM).where(AccountORM.id == "acc-001"))
        destination = session.scalar(select(AccountORM).where(AccountORM.id == "acc-002"))
        payment = session.scalar(select(PaymentORM).where(PaymentORM.id == payment_id))
        entries = list(session.scalars(select(LedgerEntryORM)))
        assert source is not None and destination is not None and payment is not None
        assert source.available_balance_cents == 800
        assert destination.available_balance_cents == 1_200
        assert payment.status == PaymentStatus.COMPLETED.value
        assert len(entries) == 2
    finally:
        session.close()


def test_eventual_rejection_due_to_funds() -> None:
    os.environ["CONSISTENCY_MODE"] = "eventual"
    _insert_payment_with_event(
        PaymentStatus.RECEIVED.value,
        OutboxEventType.PAYMENT_REQUESTED.value,
        10_000,
        suffix="reject",
    )
    processed = process_outbox_once(load_settings())
    assert processed == 1

    session = get_session_factory()()
    try:
        payment = session.scalar(select(PaymentORM).where(PaymentORM.id == "pay-test-reject"))
        assert payment is not None
        assert payment.status == PaymentStatus.REJECTED.value
    finally:
        session.close()


def test_strong_mode_marks_outbox_as_processed() -> None:
    os.environ["CONSISTENCY_MODE"] = "strong"
    _insert_payment_with_event(
        PaymentStatus.COMPLETED.value,
        OutboxEventType.PAYMENT_RESERVED.value,
        10,
        suffix="strong",
    )
    processed = process_outbox_once(load_settings())
    assert processed == 1
    session = get_session_factory()()
    try:
        event = session.scalar(select(OutboxEventORM).where(OutboxEventORM.id == "evt-test-strong"))
        assert event is not None
        assert event.status == OutboxStatus.PROCESSED.value
    finally:
        session.close()


def test_unexpected_event_type_moves_to_dead() -> None:
    os.environ["CONSISTENCY_MODE"] = "eventual"
    _insert_payment_with_event(
        PaymentStatus.RESERVED.value,
        OutboxEventType.PAYMENT_RESERVED.value,
        100,
        suffix="unexpected",
    )
    processed = process_outbox_once(load_settings())
    assert processed == 1
    session = get_session_factory()()
    try:
        event = session.scalar(select(OutboxEventORM).where(OutboxEventORM.id == "evt-test-unexpected"))
        assert event is not None
        assert event.status == OutboxStatus.DEAD.value
    finally:
        session.close()


def test_retry_scheduling_and_dead_after_seven_attempts() -> None:
    class AlwaysFailInjector:
        def maybe_apply_db_delay(self, event_id: str, attempt: int) -> None:
            del event_id, attempt
            return None

        def should_raise_worker_exception(self, event_id: str, attempt: int) -> bool:
            del event_id, attempt
            return True

        def should_fail_redis_simulation(self, event_id: str, attempt: int) -> bool:
            del event_id, attempt
            return False

    os.environ["CONSISTENCY_MODE"] = "eventual"
    _insert_payment_with_event(
        PaymentStatus.RECEIVED.value,
        OutboxEventType.PAYMENT_REQUESTED.value,
        100,
        suffix="retry",
    )
    processor = WorkerProcessor(get_session_factory(), load_settings().consistency_mode, AlwaysFailInjector())
    assert processor.process_available_events() == 1

    session = get_session_factory()()
    try:
        event = session.scalar(select(OutboxEventORM).where(OutboxEventORM.id == "evt-test-retry"))
        assert event is not None
        assert event.status == OutboxStatus.PENDING.value
        assert event.attempts == 1
        assert event.next_retry_at is not None
        event.attempts = 6
        event.status = OutboxStatus.PENDING.value
        event.next_retry_at = None
        session.commit()
    finally:
        session.close()

    assert processor.process_available_events() == 1
    session = get_session_factory()()
    try:
        dead_event = session.scalar(select(OutboxEventORM).where(OutboxEventORM.id == "evt-test-retry"))
        assert dead_event is not None
        assert dead_event.status == OutboxStatus.DEAD.value
        assert dead_event.attempts == 7
    finally:
        session.close()


def test_failure_injector_redis_path_is_handled_as_retry() -> None:
    class RedisFailInjector:
        def maybe_apply_db_delay(self, event_id: str, attempt: int) -> None:
            del event_id, attempt
            return None

        def should_raise_worker_exception(self, event_id: str, attempt: int) -> bool:
            del event_id, attempt
            return False

        def should_fail_redis_simulation(self, event_id: str, attempt: int) -> bool:
            del event_id, attempt
            return True

    os.environ["CONSISTENCY_MODE"] = "eventual"
    _insert_payment_with_event(
        PaymentStatus.RECEIVED.value,
        OutboxEventType.PAYMENT_REQUESTED.value,
        100,
        suffix="redis",
    )
    processor = WorkerProcessor(get_session_factory(), load_settings().consistency_mode, RedisFailInjector())
    assert processor.process_available_events() == 1
    session = get_session_factory()()
    try:
        event = session.scalar(select(OutboxEventORM).where(OutboxEventORM.id == "evt-test-redis"))
        assert event is not None
        assert event.status in [OutboxStatus.PENDING.value, OutboxStatus.DEAD.value]
    finally:
        session.close()


def test_failure_injector_is_deterministic_per_event_and_attempt() -> None:
    injector_a = FailureInjector("harsh", 42)
    injector_b = FailureInjector("harsh", 42)
    pairs = [("evt-a", 1), ("evt-a", 2), ("evt-b", 1), ("evt-b", 2)]
    decisions_a = [
        (
            injector_a.should_raise_worker_exception(event_id, attempt),
            injector_a.should_fail_redis_simulation(event_id, attempt),
        )
        for event_id, attempt in pairs
    ]
    decisions_b = [
        (
            injector_b.should_raise_worker_exception(event_id, attempt),
            injector_b.should_fail_redis_simulation(event_id, attempt),
        )
        for event_id, attempt in reversed(pairs)
    ]
    reordered_b = list(reversed(decisions_b))
    assert decisions_a == reordered_b


def test_failure_injector_invalid_profile_message() -> None:
    with pytest.raises(ValueError) as exc_info:
        FailureInjector("invalid-profile", 42)
    assert str(exc_info.value).startswith(WorkerMessage.INVALID_FAIL_PROFILE.value)


def test_reconciliation_detects_imbalance() -> None:
    session = get_session_factory()()
    try:
        with session.begin():
            session.add(
                LedgerEntryORM(
                    id="led-imbalance-001",
                    payment_id="payment-imbalance-001",
                    account_id="acc-001",
                    direction=LedgerDirection.DEBIT.value,
                    amount_cents=123,
                )
            )
    finally:
        session.close()

    result = ReconciliationService(get_session_factory()).run_once()
    assert result["imbalance"] == 123
    assert result["negative_count"] == 0


def test_processing_event_is_recovered_after_timeout() -> None:
    os.environ["CONSISTENCY_MODE"] = "eventual"
    payment_id = _insert_payment_with_event(
        PaymentStatus.RECEIVED.value,
        OutboxEventType.PAYMENT_REQUESTED.value,
        120,
        suffix="stuck",
    )
    session = get_session_factory()()
    try:
        with session.begin():
            event = session.scalar(select(OutboxEventORM).where(OutboxEventORM.id == "evt-test-stuck"))
            assert event is not None
            event.status = OutboxStatus.PROCESSING.value
            event.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    finally:
        session.close()

    processor = WorkerProcessor(
        get_session_factory(),
        load_settings().consistency_mode,
        FailureInjector("none", 42),
        processing_timeout_seconds=0.0,
    )
    assert processor.process_available_events() == 1
    session = get_session_factory()()
    try:
        payment = session.scalar(select(PaymentORM).where(PaymentORM.id == payment_id))
        event = session.scalar(select(OutboxEventORM).where(OutboxEventORM.id == "evt-test-stuck"))
        assert payment is not None and event is not None
        assert payment.status == PaymentStatus.COMPLETED.value
        assert event.status == OutboxStatus.PROCESSED.value
    finally:
        session.close()


def test_run_loop_executes_processing_and_reconciliation(monkeypatch: pytest.MonkeyPatch) -> None:
    class StopLoop(Exception):
        pass

    calls = {"process": 0, "reconcile": 0}

    class FakeProcessor:
        def process_available_events(self) -> int:
            calls["process"] += 1
            return 0

    class FakeReconciliation:
        def __init__(self, _factory: object) -> None:
            pass

        def run_once(self) -> dict[str, int]:
            calls["reconcile"] += 1
            return {"imbalance": 0, "negative_count": 0}

    async def fake_sleep(_seconds: float) -> None:
        raise StopLoop()

    settings = Settings(
        database_url="sqlite+pysqlite:///unused.db",
        consistency_mode=ConsistencyMode.HYBRID,
        fail_profile="none",
        experiment_seed=42,
        poll_interval_seconds=0.0,
        reconciliation_interval_seconds=0.0,
        processing_timeout_seconds=30.0,
    )
    monkeypatch.setattr(worker_main, "build_processor", lambda _settings: FakeProcessor())
    monkeypatch.setattr(worker_main, "ReconciliationService", FakeReconciliation)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(StopLoop):
        asyncio.run(worker_main.run_loop(settings))
    assert calls["process"] == 1
    assert calls["reconcile"] == 1


def test_main_wires_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    settings = Settings(
        database_url="sqlite+pysqlite:///unused.db",
        consistency_mode=ConsistencyMode.HYBRID,
        fail_profile="none",
        experiment_seed=42,
        poll_interval_seconds=0.1,
        reconciliation_interval_seconds=1.0,
        processing_timeout_seconds=30.0,
    )

    def fake_run(coroutine: object) -> None:
        close = getattr(coroutine, "close", None)
        if callable(close):
            close()
        captured["asyncio_run"] = True

    monkeypatch.setattr(worker_main, "load_settings", lambda: settings)
    monkeypatch.setattr(worker_main, "configure_otel", lambda service_name: captured.setdefault("service", service_name))
    monkeypatch.setattr(worker_main, "start_metrics_server", lambda port: captured.setdefault("port", port))
    monkeypatch.setattr(asyncio, "run", fake_run)
    monkeypatch.setenv("LEDGER_WORKER_METRICS_PORT", "9001")
    monkeypatch.setenv("LEDGER_WORKER_OTEL_SERVICE_NAME", "worker-test")
    worker_main.main()
    assert captured["port"] == 9001
    assert captured["service"] == "worker-test"
    assert captured["asyncio_run"] is True


def test_metrics_start_server(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, int] = {}
    monkeypatch.setattr(worker_metrics, "start_http_server", lambda port: called.setdefault("port", port))
    worker_metrics.start_metrics_server(9100)
    assert called["port"] == 9100


def test_configure_otel_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeExporter:
        def __init__(self, endpoint: str, insecure: bool) -> None:
            self.endpoint = endpoint
            self.insecure = insecure

    class FakeProcessor:
        def __init__(self, _exporter: object) -> None:
            pass

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(worker_otel, "OTLPSpanExporter", FakeExporter)
    monkeypatch.setattr(worker_otel, "BatchSpanProcessor", FakeProcessor)
    monkeypatch.setenv("DISABLE_OTEL_EXPORTER", "0")
    monkeypatch.delitem(sys.modules, "pytest", raising=False)
    worker_otel._configured = False
    worker_otel.configure_otel("ledger-worker-test")
    worker_otel.configure_otel("ledger-worker-test")
    assert worker_otel._configured is True
    worker_otel._configured = False
