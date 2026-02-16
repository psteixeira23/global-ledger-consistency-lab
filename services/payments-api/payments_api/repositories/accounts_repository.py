from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from shared.db import AccountORM


class AccountsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_for_update(self, account_id: str) -> AccountORM | None:
        statement: Select[tuple[AccountORM]] = select(AccountORM).where(AccountORM.id == account_id)
        dialect = self.session.bind.dialect.name if self.session.bind is not None else ""
        if dialect != "sqlite":
            statement = statement.with_for_update()
        return self.session.scalar(statement)
