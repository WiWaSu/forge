from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

Report = Callable[[str, int, int], None]

MAX_JOBS = 20
KEEP_SECONDS = 1800


class Job:

    def __init__(self, job_id: str) -> None:
        self.id = job_id
        self._lock = threading.Lock()
        self._stage = "Расчёт поковки и заготовки"
        self._done = 0
        self._total = 0
        self._started = time.monotonic()
        self._finished: Optional[float] = None
        self._cancelled = False
        self._requests: list[dict[str, Any]] = []

    def request_begin(self) -> None:
        with self._lock:
            attempt = 1 + sum(r["stage"] == self._stage for r in self._requests)
            self._requests.append({"stage": self._stage, "attempt": attempt,
                                   "start": time.monotonic() - self._started, "end": None, "status": "идёт"})

    def request_end(self, status: str) -> None:
        with self._lock:
            if self._requests and self._requests[-1]["end"] is None:
                self._requests[-1].update(end=time.monotonic() - self._started, status=status)

    def report(self, stage: str, done: int, total: int) -> None:
        with self._lock:
            self._stage, self._done, self._total = stage, done, total

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def finish(self) -> None:
        with self._lock:
            if self._finished is None:
                self._finished = time.monotonic()

    @property
    def age(self) -> float:
        with self._lock:
            if self._finished is None:
                return 0.0
            return time.monotonic() - self._finished

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            end = self._finished if self._finished is not None else time.monotonic()
            now = time.monotonic() - self._started
            requests = []
            for i, r in enumerate(self._requests):
                retried = any(x["stage"] == r["stage"] for x in self._requests[i + 1:])
                requests.append({"stage": r["stage"], "attempt": r["attempt"], "start": round(r["start"], 1),
                                 "sec": round((r["end"] if r["end"] is not None else now) - r["start"], 1),
                                 "status": "брак" if r["status"] == "ок" and retried else r["status"]})
            return {
                "job": self.id,
                "stage": self._stage,
                "done": self._done,
                "total": self._total,
                "finished": self._finished is not None,
                "cancelled": self._cancelled,
                "elapsed_sec": round(end - self._started, 1),
                "requests": requests,
            }


_lock = threading.Lock()
_jobs: dict[str, Job] = {}


def start(job_id: str) -> Job:
    job = Job(job_id)
    with _lock:
        _jobs[job_id] = job
        _cleanup()
    return job


def get(job_id: str) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id)


def cancel(job_id: str) -> bool:
    job = get(job_id)
    if job is None:
        return False
    job.cancel()
    return True


def count() -> int:
    with _lock:
        return len(_jobs)


def clear() -> None:
    with _lock:
        _jobs.clear()


def _cleanup() -> None:
    for job_id, job in list(_jobs.items()):
        if job.age > KEEP_SECONDS:
            _jobs.pop(job_id, None)
    while len(_jobs) > MAX_JOBS:
        _jobs.pop(next(iter(_jobs)))
