from __future__ import annotations

from hashlib import sha256
import time
from dataclasses import dataclass

from shared.contracts.messages import WorkerMessage


@dataclass(frozen=True)
class FailurePreset:
    db_delay_probability: float
    worker_exception_probability: float
    redis_failure_probability: float


PRESETS: dict[str, FailurePreset] = {
    "none": FailurePreset(0.0, 0.0, 0.0),
    "mild": FailurePreset(0.02, 0.01, 0.0),
    "harsh": FailurePreset(0.10, 0.05, 0.05),
}


class FailureInjector:
    def __init__(self, profile: str, seed: int) -> None:
        if profile not in PRESETS:
            raise ValueError(f"{WorkerMessage.INVALID_FAIL_PROFILE.value}: {profile}")
        self.profile = profile
        self.seed = seed
        self.preset = PRESETS[profile]

    def maybe_apply_db_delay(self, event_id: str, attempt: int) -> None:
        if self._score("db_delay", event_id, attempt) < self.preset.db_delay_probability:
            time.sleep(0.02)

    def should_raise_worker_exception(self, event_id: str, attempt: int) -> bool:
        score = self._score("worker_exception", event_id, attempt)
        return score < self.preset.worker_exception_probability

    def should_fail_redis_simulation(self, event_id: str, attempt: int) -> bool:
        score = self._score("redis_failure", event_id, attempt)
        return score < self.preset.redis_failure_probability

    def _score(self, namespace: str, event_id: str, attempt: int) -> float:
        payload = f"{self.seed}:{self.profile}:{namespace}:{event_id}:{attempt}".encode("utf-8")
        digest = sha256(payload).digest()
        value = int.from_bytes(digest[:8], byteorder="big")
        return value / float(2**64)
