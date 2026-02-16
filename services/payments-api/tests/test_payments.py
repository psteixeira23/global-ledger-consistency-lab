from __future__ import annotations

import os
import subprocess

from fastapi.testclient import TestClient
from sqlalchemy import select

from payments_api.db.session import get_session_factory
from payments_api.main import create_app
from shared.contracts.messages import DomainMessage
from shared.contracts.models import ErrorCode, OutboxEventType, OutboxStatus, PaymentStatus
from shared.db import AccountORM, IdempotencyKeyORM, LedgerEntryORM, OutboxEventORM, PaymentORM


def test_health() -> None:
    app = create_app()
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_strong_mode_success_flow() -> None:
    os.environ["CONSISTENCY_MODE"] = "strong"
    app = create_app()
    client = TestClient(app)
    payload = {
        "idempotency_key": "idem-strong-0001",
        "source_account_id": "acc-001",
        "destination_account_id": "acc-002",
        "amount_cents": 300,
        "method": "pix",
    }
    response = client.post("/v1/payments", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == PaymentStatus.COMPLETED.value

    session = get_session_factory()()
    try:
        source = session.scalar(select(AccountORM).where(AccountORM.id == "acc-001"))
        destination = session.scalar(select(AccountORM).where(AccountORM.id == "acc-002"))
        payment = session.scalar(select(PaymentORM).where(PaymentORM.id == response.json()["payment_id"]))
        outbox_events = list(session.scalars(select(OutboxEventORM)))
        ledger_entries = list(session.scalars(select(LedgerEntryORM)))
        assert source is not None and destination is not None and payment is not None
        assert source.available_balance_cents == 700
        assert destination.available_balance_cents == 1_300
        assert payment.status == PaymentStatus.COMPLETED.value
        assert len(outbox_events) == 0
        assert len(ledger_entries) == 2
    finally:
        session.close()


def test_hybrid_mode_reservation_flow() -> None:
    os.environ["CONSISTENCY_MODE"] = "hybrid"
    app = create_app()
    client = TestClient(app)
    payload = {
        "idempotency_key": "idem-hybrid-0001",
        "source_account_id": "acc-001",
        "destination_account_id": "acc-002",
        "amount_cents": 250,
        "method": "pix",
    }
    response = client.post("/v1/payments", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == PaymentStatus.RESERVED.value

    session = get_session_factory()()
    try:
        source = session.scalar(select(AccountORM).where(AccountORM.id == "acc-001"))
        payment = session.scalar(select(PaymentORM).where(PaymentORM.id == response.json()["payment_id"]))
        outbox_event = session.scalar(select(OutboxEventORM))
        assert source is not None and payment is not None and outbox_event is not None
        assert source.available_balance_cents == 750
        assert source.reserved_balance_cents == 250
        assert payment.status == PaymentStatus.RESERVED.value
        assert outbox_event.event_type == OutboxEventType.PAYMENT_RESERVED.value
    finally:
        session.close()


def test_eventual_mode_rejection_due_to_funds() -> None:
    os.environ["CONSISTENCY_MODE"] = "eventual"
    app = create_app()
    client = TestClient(app)

    session = get_session_factory()()
    try:
        with session.begin():
            source = session.scalar(select(AccountORM).where(AccountORM.id == "acc-001"))
            assert source is not None
            source.available_balance_cents = 100
    finally:
        session.close()

    payload = {
        "idempotency_key": "idem-eventual-0001",
        "source_account_id": "acc-001",
        "destination_account_id": "acc-002",
        "amount_cents": 300,
        "method": "pix",
    }
    response = client.post("/v1/payments", json=payload)
    assert response.status_code == 200
    assert response.json()["status"] == PaymentStatus.RECEIVED.value

    env = dict(os.environ)
    command = [
        "poetry",
        "run",
        "python",
        "-c",
        "from ledger_worker.main import process_outbox_once; from ledger_worker.core.config import load_settings; print(process_outbox_once(load_settings()))",
    ]
    completed = subprocess.run(
        command,
        cwd="../ledger-worker",
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip().endswith("1")

    session = get_session_factory()()
    try:
        payment = session.scalar(select(PaymentORM).where(PaymentORM.id == response.json()["payment_id"]))
        outbox_event = session.scalar(select(OutboxEventORM))
        assert payment is not None and outbox_event is not None
        assert payment.status == PaymentStatus.REJECTED.value
        assert outbox_event.status == OutboxStatus.PROCESSED.value
    finally:
        session.close()


def test_idempotency_hit_returns_stored_response() -> None:
    os.environ["CONSISTENCY_MODE"] = "hybrid"
    app = create_app()
    client = TestClient(app)
    payload = {
        "idempotency_key": "idem-hit-0001",
        "source_account_id": "acc-001",
        "destination_account_id": "acc-002",
        "amount_cents": 110,
        "method": "pix",
    }
    first = client.post("/v1/payments", json=payload)
    second = client.post("/v1/payments", json=payload)
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()

    session = get_session_factory()()
    try:
        keys = list(session.scalars(select(IdempotencyKeyORM)))
        assert len(keys) == 1
    finally:
        session.close()


def test_idempotency_conflict_returns_409() -> None:
    os.environ["CONSISTENCY_MODE"] = "hybrid"
    app = create_app()
    client = TestClient(app)
    payload = {
        "idempotency_key": "idem-conflict-0001",
        "source_account_id": "acc-001",
        "destination_account_id": "acc-002",
        "amount_cents": 100,
        "method": "pix",
    }
    first = client.post("/v1/payments", json=payload)
    assert first.status_code == 200

    changed = dict(payload)
    changed["amount_cents"] = 101
    conflict = client.post("/v1/payments", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["error_code"] == ErrorCode.IDEMPOTENCY_CONFLICT.value
    assert conflict.json()["message"] == DomainMessage.IDEMPOTENCY_CONFLICT.value


def test_internal_stats_after_strong_payment() -> None:
    os.environ["CONSISTENCY_MODE"] = "strong"
    app = create_app()
    client = TestClient(app)
    payload = {
        "idempotency_key": "idem-stats-strong-0001",
        "source_account_id": "acc-001",
        "destination_account_id": "acc-002",
        "amount_cents": 90,
        "method": "pix",
    }
    response = client.post("/v1/payments", json=payload)
    assert response.status_code == 200
    stats = client.get("/internal/stats")
    assert stats.status_code == 200
    payload_stats = stats.json()
    assert payload_stats["completed"] == 1
    assert payload_stats["rejected"] == 0
    assert payload_stats["outbox_pending"] == 0
    assert payload_stats["ledger_imbalance"] == 0
    assert payload_stats["negative_balance_detected"] == 0


def test_internal_stats_for_eventual_pending_outbox_and_negative_balance() -> None:
    os.environ["CONSISTENCY_MODE"] = "eventual"
    app = create_app()
    client = TestClient(app)
    payload = {
        "idempotency_key": "idem-stats-eventual-0001",
        "source_account_id": "acc-001",
        "destination_account_id": "acc-002",
        "amount_cents": 40,
        "method": "pix",
    }
    response = client.post("/v1/payments", json=payload)
    assert response.status_code == 200
    first_stats = client.get("/internal/stats").json()
    assert first_stats["completed"] == 0
    assert first_stats["rejected"] == 0
    assert first_stats["outbox_pending"] == 1
    assert first_stats["ledger_imbalance"] == 0

    session = get_session_factory()()
    try:
        with session.begin():
            source = session.scalar(select(AccountORM).where(AccountORM.id == "acc-001"))
            assert source is not None
            source.available_balance_cents = -1
    finally:
        session.close()

    second_stats = client.get("/internal/stats").json()
    assert second_stats["negative_balance_detected"] == 1
