from __future__ import annotations

from dataclasses import dataclass

from shared.contracts.models import ErrorCode


@dataclass
class WorkerError(Exception):
    error_code: ErrorCode
    message: str
