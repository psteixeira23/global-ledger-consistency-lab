from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.db import IdempotencyKeyORM


class IdempotencyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, key: str) -> IdempotencyKeyORM | None:
        return self.session.scalar(select(IdempotencyKeyORM).where(IdempotencyKeyORM.key == key))

    def save(self, key: str, request_hash: str, response_payload_json: str) -> None:
        self.session.add(
            IdempotencyKeyORM(key=key, request_hash=request_hash, response_payload_json=response_payload_json)
        )
